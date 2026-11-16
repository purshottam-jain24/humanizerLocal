"use client";

import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { motion } from "motion/react";
import { Image as ImageIcon } from "lucide-react";
import type { VisualParagraph } from "@/lib/types";
import type { ParaVisualState, ScanEngine } from "./scan-engine";
import { cn } from "@/lib/utils";

interface Block {
  type: "para" | "table";
  key: string;
  paras: VisualParagraph[];
}

function groupBlocks(paras: VisualParagraph[]): Block[] {
  const blocks: Block[] = [];
  for (const p of paras) {
    const tid = p.table?.[0]?.table_id;
    const last = blocks[blocks.length - 1];
    if (tid && last?.type === "table" && last.key === tid) last.paras.push(p);
    else if (tid) blocks.push({ type: "table", key: tid, paras: [p] });
    else blocks.push({ type: "para", key: p.id, paras: [p] });
  }
  return blocks;
}

export function ScanPaper({ engine, page, frame, landscape }: { engine: ScanEngine; page: number; frame: number; landscape?: boolean }) {
  const paperRef = useRef<HTMLDivElement>(null);
  const beamRef = useRef<HTMLDivElement>(null);
  const sweepRef = useRef<HTMLDivElement>(null);
  const wordBoxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<{ pid: string; x: number; y: number; w: number; h: number; mode: string } | null>(null);
  const paras = useMemo(() => engine.paragraphs.filter((p) => (p.page ?? 1) === page), [engine, page, engine.paragraphs.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const blocks = useMemo(() => groupBlocks(paras), [paras]);
  const activePid = engine.activePid;
  const phase = engine.phase;
  const activeOnPage = !!activePid && paras.some((p) => p.id === activePid);

  // Position overlays from real DOM geometry of the words / paragraphs on the page.
  // Runs after every render on purpose (the parent re-renders per animation frame); setBox
  // is guarded so it only fires when the measured target actually changes.
  /* eslint-disable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */
  useLayoutEffect(() => {
    const paper = paperRef.current;
    if (!paper) return;
    const pr = paper.getBoundingClientRect();
    const beam = beamRef.current;
    const wb = wordBoxRef.current;
    const sweep = sweepRef.current;
    if (!activeOnPage || !activePid) {
      if (beam) beam.style.opacity = "0";
      if (wb) wb.style.opacity = "0";
      if (sweep) sweep.style.opacity = "0";
      if (box) setBox(null);
      return;
    }
    const pEl = paper.querySelector<HTMLElement>(`[data-pid="${activePid}"]`);
    if (!pEl) return;
    const rect = pEl.getBoundingClientRect();
    if (phase === "recognizing") {
      const wEl = pEl.querySelector<HTMLElement>(`[data-w="${Math.max(0, engine.wordIdx - 1)}"]`) ?? pEl;
      const r = wEl.getBoundingClientRect();
      if (wb) {
        wb.style.opacity = "1";
        wb.style.transform = `translate(${r.left - pr.left - 2}px, ${r.top - pr.top - 1}px)`;
        wb.style.width = `${r.width + 4}px`;
        wb.style.height = `${r.height + 2}px`;
      }
      if (beam) {
        beam.style.opacity = "1";
        beam.style.transform = `translateY(${r.bottom - pr.top}px)`;
      }
      if (sweep) {
        sweep.style.opacity = "1";
        sweep.style.transform = `translate(0px, ${r.top - pr.top - 2}px)`;
        sweep.style.height = `${r.height + 4}px`;
        sweep.style.width = `${r.right - pr.left}px`;
      }
      if (box?.mode !== "detect" || box.pid !== activePid) {
        setBox({ pid: activePid, x: rect.left - pr.left - 6, y: rect.top - pr.top - 4, w: rect.width + 12, h: rect.height + 8, mode: "detect" });
      } else if (Math.abs(box.h - (rect.height + 8)) > 1) {
        setBox({ ...box, h: rect.height + 8 });
      }
    } else {
      if (wb) wb.style.opacity = "0";
      if (sweep) sweep.style.opacity = "0";
      const mode = phase === "ai_review" ? "ai" : "analyze";
      // Beam sweeps through the paragraph sentence by sentence.
      const p = engine.byId.get(activePid);
      const s = p?.sentences[Math.min(engine.sentenceIdx, (p?.sentences.length ?? 1) - 1)];
      let y = rect.top - pr.top;
      if (p && s) {
        const wi = p.words.findIndex(([ws]) => ws >= s[0]);
        const wEl = pEl.querySelector<HTMLElement>(`[data-w="${Math.max(0, wi)}"]`);
        if (wEl) y = wEl.getBoundingClientRect().bottom - pr.top;
      }
      if (beam) {
        beam.style.opacity = "1";
        beam.style.transform = `translateY(${y}px)`;
      }
      if (!box || box.pid !== activePid || box.mode !== mode) {
        setBox({ pid: activePid, x: rect.left - pr.left - 8, y: rect.top - pr.top - 6, w: rect.width + 16, h: rect.height + 12, mode });
      }
    }
  });
  /* eslint-enable react-hooks/exhaustive-deps, react-hooks/set-state-in-effect */

  return (
    <div
      ref={paperRef}
      className={cn(
        "relative mx-auto rounded-[2px] bg-paper text-foreground shadow-[0_1px_2px_oklch(0.3_0.02_60/0.06),0_18px_50px_-18px_oklch(0.3_0.02_60/0.22)] ring-1 ring-foreground/[0.05]",
        landscape ? "aspect-[1.414] w-[820px]" : "aspect-[0.707] w-[600px]",
      )}
      style={{ minHeight: landscape ? 580 : 848 }}
    >
      <div className="relative px-[60px] py-[56px] font-serif text-[11.25px] leading-[1.6]">
        {blocks.map((b) =>
          b.type === "table" ? (
            <TableBlock key={b.key} paras={b.paras} engine={engine} frame={frame} />
          ) : (
            <ParagraphView key={b.key} p={b.paras[0]} engine={engine} frame={frame} />
          ),
        )}
        {paras.length === 0 && (
          <div className="grid h-[640px] place-items-center font-sans text-sm text-muted-foreground">Opening…</div>
        )}
      </div>

      {/* margin mark: a pen line beside the paragraph being read or checked */}
      {box && (
        <motion.div
          className={cn("pointer-events-none absolute top-0 left-[34px] w-[2px] rounded-full", box.mode === "detect" ? "bg-foreground/15" : "bg-brand")}
          initial={false}
          animate={{ y: box.y + 4, height: Math.max(10, box.h - 8) }}
          transition={{ type: "spring", stiffness: 300, damping: 34 }}
        />
      )}

      {/* active word recognition rectangle */}
      <div
        ref={wordBoxRef}
        className="pointer-events-none absolute top-0 left-0 rounded-[2px] bg-[oklch(0.56_0.15_38/0.09)] opacity-0 transition-[transform,width,height] duration-75 ease-linear"
      />
      {/* horizontal light sweep along the current line */}
      <div
        ref={sweepRef}
        className="pointer-events-none absolute top-0 left-0 hidden"
      />
      {/* scanning beam */}
      <div ref={beamRef} className="pointer-events-none absolute inset-x-0 top-0 opacity-0 transition-[transform,opacity] duration-200 ease-out">
        <div className="mx-[60px] h-px bg-brand/50" />
      </div>
    </div>
  );
}


interface ParaProps {
  p: VisualParagraph;
  state: ParaVisualState;
  wordIdx: number;
  sentence: number;
  flashIdx: number;
}

function paraProps(engine: ScanEngine, p: VisualParagraph): ParaProps {
  const state = engine.vState.get(p.id) ?? "pending";
  const active = engine.activePid === p.id;
  const fl = engine.flashSentence;
  return {
    p,
    state,
    wordIdx: state === "recognizing" ? engine.wordIdx : state === "pending" ? 0 : Number.MAX_SAFE_INTEGER,
    sentence: active && state === "analyzing" ? engine.sentenceIdx : -1,
    flashIdx: fl && fl.pid === p.id && engine.now - fl.at < 450 ? fl.idx : -1,
  };
}

function ParagraphView({ p, engine }: { p: VisualParagraph; engine: ScanEngine; frame: number }) {
  return <ParagraphInner {...paraProps(engine, p)} />;
}

// Shallow-compared primitive props: only paragraphs whose visual state changes re-render.
const ParagraphInner = memo(function ParagraphInner({ p, state, wordIdx, sentence, flashIdx }: ParaProps) {
  const isHeading = p.kind === "heading" || p.kind === "title" || p.probable_heading;
  const activeSentence = sentence >= 0 ? p.sentences[Math.min(sentence, p.sentences.length - 1)] ?? null : null;
  const flash = flashIdx >= 0 ? p.sentences[flashIdx] ?? null : null;

  if (p.kind === "object" || (p.objects.some((o) => o.kind === "image") && p.words.length === 0)) {
    return (
      <div data-pid={p.id} className="my-2 flex justify-center">
        <div
          className={cn(
            "flex h-20 w-48 items-center justify-center gap-1.5 rounded-sm border font-sans text-[9px] tracking-wide transition-colors duration-500",
            state === "pending" ? "border-dashed border-foreground/15 text-transparent" : "border-foreground/15 bg-foreground/[0.03] text-muted-foreground",
          )}
        >
          <ImageIcon className="size-3.5" /> Image
        </div>
      </div>
    );
  }
  if (p.kind === "empty") return <div data-pid={p.id} className="h-[0.8em]" />;

  const cls = cn(
    "relative transition-colors",
    isHeading && p.heading_level === 0 && "mb-3 text-center text-[17px] font-semibold leading-tight",
    isHeading && p.heading_level === 1 && "mt-3 mb-1.5 text-[13.5px] font-semibold",
    isHeading && (p.heading_level ?? 2) >= 2 && p.heading_level !== 0 && p.heading_level !== 1 && "mt-2 mb-1 text-[11.5px] font-semibold",
    !isHeading && "mb-[0.55em]",
    p.kind === "list_item" && "pl-4",
    p.kind === "caption" && "text-center text-[9.5px] italic opacity-80",
    p.alignment === "center" && "text-center",
    p.alignment === "right" && "text-right",
    p.alignment === "both" && "text-justify",
  );
  const indent = p.list ? { paddingLeft: `${12 + p.list.level * 12}px` } : undefined;
  return (
    <div data-pid={p.id} className={cls} style={indent}>
      {state === "analyzed" && (
        <motion.span initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.3 }}
          className="pointer-events-none absolute top-[0.3em] -left-[34px] font-sans text-[11px] leading-none text-brand" aria-hidden>✓</motion.span>
      )}
      {p.list && (
        <span className="absolute -ml-3 select-none">{p.list.format === "bullet" ? "•" : "›"}</span>
      )}
      {renderWords(p, wordIdx, activeSentence, flash)}
    </div>
  );
});

