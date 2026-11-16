"use client";

import { memo } from "react";
import { AlertTriangle, ArrowDownUp, Check, Undo2 } from "lucide-react";
import { changeSignature, type ChangeRef, type DocDiff, type Hunk, type Row } from "@/lib/diff-engine";
import type { Flag } from "@/lib/humanizer";
import { cn } from "@/lib/utils";

export interface DiffActions {
  accepted: Set<string>;
  activeId: string | null;
  onFocus: (id: string) => void;
  onAccept: (c: Pick<ChangeRef, "id" | "removed" | "added">) => void;
  onReject: (id: string) => void;
}

/** Shows `#` headings and list markers the way the document will read, without rewriting the text. */
function paraClass(text: string): string {
  const t = text.trimStart();
  if (/^#\s/.test(t)) return "font-display text-[26px] leading-tight";
  if (/^#{2,6}\s/.test(t)) return "font-display text-[21px] leading-snug";
  if (/^>/.test(t)) return "border-l-2 border-foreground/15 pl-3 italic text-foreground/80";
  return "";
}

function flagFor(row: Row, flags: Flag[]): Flag[] {
  const probe = (ex: string) => ex.replace(/…$/, "").trim().slice(0, 48).toLowerCase();
  return flags.filter((f) => {
    const r = probe(f.revised_excerpt || "");
    const o = probe(f.original_excerpt || "");
    if ("b" in row && r && row.b.toLowerCase().includes(r)) return true;
    if ("a" in row && o && row.a.toLowerCase().includes(o)) return true;
    return false;
  });
}

function HunkActions({ id, removed, added, a }: { id: string; removed: string; added: string; a: DiffActions }) {
  return (
    <span className="mx-0.5 inline-flex translate-y-[-1px] items-center gap-0.5 align-middle font-sans" contentEditable={false}>
      <button type="button" aria-label="Accept this change" title="Accept (A)"
        onClick={(e) => { e.stopPropagation(); a.onAccept({ id, removed, added }); }}
        className="inline-flex size-5 items-center justify-center rounded bg-[oklch(0.5_0.1_150)] text-white shadow-sm hover:brightness-110">
        <Check className="size-3" />
      </button>
      <button type="button" aria-label="Reject this change" title="Reject (R)"
        onClick={(e) => { e.stopPropagation(); a.onReject(id); }}
        className="inline-flex size-5 items-center justify-center rounded bg-card text-foreground shadow-sm ring-1 ring-border hover:bg-muted">
        <Undo2 className="size-3" />
      </button>
    </span>
  );
}

function InlineHunk({ h, a, show = "both" }: { h: Hunk; a: DiffActions; show?: "both" | "removed" | "added" }) {
  const accepted = a.accepted.has(changeSignature(h));
  const active = a.activeId === h.id;
  if (accepted) {
    if (show === "removed") return <span className="text-foreground/50">{h.removed}</span>;
    return <span data-change={h.id} className="rounded-[2px] decoration-[oklch(0.5_0.1_150)/0.5] underline decoration-2 underline-offset-4">{h.added}</span>;
  }
  return (
    <span
      data-change={h.id}
      role="button"
      tabIndex={0}
      aria-label={`Change: ${h.removed.trim() ? `“${h.removed.trim()}”` : "nothing"} to ${h.added.trim() ? `“${h.added.trim()}”` : "nothing"}`}
      onClick={() => a.onFocus(h.id)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); a.onFocus(h.id); } }}
      className={cn("cursor-pointer rounded-[3px] outline-none transition-shadow", active && "ring-2 ring-ink/50 ring-offset-1 ring-offset-paper")}
    >
      {show !== "added" && h.removed && <del className="rounded-[2px] bg-del-bg px-px text-del decoration-del/60">{h.removed}</del>}
      {show !== "removed" && h.added && <ins className="rounded-[2px] bg-add-bg px-px text-add no-underline">{h.added}</ins>}
      {active && show !== "removed" && <HunkActions id={h.id} removed={h.removed} added={h.added} a={a} />}
    </span>
  );
}

