"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check, ChevronLeft, ChevronRight, Copy, Download, FileText, MoreHorizontal, Redo2, RefreshCw, ScanSearch, Trash2, Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { changeSignature, diffDocuments, rebuild, type Granularity } from "@/lib/diff-engine";
import { humanizer, type HumanizeOptions } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { currentText, useHumanizer, type Version } from "@/stores/humanizer-store";
import { InlineDiff, SideBySideDiff, type DiffActions } from "./diff-views";
import { SelectionEditor } from "./selection-editor";
import { DetectorDialog } from "./detector-dialog";

type Tab = "changes" | "edit";

export function OutputPanel({ version, options }: { version: Version; options: HumanizeOptions | null }) {
  const { commit, undo, redo, markAccepted, setInput, setSettings, start, removeVersion, inputMeta } = useHumanizer();
  const [tab, setTab] = useState<Tab>("changes");
  const [layout, setLayout] = useState<"inline" | "side">("inline");
  const [granularity, setGranularity] = useState<Granularity>("word");
  const [cursor, setCursor] = useState(0);
  const [detectOpen, setDetectOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  const text = currentText(version);
  const diff = useMemo(() => diffDocuments(version.original, text, granularity), [version.original, text, granularity]);
  const accepted = useMemo(() => new Set(version.accepted), [version.accepted]);
  const pending = useMemo(() => diff.changes.filter((c) => !accepted.has(changeSignature(c))), [diff, accepted]);
  const idx = pending.length ? Math.min(cursor, pending.length - 1) : -1;
  const active = idx >= 0 ? pending[idx] : null;

  const scrollTo = useCallback((id: string) => {
    requestAnimationFrame(() => {
      const el = bodyRef.current?.querySelector(`[data-change="${CSS.escape(id)}"]`) as HTMLElement | null;
      el?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, []);

  const go = useCallback((delta: number) => {
    if (!pending.length) return;
    const next = (idx + delta + pending.length) % pending.length;
    setCursor(next);
    scrollTo(pending[next].id);
  }, [idx, pending, scrollTo]);

  const acceptOne = useCallback((c: { removed: string; added: string }) => markAccepted([changeSignature(c)]), [markAccepted]);
  const rejectOne = useCallback((id: string) => commit(rebuild(diff, new Set([id])), "action"), [commit, diff]);

  const actions: DiffActions = useMemo(() => ({
    accepted,
    activeId: active?.id ?? null,
    onFocus: (id) => {
      const i = pending.findIndex((c) => c.id === id);
      if (i >= 0) setCursor(i);
    },
    onAccept: acceptOne,
    onReject: rejectOne,
  }), [accepted, active, pending, acceptOne, rejectOne]);

  // Keyboard review: ←/→ (or J/K) move between changes, A accepts, R rejects. Ignored while typing.
  useEffect(() => {
    if (tab !== "changes") return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, [contenteditable=true], [role=menu], [role=dialog]") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); go(1); }
      else if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); go(-1); }
      else if ((e.key === "a" || e.key === "Enter") && active) { e.preventDefault(); acceptOne(active); scrollTo(pending[Math.min(idx + 1, pending.length - 1)]?.id ?? ""); }
      else if (e.key === "r" && active) { e.preventDefault(); rejectOne(active.id); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, go, active, acceptOne, rejectOne, pending, idx, scrollTo]);

  // ⌘Z / ⇧⌘Z for the result (outside text fields, where the browser's own undo applies).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z") return;
      if ((e.target as HTMLElement).closest("input, textarea")) return;
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const baseName = (inputMeta?.filename?.replace(/\.[^.]+$/, "") || "text") + " (humanized)";
  const download = async (fmt: "txt" | "md" | "docx") => {
    try {
      await humanizer.exportFile(text, fmt, baseName);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied to the clipboard");
    } catch {
      toast.error("Your browser blocked clipboard access.");
    }
  };
  const regenerate = () => {
    setInput(version.original, inputMeta);
    setSettings(version.settings);
    start();
  };
  const reviewedAll = diff.changes.length > 0 && pending.length === 0;

  return (
    <section aria-label="Result" className="hw-paper flex min-h-[560px] flex-col rounded-[3px]">
      <header className="flex flex-wrap items-center gap-2 border-b border-dashed border-foreground/10 px-4 py-2 sm:px-5">
        <Segmented ariaLabel="View" value={tab} onChange={setTab}
          options={[{ value: "changes", label: "Changes" }, { value: "edit", label: "Edit text" }]} />
        {tab === "changes" && (
          <>
            <Segmented ariaLabel="Comparison layout" size="xs" value={layout} onChange={setLayout} className="hidden sm:inline-flex"
              options={[{ value: "inline", label: "Inline" }, { value: "side", label: "Side by side" }]} />
            <Segmented ariaLabel="Granularity" size="xs" value={granularity} onChange={setGranularity}
              options={[{ value: "word", label: "Words", title: "Show word-level changes" }, { value: "sentence", label: "Sentences", title: "Show whole changed sentences" }]} />
          </>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <IconBtn label="Undo (⌘Z)" onClick={undo} disabled={version.index === 0}><Undo2 /></IconBtn>
          <IconBtn label="Redo (⇧⌘Z)" onClick={redo} disabled={version.index >= version.history.length - 1}><Redo2 /></IconBtn>
          <IconBtn label="Copy text" onClick={copy}><Copy /></IconBtn>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="sm" variant="ghost" aria-label="Download" />}><Download /> <span className="hidden sm:inline">Download</span></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuItem onClick={() => download("docx")}><FileText /> Word document (.docx)</DropdownMenuItem>
              <DropdownMenuItem onClick={() => download("md")}>Markdown (.md)</DropdownMenuItem>
              <DropdownMenuItem onClick={() => download("txt")}>Plain text (.txt)</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger render={<Button size="icon-sm" variant="ghost" aria-label="More actions" />}><MoreHorizontal /></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-60">
              {version.result && <DropdownMenuItem onClick={regenerate}><RefreshCw /> Regenerate with the same settings</DropdownMenuItem>}
              <DropdownMenuItem onClick={() => setDetectOpen(true)}><ScanSearch /> Check with AI detector…</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
              <DropdownMenuLabel className="text-[11px] font-normal text-muted-foreground">Accept / reject everything</DropdownMenuLabel>
              <DropdownMenuItem disabled={!pending.length} onClick={() => markAccepted(diff.changes.map(changeSignature))}><Check /> Accept all changes</DropdownMenuItem>
              <DropdownMenuItem disabled={!diff.changes.length} onClick={() => {
                commit(rebuild(diff, new Set(diff.changes.map((c) => c.id))), "action");
                toast("All changes rejected", { action: { label: "Undo", onClick: undo } });
              }}><Undo2 /> Reject all changes</DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => removeVersion(version.id)}><Trash2 /> Discard this version</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {tab === "changes" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-dashed border-foreground/10 px-4 py-1.5 text-[12px] text-muted-foreground sm:px-5">
          {diff.changes.length === 0 ? (
            <span>No differences from your original text.</span>
          ) : (
            <>
              <span className="tabular-nums">
                {diff.changes.length} change{diff.changes.length === 1 ? "" : "s"} · {diff.stats.paragraphsChanged} paragraph{diff.stats.paragraphsChanged === 1 ? "" : "s"} edited
                {diff.stats.moved ? ` · ${diff.stats.moved} moved` : ""} · <span className="text-add">+{diff.stats.wordsAdded}</span> / <span className="text-del">−{diff.stats.wordsRemoved}</span> words
              </span>
              {reviewedAll ? (
                <span className="font-medium text-[oklch(0.45_0.1_150)]">All changes reviewed</span>
              ) : (
                <span className="ml-auto flex items-center gap-1">
                  <span className="tabular-nums">{idx + 1} of {pending.length} to review</span>
                  <IconBtn label="Previous change (←)" onClick={() => go(-1)}><ChevronLeft /></IconBtn>
                  <IconBtn label="Next change (→)" onClick={() => go(1)}><ChevronRight /></IconBtn>
                </span>
              )}
            </>
          )}
        </div>
      )}

      <div ref={bodyRef} className="relative flex-1 overflow-y-auto px-6 py-5 pl-9 sm:px-10 sm:pl-12" style={{ maxHeight: "calc(100vh - 220px)" }}>
        {tab === "changes" ? (
          layout === "side" ? <SideBySideDiff diff={diff} actions={actions} flags={version.flags} /> : <InlineDiff diff={diff} actions={actions} flags={version.flags} />
        ) : (
          <SelectionEditor text={text} onCommit={commit} settings={version.settings} options={options} />
        )}
      </div>

      {tab === "changes" && diff.changes.length > 0 && (
        <footer className={cn("border-t border-dashed border-foreground/10 px-5 py-2 text-[11.5px] text-muted-foreground")}>
          Click a highlighted change, then <kbd className="rounded bg-muted px-1">A</kbd> to accept or <kbd className="rounded bg-muted px-1">R</kbd> to
          reject it. Rejecting puts your original wording back. Use <span className="font-medium">Edit text</span> to change anything yourself.
        </footer>
      )}
      <DetectorDialog open={detectOpen} onOpenChange={setDetectOpen} text={text} />
    </section>
  );
}

function IconBtn({ label, children, ...props }: { label: string; children: React.ReactNode } & React.ComponentProps<typeof Button>) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button size="icon-sm" variant="ghost" aria-label={label} {...props} />}>{children}</TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