function renderWords(
  p: VisualParagraph,
  wordIdx: number,
  activeSentence: [number, number] | null,
  flash: [number, number] | null,
) {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  const text = p.text;
  p.words.forEach(([s, e], i) => {
    if (s > cursor) out.push(<span key={`g${i}`}>{sepText(text.slice(cursor, s), i < wordIdx)}</span>);
    const revealed = i < wordIdx;
    const active = i === wordIdx - 1;
    const inSentence = activeSentence && s >= activeSentence[0] && e <= activeSentence[1];
    const inFlash = flash && s >= flash[0] && e <= flash[1];
    out.push(
      <span
        key={i}
        data-w={i}
        className={cn(
          "rounded-[1px] transition-[color,background-color] duration-300",
          !revealed && "bg-foreground/[0.07] text-transparent",
          revealed && active && "text-brand",
          inFlash && "bg-highlighter/50",
          inSentence && "bg-highlighter",
        )}
      >
        {text.slice(s, e)}
      </span>,
    );
    cursor = e;
  });
  if (cursor < text.length) out.push(<span key="tail">{sepText(text.slice(cursor), wordIdx >= p.words.length)}</span>);
  return out;
}

function sepText(t: string, revealed: boolean) {
  const cleaned = t.replace(/￼/g, "");
  if (!revealed) return cleaned.replace(/\S/g, " ");
  return cleaned;
}

