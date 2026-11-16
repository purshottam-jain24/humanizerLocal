"use client";

import { useState } from "react";
import { AlertTriangle, ArrowLeft, Check, Combine, PencilLine, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DiffView } from "@/components/suggestions/diff-view";
import { combineBlocks, type Choice } from "@/lib/combine";
import { humanizer, type Flag, type ResultBlock } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { currentText, useHumanizer, type Version } from "@/stores/humanizer-store";

/** Which text is used for one paragraph: a variation (by index), the original, or the user's own edit. */
type Pick = Choice;

/**
 * Compare the variations of one run paragraph by paragraph, pick the best version of each paragraph
 * (or keep the original), edit any pick, and turn the selection into a new version.
 *
 * Paragraph boundaries are the same in every variation (each one rewrites the same blocks), so a
 * combination is rebuilt exactly from block prefixes and separators.
 */
export function CompareVariations({ versions, onClose }: { versions: Version[]; onClose: () => void }) {
  const addCombined = useHumanizer((s) => s.addCombined);
  const setFlags = useHumanizer((s) => s.setFlags);
  const variants = versions.filter((v) => v.result && v.approach !== "combined");
  const base = variants[0];
  const blocks: ResultBlock[] = base?.result?.blocks ?? [];
  const [picks, setPicks] = useState<Record<number, Pick>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [showDiff, setShowDiff] = useState(true);
  const [busy, setBusy] = useState(false);

  if (!base || variants.length < 2) return null;
  const edited = variants.filter((v) => currentText(v) !== v.result!.output);

  const textOf = (bid: number, from: number | "original") =>
    from === "original" ? blocks.find((b) => b.id === bid)!.original : variants[from].result!.blocks.find((b) => b.id === bid)?.revised ?? "";
  const pickOf = (bid: number): Pick => picks[bid] ?? { from: 0 };
  const chosenText = (bid: number) => pickOf(bid).edited ?? textOf(bid, pickOf(bid).from);
  const flagsFor = (vi: number, bid: number): Flag[] => (variants[vi].result!.flags ?? []).filter((f) => f.block_id === bid);
  const rows = blocks.filter((b) => b.rewritable && b.changed !== undefined);
  const counts = variants.map((_, vi) => rows.filter((b) => pickOf(b.id).from === vi).length);

  const pickAll = (from: number | "original") => setPicks(Object.fromEntries(rows.map((b) => [b.id, { from }])));

  const create = async () => {
    const text = combineBlocks(blocks, (vi, bid) => textOf(bid, vi), pickOf);
    const used = Array.from(new Set(rows.map((b) => pickOf(b.id).from))).map((f) => (f === "original" ? "Original" : variants[f].result!.label ?? variants[f].approach));
    const id = addCombined(base, text, `Combined · ${used.join(" + ")}`);
    setBusy(true);
    try {
      const res = await humanizer.validate(base.original, text, base.result?.context.key_terms ?? []);
      if (useHumanizer.getState().activeId === id) setFlags(res.flags, text);
      toast.success(res.flags.length ? `Combined version created · ${res.flags.length} item(s) to review` : "Combined version created · no problems found by the automated checks");
    } catch (e) {
      toast.error(`Combined version created, but the meaning check failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      onClose();
    }
  };

  return (
    <section aria-label="Compare variations" className="hw-paper rounded-[3px]">
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-dashed border-foreground/10 bg-paper/95 px-4 py-2.5 backdrop-blur sm:px-5">
        <Button size="sm" variant="ghost" onClick={onClose}><ArrowLeft /> Back</Button>
        <h2 className="font-display text-[22px] leading-none">Compare &amp; combine</h2>
        <div className="ml-auto flex flex-wrap items-center gap-1.5 text-[12px]">
          <label className="mr-2 flex items-center gap-1.5 text-muted-foreground">
            <input type="checkbox" checked={showDiff} onChange={(e) => setShowDiff(e.target.checked)} className="accent-[var(--color-ink)]" />
            Show changes
          </label>
          <span className="text-muted-foreground">Use everywhere:</span>
          {variants.map((v, vi) => (
            <Button key={v.id} size="xs" variant="outline" className="bg-paper" onClick={() => pickAll(vi)}>{v.result!.label ?? v.approach}</Button>
          ))}
          <Button size="xs" variant="ghost" onClick={() => pickAll("original")}>Original</Button>
        </div>
      </header>

      <div className="grid gap-2 border-b border-dashed border-foreground/10 px-4 py-3 text-[12px] sm:px-5" style={{ gridTemplateColumns: `repeat(${variants.length}, minmax(0, 1fr))` }}>
        {variants.map((v, vi) => {
          const r = v.result!;
          const high = (r.flags ?? []).filter((f) => f.severity === "high").length;
          return (
            <div key={v.id} className="rounded-md bg-foreground/[0.03] px-3 py-2">
              <p className="font-medium">{r.label ?? v.approach}</p>
              <p className="text-muted-foreground">
                {r.metrics_after?.words?.toLocaleString() ?? "–"} words · {Math.round((r.change_ratio ?? 0) * 100)}% changed · reading ease{" "}
                {r.metrics_after?.flesch_reading_ease?.toFixed(0) ?? "–"}
              </p>
              <p className={cn(high ? "text-del" : "text-muted-foreground")}>
                {(r.flags ?? []).length ? `${r.flags.length} item(s) to review${high ? `, ${high} important` : ""}` : "No meaning problems found by the checks"}
              </p>
              <p className="mt-1 tabular-nums text-ink">{counts[vi]} of {rows.length} paragraph(s) picked</p>
            </div>
          );
        })}
      </div>
      {edited.length > 0 && (
        <p className="mx-4 mt-3 rounded bg-warn-bg/70 px-2 py-1.5 text-[12px] sm:mx-5">
          Comparison uses the variations as generated; your later edits to {edited.map((v) => v.result!.label).join(", ")} are kept in those versions but not shown here.
        </p>
      )}

      <ol className="divide-y divide-dashed divide-foreground/10">
        {blocks.map((b) => {
          if (!b.rewritable) {
            return b.kind === "heading" && b.original.trim() ? (
              <li key={b.id} className="px-4 pt-5 pb-1 font-display text-[19px] sm:px-5">{b.original}</li>
            ) : null;
          }
          const pick = pickOf(b.id);
          return (
            <li key={b.id} className="px-4 py-4 sm:px-5" data-row>
              <details className="mb-2 text-[12.5px]">
                <summary className={cn("cursor-pointer text-muted-foreground", pick.from === "original" && "font-medium text-ink")}>
                  Original{pick.from === "original" ? " (picked)" : ""}
                </summary>
                <p className="mt-1 font-serif text-[14px] leading-relaxed text-foreground/75">{b.original}</p>
                <Button size="xs" variant="ghost" className="mt-1" onClick={() => setPicks((p) => ({ ...p, [b.id]: { from: "original" } }))}>
                  <Undo2 /> Keep the original for this paragraph
                </Button>
              </details>
              <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${variants.length}, minmax(0, 1fr))` }}>
                {variants.map((v, vi) => {
                  const t = textOf(b.id, vi);
                  const selected = pick.from === vi;
                  const fl = flagsFor(vi, b.id);
                  return (
                    <button key={v.id} type="button" onClick={() => setPicks((p) => ({ ...p, [b.id]: { from: vi } }))} aria-pressed={selected}
                      className={cn("rounded-md border p-2.5 text-left transition-colors",
                        selected ? "border-ink/45 bg-ink/[0.05] shadow-[0_0_0_1px_var(--color-ink)]/10" : "border-foreground/10 hover:bg-foreground/[0.03]")}>
                      <span className="mb-1 flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
                        {selected && <Check className="size-3 text-ink" />}{v.result!.label ?? v.approach}
                        {fl.length > 0 && <span className="ml-auto inline-flex items-center gap-1 normal-case text-warn" title={fl.map((f) => f.message).join("\n")}>
                          <AlertTriangle className="size-3" />{fl.length}</span>}
                      </span>
                      {showDiff ? <DiffView before={b.original} after={t} mode="after" className="text-[14px]" />
                        : <span className="font-serif text-[14px] leading-relaxed">{t}</span>}
                    </button>
                  );
                })}
              </div>
              {editing === b.id ? (
                <div className="mt-2 space-y-1.5">
                  <Textarea autoFocus value={chosenText(b.id)} rows={4} className="font-serif text-[14.5px]"
                    onChange={(e) => setPicks((p) => ({ ...p, [b.id]: { from: pickOf(b.id).from, edited: e.target.value } }))} />
                  <div className="flex gap-1.5">
                    <Button size="xs" onClick={() => setEditing(null)}><Check /> Done</Button>
                    <Button size="xs" variant="ghost" onClick={() => { setPicks((p) => ({ ...p, [b.id]: { from: pickOf(b.id).from } })); setEditing(null); }}>Discard edit</Button>
                  </div>
                </div>
              ) : (
                <div className="mt-1.5 flex items-center gap-2 text-[12px] text-muted-foreground">
                  <Button size="xs" variant="ghost" onClick={() => setEditing(b.id)}><PencilLine /> Edit the picked text</Button>
                  {pick.edited !== undefined && <span className="text-ink">Edited</span>}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <footer className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-dashed border-foreground/10 bg-paper/95 px-4 py-3 backdrop-blur sm:px-5">
        <p className="text-[12.5px] text-muted-foreground">
          The combined text becomes a new version. It is checked against your original again, because paragraphs from different variations were written separately.
        </p>
        <Button className="ml-auto" onClick={create} disabled={busy}><Combine /> Create combined version</Button>
      </footer>
    </section>
  );
}
