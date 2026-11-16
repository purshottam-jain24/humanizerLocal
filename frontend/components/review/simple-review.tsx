"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight, Download, Feather, FileText, Loader2, PartyPopper,
  Pencil, Redo2, Sparkles, Square, Undo2, Wand2, X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DocxPreviewPanel, PreviewPanelSkeleton } from "@/components/document-preview/docx-preview-panel";
import { AiDetectionCard, aiMarksFrom } from "@/components/detection/ai-detection";
import type { AiDetectionReport } from "@/lib/api";
import { DiffView } from "@/components/suggestions/diff-view";
import { useSystem } from "@/hooks/use-system";
import { api, postSSE } from "@/lib/api";
import { fromEditable, toEditable } from "@/lib/text";
import type { ExportReport, Suggestion } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace-store";
import { useHumanizer } from "@/stores/humanizer-store";
import type { Paragraph } from "@/lib/types";
import { BACK, FORWARD } from "@/components/motion/transitions";
import { InkProgress, Scribble, Stamp } from "@/components/human/ink";

/** Plain-language names for suggestion categories. */
const FRIENDLY: Record<string, string> = {
  spelling: "Spelling mistake",
  grammar: "Grammar",
  punctuation: "Punctuation",
  wordiness: "Shorter wording",
  redundancy: "Repeated meaning",
  repetition: "Repetition",
  terminology: "Consistent wording",
  clarity: "Clearer wording",
  awkward_wording: "Smoother wording",
  transition: "Better flow",
  tone: "Tone",
  complexity: "Long sentence",
  heading_punctuation: "Heading style",
  readability: "Easier to read",
};
const friendly = (c: string) => FRIENDLY[c] ?? c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
const EASY = new Set(["spelling", "grammar", "punctuation"]);

const QUICK_ACTIONS = [
  { action: "fix_grammar", label: "Fix mistakes" },
  { action: "improve_clarity", label: "Make it clearer" },
  { action: "concise", label: "Make it shorter" },
  { action: "academic_tone", label: "More formal" },
];

/** Body text in light Markdown, so the Humanizer keeps headings and list items as they are. */
function documentAsText(paragraphs: Paragraph[]): string {
  return paragraphs
    .filter((p) => p.part === "body" && !["empty", "toc", "object"].includes(p.kind) && p.text.trim())
    .sort((a, b) => a.order - b.order)
    .map((p) => {
      const t = p.text.trim();
      if (p.kind === "heading" || p.kind === "title" || p.probable_heading) return `${"#".repeat(Math.min(Math.max(p.heading_level ?? (p.kind === "title" ? 1 : 2), 1), 6))} ${t}`;
      if (p.kind === "list_item") return `- ${t}`;
      return t;
    })
    .join("\n\n");
}