function ParaFrame({ row, flags, children, className, label, actions }: {
  row: Row; flags: Flag[]; children: React.ReactNode; className?: string; label?: React.ReactNode; actions?: React.ReactNode;
}) {
  const fl = flagFor(row, flags);
  return (
    <div className={cn("group relative", className)} data-row={row.key}>
      {fl.length > 0 && (
        <span className="absolute top-1 -left-6 text-warn" title={fl.map((f) => f.message).join("\n")} aria-label={`${fl.length} meaning check(s) for this paragraph`}>
          <AlertTriangle className="size-3.5" />
        </span>
      )}
      {label && <div className="mb-1 flex items-center gap-2 font-sans text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{label}{actions}</div>}
      {children}
    </div>
  );
}

function WholeParaActions({ id, a, removed, added }: { id: string; a: DiffActions; removed: string; added: string }) {
  if (a.accepted.has(changeSignature({ removed, added }))) return <span className="normal-case tracking-normal text-[oklch(0.5_0.1_150)]">accepted</span>;
  return <HunkActions id={id} removed={removed} added={added} a={a} />;
}

export const InlineDiff = memo(function InlineDiff({ diff, actions, flags }: { diff: DocDiff; actions: DiffActions; flags: Flag[] }) {
  return (
    <div className="space-y-4 font-serif text-[16px] leading-[1.8]">
      {diff.rows.map((r) => {
        if (r.kind === "equal") return <p key={r.key} data-row={r.key} className={cn("whitespace-pre-wrap", paraClass(r.b))}>{r.b}</p>;
        if (r.kind === "modified")
          return (
            <ParaFrame key={r.key} row={r} flags={flags}>
              <p className={cn("whitespace-pre-wrap", paraClass(r.b))}>
                {r.segs.map((s, i) => (s.kind === "same" ? <span key={i}>{s.text}</span> : <InlineHunk key={s.hunk.id} h={s.hunk} a={actions} />))}
              </p>
            </ParaFrame>
          );
        if (r.kind === "added") {
          const active = actions.activeId === r.key;
          return (
            <ParaFrame key={r.key} row={r} flags={flags} label={r.movedFrom ? <><ArrowDownUp className="size-3" /> Moved here</> : "New paragraph"}
              actions={<WholeParaActions id={r.key} a={actions} removed="" added={r.b} />}>
              <p data-change={r.key} onClick={() => actions.onFocus(r.key)}
                className={cn("whitespace-pre-wrap rounded-[3px] px-1", r.movedFrom ? "bg-[oklch(0.95_0.03_250)]" : "bg-add-bg text-add", active && "ring-2 ring-ink/50", paraClass(r.b))}>
                {r.b}
              </p>
            </ParaFrame>
          );
        }
        if (r.movedTo)
          return (
            <p key={r.key} data-row={r.key} className="flex items-center gap-1.5 font-sans text-[12px] text-muted-foreground">
              <ArrowDownUp className="size-3" /> A paragraph was moved from here: “{r.a.slice(0, 60)}{r.a.length > 60 ? "…" : ""}”
            </p>
          );
        const active = actions.activeId === r.key;
        return (
          <ParaFrame key={r.key} row={r} flags={flags} label="Removed paragraph" actions={<WholeParaActions id={r.key} a={actions} removed={r.a} added="" />}>
            <p data-change={r.key} onClick={() => actions.onFocus(r.key)}
              className={cn("whitespace-pre-wrap rounded-[3px] bg-del-bg px-1 text-del line-through decoration-del/50", active && "ring-2 ring-ink/50")}>
              {r.a}
            </p>
          </ParaFrame>
        );
      })}
    </div>
  );
});

export const SideBySideDiff = memo(function SideBySideDiff({ diff, actions, flags }: { diff: DocDiff; actions: DiffActions; flags: Flag[] }) {
  return (
    <div className="font-serif text-[15px] leading-[1.75]">
      <div className="sticky top-0 z-10 grid grid-cols-2 gap-6 border-b border-dashed border-foreground/10 bg-paper/95 pb-2 font-sans text-[11px] font-medium tracking-wide text-muted-foreground uppercase backdrop-blur">
        <span>Original</span>
        <span>Revised</span>
      </div>
      <div className="mt-3 space-y-3">
        {diff.rows.map((r) => (
          <div key={r.key} data-row={r.key} className={cn("grid grid-cols-2 gap-6 rounded-[3px]", r.kind !== "equal" && "bg-foreground/[0.018]")}>
            <div className={cn("min-w-0 whitespace-pre-wrap px-1", "a" in r && paraClass(r.a))}>
              {r.kind === "equal" && <span className="text-foreground/80">{r.a}</span>}
              {r.kind === "modified" && r.segs.map((s, i) => (s.kind === "same" ? <span key={i}>{s.text}</span> : <InlineHunk key={s.hunk.id} h={s.hunk} a={actions} show="removed" />))}
              {r.kind === "removed" && (
                <span className={cn(r.movedTo ? "text-muted-foreground" : "bg-del-bg text-del line-through decoration-del/50")}>
                  {r.movedTo && <span className="mr-1 font-sans text-[11px] uppercase">moved ↓</span>}{r.a}
                </span>
              )}
            </div>
            <ParaFrame row={r} flags={flags} className={cn("min-w-0 whitespace-pre-wrap px-1", "b" in r && paraClass(r.b))}>
              {r.kind === "equal" && <span>{r.b}</span>}
              {r.kind === "modified" && (
                <>
                  {r.segs.map((s, i) => (s.kind === "same" ? <span key={i}>{s.text}</span> : <InlineHunk key={s.hunk.id} h={s.hunk} a={actions} show="added" />))}
                  {r.sentencesChanged > 0 && (
                    <span className="ml-1 font-sans text-[10.5px] text-muted-foreground">· {r.sentencesChanged} sentence{r.sentencesChanged === 1 ? "" : "s"} rewritten</span>
                  )}
                </>
              )}
              {r.kind === "added" && (
                <span data-change={r.key} onClick={() => actions.onFocus(r.key)}
                  className={cn(r.movedFrom ? "bg-[oklch(0.95_0.03_250)]" : "bg-add-bg text-add", actions.activeId === r.key && "ring-2 ring-ink/50")}>
                  {r.movedFrom && <span className="mr-1 font-sans text-[11px] uppercase">moved here</span>}{r.b}
                  {actions.activeId === r.key && <WholeParaActions id={r.key} a={actions} removed="" added={r.b} />}
                </span>
              )}
              {r.kind === "removed" && !r.movedTo && (
                <span data-change={r.key} onClick={() => actions.onFocus(r.key)}
                  className={cn("font-sans text-[12px] text-muted-foreground", actions.activeId === r.key && "ring-2 ring-ink/50")}>
                  (removed) {actions.activeId === r.key && <WholeParaActions id={r.key} a={actions} removed={r.a} added="" />}
                </span>
              )}
            </ParaFrame>
          </div>
        ))}
      </div>
    </div>
  );
});