function TableBlock({ paras, engine, frame }: { paras: VisualParagraph[]; engine: ScanEngine; frame: number }) {
  const rows = new Map<number, Map<number, VisualParagraph[]>>();
  let maxCol = 0;
  for (const p of paras) {
    const t = p.table![0];
    if (!rows.has(t.row)) rows.set(t.row, new Map());
    const r = rows.get(t.row)!;
    if (!r.has(t.col)) r.set(t.col, []);
    r.get(t.col)!.push(p);
    maxCol = Math.max(maxCol, t.col);
  }
  const allDone = paras.every((p) => (engine.vState.get(p.id) ?? "pending") !== "pending");
  return (
    <div className={cn("relative my-2 border transition-colors duration-500", allDone ? "border-foreground/30" : "border-foreground/15")}>
      {allDone && (
        <span className="absolute -top-3.5 right-0 font-sans text-[8px] text-muted-foreground">
          table {rows.size}×{maxCol + 1}
        </span>
      )}
      {[...rows.entries()].sort((a, b) => a[0] - b[0]).map(([ri, cols]) => (
        <div key={ri} className="grid border-b border-foreground/15 last:border-b-0" style={{ gridTemplateColumns: `repeat(${maxCol + 1}, minmax(0, 1fr))` }}>
          {Array.from({ length: maxCol + 1 }, (_, ci) => (
            <div key={ci} className="border-r border-foreground/15 px-1.5 py-1 text-[9.5px] last:border-r-0">
              {(cols.get(ci) ?? []).map((p) => (
                <ParagraphInner key={p.id} {...paraProps(engine, p)} />
              ))}
            </div>
          ))}
        </div>
      ))}
      <span className="hidden">{frame}</span>
    </div>
  );
}