export function SimpleReview({ docId, highlightAi = false }: { docId: string; highlightAi?: boolean }) {
  const { init, structure, suggestions, refresh, select, selectedPid, selectionSource, loading, error, history } = useWorkspace();
  const { system } = useSystem();
  const router = useRouter();
  const [idx, setIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [gen, setGen] = useState<{ text: string; genId: string | null; thinking?: boolean } | null>(null);
  const [custom, setCustom] = useState("");
  const [exporting, setExporting] = useState(false);
  const [report, setReport] = useState<ExportReport | null>(null);
  const [aiReport, setAiReport] = useState<AiDetectionReport | null>(null);
  const [aiHighlight, setAiHighlight] = useState(highlightAi);

  useEffect(() => {
    init(docId, new URLSearchParams(window.location.search).get("p"));
  }, [docId, init]);

  // Pending suggestions in reading order.
  const queue = useMemo(() => {
    if (!structure) return [];
    const order = new Map(structure.paragraphs.map((p, i) => [p.id, i]));
    return suggestions
      .filter((s) => s.status === "pending" || s.status === "incomplete")
      .sort((a, b) => (order.get(a.paragraph_id) ?? 0) - (order.get(b.paragraph_id) ?? 0) || (a.span_start ?? 0) - (b.span_start ?? 0));
  }, [suggestions, structure]);
  const current: Suggestion | undefined = queue[Math.min(idx, queue.length - 1)];
  const accepted = suggestions.filter((s) => s.status === "accepted").length;
  const para = structure?.paragraphs.find((p) => p.id === selectedPid) ?? null;
  const modelReady = !!system?.ollama.available && !!system.settings.ollama.model;

  // Keep the preview focused on the current suggestion.
  useEffect(() => {
    if (current && selectionSource !== "preview") select(current.paragraph_id, "editor");
  }, [current?.id, structure?.document.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Clicking a paragraph in the document jumps to its first suggestion.
  useEffect(() => {
    if (selectionSource !== "preview" || !selectedPid) return;
    const i = queue.findIndex((s) => s.paragraph_id === selectedPid);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (i >= 0) setIdx(i);
  }, [selectedPid, selectionSource]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>, msg?: string) => {
    setBusy(true);
    try {
      await fn();
      if (msg) toast.success(msg);
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
      await refresh(["suggestions"]);
    } finally {
      setBusy(false);
      setEditing(null);
    }
  };

  // Hand the document's current text (with accepted changes) to the Humanizer and start a rewrite there.
  const humanizeDocument = () => {
    if (!structure) return;
    const text = documentAsText(structure.paragraphs);
    if (!text.trim()) return toast.error("This document has no text to rewrite.");
    useHumanizer.getState().queueRun(text, { filename: structure.document.filename, source: "docx", warnings: [] });
    router.push("/humanize");
  };

  const acceptEasy = async () => {
    const ids = queue.filter((s) => s.status === "pending" && EASY.has(s.category) && !s.verification?.required && s.source === "rule").map((s) => s.id);
    if (!ids.length) return;
    await act(async () => {
      const r = await api.batch(docId, ids, "accept");
      toast.success(`Fixed ${r.done.length} spelling, grammar and punctuation mistake${r.done.length === 1 ? "" : "s"}`);
    });
  };

  const improve = async (action: string, instruction?: string) => {
    const pid = selectedPid ?? current?.paragraph_id;
    if (!pid) return;
    setGen({ text: "", genId: null });
    let raw = "";
    let made: Suggestion | null = null;
    try {
      await postSSE(`/api/documents/${docId}/suggestions/generate`,
        { paragraph_id: pid, action, scope: "paragraph", custom_instruction: instruction ?? null },
        (event, data) => {
          if (event === "started") setGen((g) => ({ text: g?.text ?? "", genId: data.gen_id }));
          if (event === "thinking") setGen((g) => ({ text: g?.text ?? "", genId: g?.genId ?? null, thinking: true }));
          if (event === "token") { raw += data.text; setGen((g) => ({ text: raw, genId: g?.genId ?? null })); }
          if (event === "completed") made = data.suggestion;
          if (event === "cancelled") toast("Stopped. The partial text was saved as a draft.");
          if (event === "error") toast.error(data.message);
        });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setGen(null);
      setCustom("");
      await refresh(["suggestions"]);
      const m = made as Suggestion | null;
      if (m) {
        if (m.proposed_text === m.original_text) toast("This paragraph already reads well — no changes suggested.");
        const q = useWorkspace.getState().suggestions
          .filter((s) => s.status === "pending" || s.status === "incomplete");
        const order = new Map(structure!.paragraphs.map((p, i) => [p.id, i]));
        q.sort((a, b) => (order.get(a.paragraph_id) ?? 0) - (order.get(b.paragraph_id) ?? 0) || (a.span_start ?? 0) - (b.span_start ?? 0));
        const i = q.findIndex((s) => s.id === m.id);
        if (i >= 0) setIdx(i);
      }
    }
  };

  const download = async () => {
    setExporting(true);
    try {
      const r = await api.exportDoc(docId);
      setReport(r);
      if (r.ok) window.location.href = api.downloadUrl(r.export_id);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const undo = async () => { await api.undo(docId); refresh(); };
  const redo = async () => { await api.redo(docId); refresh(); };

  if (error) {
    return (
      <div className="grid min-h-screen place-items-center p-6 text-center">
        <div>
          <p className="text-lg font-medium">This document isn&apos;t ready yet</p>
          <p className="mt-1 text-sm text-muted-foreground">{error}</p>
          <Button className="mt-4" render={<Link href={`/documents/${docId}/scan`} transitionTypes={BACK} />}>See progress</Button>
        </div>
      </div>
    );
  }

  const easyCount = queue.filter((s) => s.status === "pending" && EASY.has(s.category) && !s.verification?.required && s.source === "rule").length;

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <header style={{ viewTransitionName: "app-header" }} className="flex h-16 shrink-0 items-center gap-3 border-b border-dashed border-foreground/[0.12] bg-[oklch(0.962_0.012_80/0.72)] px-4 backdrop-blur-md">
        <Link href="/" transitionTypes={BACK} aria-label="Back to my documents" className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground">
          <ArrowLeft className="size-4" /> My documents
        </Link>
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{structure?.document.filename ?? "…"}</span>
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={undo} disabled={!history?.can_undo} title="Undo (⌘Z)"><Undo2 /> Undo</Button>
          <Button variant="ghost" size="icon-sm" onClick={redo} disabled={!history?.can_redo} aria-label="Redo"><Redo2 /></Button>
          <Button size="lg" className="ml-2" onClick={download} disabled={exporting || !structure}>
            {exporting ? <Loader2 className="animate-spin" /> : <Download />} Download Word file
          </Button>
        </div>
      </header>

      {loading || !structure ? (
        // Same layout as the loaded screen, so the document page arriving from the scan has a place to land.
        <div className="grid min-h-0 flex-1 grid-cols-[1fr_420px]">
          <div className="min-h-0 border-r border-dashed border-foreground/10"><PreviewPanelSkeleton docId={docId} /></div>
          <aside className="space-y-4 bg-sidebar px-5 py-4" aria-busy>
            <div className="h-5 w-48 rounded-md bg-muted" />
            <div className="h-1.5 rounded-full bg-muted" />
            <div className="h-56 rounded-2xl bg-muted/70" />
            <div className="h-40 rounded-2xl bg-muted/50" />
          </aside>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[1fr_420px]">
          <div className="min-h-0 border-r border-dashed border-foreground/10">
            <DocxPreviewPanel simple pendingIds={queue.map((s) => s.paragraph_id)} aiMarks={aiHighlight ? aiMarksFrom(aiReport) : null} />
          </div>

          {/* Review side — slides in beside the document */}
          <motion.aside className="flex min-h-0 flex-col bg-sidebar"
            initial={{ opacity: 0, x: 28 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.55, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}>
            <div className="border-b border-dashed border-foreground/10 px-5 pt-5 pb-4">
              <div className="flex items-baseline justify-between gap-3">
                <h1 className="font-display text-[27px] leading-none">
                  {queue.length ? <>{queue.length} note{queue.length === 1 ? "" : "s"} <span className="text-muted-foreground italic">to read</span></> : "All read"}
                </h1>
                <span className="font-hand text-[19px] leading-none text-[oklch(0.42_0.1_155)]">{accepted} kept</span>
              </div>
              <InkProgress className="mt-3" tone="text-[oklch(0.5_0.11_155)]" value={accepted / Math.max(1, accepted + queue.length)} />
              {easyCount > 1 && (
                <Button variant="outline" size="sm" className="mt-3 w-full" disabled={busy} onClick={acceptEasy}>
                  <Check /> Fix all {easyCount} simple mistakes at once
                </Button>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <AnimatePresence mode="wait">
                {current ? (
                  <motion.div key={current.id} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: 0.18 }}>
                    <SuggestionStep
                      s={current}
                      position={Math.min(idx, queue.length - 1) + 1}
                      total={queue.length}
                      busy={busy}
                      editing={editing === current.id}
                      onEdit={() => setEditing(current.id)}
                      onCancelEdit={() => setEditing(null)}
                      onAccept={(edited) => act(() => api.accept(docId, current.id, edited), edited ? "Your edit was applied" : undefined)}
                      onIgnore={() => act(() => api.reject(docId, current.id))}
                      onPrev={() => setIdx((i) => Math.max(0, Math.min(i, queue.length - 1) - 1))}
                      onNext={() => setIdx((i) => Math.min(queue.length - 1, i + 1))}
                    />
                  </motion.div>
                ) : (
                  <motion.div key="done" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="hw-card p-6 text-center">
                    <Stamp tone="green" className="text-[15px]" delay={0.2}><PartyPopper className="size-4" /> Reviewed</Stamp>
                    <p className="mt-4 font-hand text-[26px] leading-none text-ink">Nice work.</p>
                    <p className="mt-2 font-medium">You&apos;ve been through every note</p>
                    <p className="mt-1 text-sm text-muted-foreground">Download your improved Word file, or use the AI below to polish any paragraph further.</p>
                    <Button className="mt-4" onClick={download} disabled={exporting}><Download /> Download Word file</Button>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* AI improve */}
              <section className="mt-6 hw-card p-4">
                <p className="flex items-center gap-1.5 font-hand text-[22px] leading-none text-pencil"><Sparkles className="size-4 text-brand" /> Ask for a rewrite</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {para ? <>Selected: <span className="italic">“{para.text.replace(/￼/g, "").slice(0, 60)}{para.text.length > 60 ? "…" : ""}”</span></> : "Click a paragraph in your document first."}
                </p>
                {!modelReady ? (
                  <p className="mt-3 rounded-lg bg-warn-bg p-3 text-xs text-[oklch(0.42_0.1_70)]">
                    The AI assistant is off. <Link href="/settings" className="font-medium underline">Turn it on in Settings</Link> — it runs privately on your computer.
                  </p>
                ) : gen ? (
                  <div className="mt-3 rounded-lg border border-brand/25 bg-brand-soft/50 p-3">
                    <p className="mb-1 flex items-center gap-1.5 text-xs text-brand"><Loader2 className="size-3 animate-spin" /> {!gen.genId ? "Starting the AI (the first time can take a few seconds)…" : gen.text ? "Writing…" : gen.thinking ? "Thinking it through…" : "Reading the paragraph…"}</p>
                    <p className="data-[empty=true]:hidden hw-caret min-h-5 whitespace-pre-wrap font-serif text-[15px] leading-relaxed">{streamText(gen.text)}</p>
                    <Button size="sm" variant="outline" className="mt-2" onClick={() => gen.genId && api.cancelGeneration(gen.genId)}><Square /> Stop</Button>
                  </div>
                ) : (
                  <>
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      {QUICK_ACTIONS.map((a) => (
                        <Button key={a.action} variant="outline" size="sm" disabled={!para || busy} onClick={() => improve(a.action)}>
                          <Wand2 /> {a.label}
                        </Button>
                      ))}
                    </div>
                    <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) improve("custom", custom.trim()); }}>
                      <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Or say what you want, e.g. “make it friendlier”"
                        className="h-8 min-w-0 flex-1 rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/40" disabled={!para} />
                      <Button size="sm" type="submit" disabled={!para || !custom.trim()}>Go</Button>
                    </form>
                  </>
                )}
              </section>

              <Button className="mt-4 h-11 w-full gap-2 text-[15px]" disabled={!structure} onClick={humanizeDocument}
                title="Rewrite the whole document in the Humanizer. You review every change there; this document is not changed.">
                <Feather className="size-4" /> Humanize it
              </Button>

              <div className="mt-4">
                <AiDetectionCard docId={docId} highlight={aiHighlight} onHighlight={setAiHighlight} onReport={setAiReport} />
              </div>

              <p className="mt-6 text-center text-xs text-muted-foreground">
                Your original file is never changed. ·{" "}
                <Link href={`/documents/${docId}/analysis`} transitionTypes={FORWARD} className="underline hover:text-foreground">Full report</Link> ·{" "}
                <Link href={`/documents/${docId}/advanced`} transitionTypes={FORWARD} className="underline hover:text-foreground">Advanced editor</Link>
              </p>
            </div>
          </motion.aside>
        </div>
      )}

      <Dialog open={!!report} onOpenChange={(o) => !o && setReport(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{report?.ok ? "Your document is downloading" : "We couldn't create the file"}</DialogTitle>
            <DialogDescription>
              {report?.ok
                ? `${report.paragraphs_changed} paragraph${report.paragraphs_changed === 1 ? "" : "s"} changed. Your formatting was kept and the file was checked. Your original file is unchanged.`
                : report?.message}
            </DialogDescription>
          </DialogHeader>
          {report?.ok && (
            <div className="space-y-2 text-sm">
              {report.needs_verification.length > 0 && (
                <p className="flex gap-2 rounded-lg bg-warn-bg p-2.5 text-xs text-[oklch(0.42_0.1_70)]"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {report.needs_verification.length} accepted change(s) added facts or sources — please double-check them.</p>
              )}
              {report.failed.length > 0 && (
                <p className="flex gap-2 rounded-lg bg-del-bg p-2.5 text-xs text-del"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {report.failed.length} change(s) couldn&apos;t be applied safely and were left as they were.</p>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" render={<Link href={`/documents/${docId}/export`} />}>Details</Button>
                <Button render={<a href={api.downloadUrl(report.export_id)} />}><Download /> Download again</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function streamText(raw: string) {
  const m = raw.match(/<revision>([\s\S]*?)(<\/revision>|$)/);
  return (m ? m[1] : "").replace(/⟦\d+⟧/g, "◆");
}

function SuggestionStep({
  s, position, total, busy, editing, onEdit, onCancelEdit, onAccept, onIgnore, onPrev, onNext,
}: {
  s: Suggestion; position: number; total: number; busy: boolean; editing: boolean;
  onEdit: () => void; onCancelEdit: () => void; onAccept: (edited?: string) => void; onIgnore: () => void; onPrev: () => void; onNext: () => void;
}) {
  const [draft, setDraft] = useState(toEditable(s.proposed_text));
  const ctx = context(s);
  const warn = s.verification?.required || (s.meaning && s.meaning.level !== "likely_preserved") || s.status === "incomplete";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea") || busy || editing) return;
      if (e.key === "Enter" && s.status === "pending") onAccept();
      else if (e.key === "ArrowRight") onNext();
      else if (e.key === "ArrowLeft") onPrev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">Suggestion {position} of {total}</span>
        <div className="flex gap-1">
          <Button size="icon-xs" variant="ghost" onClick={onPrev} disabled={position <= 1} aria-label="Previous suggestion"><ChevronLeft /></Button>
          <Button size="icon-xs" variant="ghost" onClick={onNext} disabled={position >= total} aria-label="Next suggestion"><ChevronRight /></Button>
        </div>
      </div>
      <div className="hw-card p-5 shadow-xs">
        <p className="-mt-1 -ml-0.5 flex items-center gap-2 font-hand text-[25px] leading-none text-ink -rotate-1">
          {s.source.startsWith("llm") && <Sparkles className="size-4" />}
          {friendly(s.category)}
          <Scribble variant="underline" className="h-2 w-12 text-ink/50" strokeWidth={1.8} delay={0.15} duration={0.35} />
        </p>

        <p className="mt-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Before</p>
        <DiffView before={ctx.before} after={ctx.after} mode="before" className="text-[15px] text-muted-foreground" />
        <p className="mt-3 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">After</p>
        {editing ? (
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} className="mt-1 min-h-24 font-serif text-[15px]" autoFocus />
        ) : (
          <DiffView before={ctx.before} after={ctx.after} mode="after" className="text-[15px]" />
        )}

        {s.explanation && <p className="mt-3 text-sm text-foreground/80">{s.explanation.replace(/\s*\(No changes were needed\.\)/, "")}</p>}

        {warn && (
          <div className="mt-3 flex gap-2 rounded-lg bg-warn-bg p-2.5 text-xs text-[oklch(0.42_0.1_70)]">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <div>
              <p className="font-medium">Check this one carefully</p>
              {s.status === "incomplete" && <p>The AI was stopped before finishing — edit it or ignore it.</p>}
              {[...(s.meaning?.level !== "likely_preserved" ? s.meaning?.warnings ?? [] : []), ...(s.verification?.items ?? [])].map((w) => <p key={w}>{w}</p>)}
            </div>
          </div>
        )}

        <div className="mt-5 flex gap-2">
          {editing ? (
            <>
              <Button className="flex-1" disabled={busy} onClick={() => onAccept(fromEditable(draft))}><Check /> Use my version</Button>
              <Button variant="ghost" onClick={onCancelEdit}>Cancel</Button>
            </>
          ) : (
            <>
              <Button className="flex-1" size="lg" disabled={busy || s.status !== "pending"} onClick={() => onAccept()}><Check /> Accept</Button>
              <Button variant="outline" size="lg" disabled={busy} onClick={onIgnore}><X /> Ignore</Button>
              <Button variant="ghost" size="lg" onClick={onEdit} aria-label="Edit suggestion"><Pencil /></Button>
            </>
          )}
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted-foreground">Enter to accept · ← → to move between suggestions</p>
      {total > position && (
        <button onClick={onNext} className="mx-auto mt-1 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">Skip for now <ArrowRight className="size-3" /></button>
      )}
    </div>
  );
}

function context(s: Suggestion): { before: string; after: string } {
  if (s.scope !== "span" || s.span_start === null || s.span_end === null) return { before: s.original_text, after: s.proposed_text };
  const t = s.base_text;
  let a = s.span_start;
  while (a > 0 && !/[.!?]\s/.test(t.slice(a - 2, a)) && s.span_start - a < 160) a--;
  let b = s.span_end;
  while (b < t.length && !/[.!?]/.test(t[b - 1] ?? "") && b - s.span_end < 160) b++;
  const pre = (a > 0 ? "…" : "") + t.slice(a, s.span_start);
  const post = t.slice(s.span_end, b) + (b < t.length ? "…" : "");
  return { before: pre + s.original_text + post, after: pre + s.proposed_text + post };
}
