"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { AlertTriangle, ChevronDown, Lock, Minus, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Scribble } from "@/components/human/ink";
import { estimate, formatRemaining } from "@/lib/humanizer-eta";
import type { StageKey } from "@/lib/humanizer";
import { cn } from "@/lib/utils";
import { STAGE_LABELS, STAGE_ORDER, useHumanizer, type Processing } from "@/stores/humanizer-store";

function useNow(running: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  return now;
}

const fmt = (s: number) => {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};

const sectionNo = (label?: string) => Number(label?.match(/(\d+)\s*\//)?.[1] ?? 0) || null;

/** What the model is doing right now, in plain words. */
function nowText(p: Processing): string {
  const a = p.activity;
  const active = STAGE_ORDER.find((k) => p.stages[k].state === "active");
  if (!active) return "Starting…";
  const n = sectionNo(a?.label);
  if (a?.activity === "retrying") return `Trying again${n ? ` (section ${n})` : ""}: ${a.reason ?? "temporary problem"}`;
  const thinking = a?.stage === active && a.activity === "thinking";
  const writing = a?.stage === active && a.activity === "writing";
  switch (active) {
    case "reading": return "Reading the document";
    case "analyzing": return "Measuring sentences and repeated patterns";
    case "context": {
      if (a?.label?.startsWith("map")) return writing ? "Listing the points each paragraph makes" : "Reading each paragraph for its claims, caveats and logic";
      return thinking ? "Reading the whole document to understand its purpose" : writing ? "Writing notes on the document's purpose and key terms" : "Recording qualifiers, negations and logical links to keep";
    }
    case "rewriting": {
      const s = n ?? (p.stages.rewriting.progress?.done ?? 0) + 1;
      return thinking ? `Planning how to restructure section ${s}` : writing ? `Writing the new version of section ${s}` : `Sending section ${s} to the model`;
    }
    case "vocabulary": return writing ? "Replacing vague, generic or needlessly formal words" : "Checking word choice against your original";
    case "style": return writing ? "Adjusting the passages that don't match the chosen style" : "Measuring each passage against the chosen style";
    case "grammar":
      return a?.label === "flow" ? (writing ? "Rewriting abrupt paragraph openings" : "Checking how paragraphs connect")
        : writing ? "Fixing grammar and splitting hard-to-follow sentences" : "Checking grammar, punctuation and readability";
    case "validating": {
      if (a?.label?.startsWith("repair")) return writing ? "Correcting a passage whose meaning drifted" : "Preparing a correction for a passage whose meaning drifted";
      const c = a?.label?.match(/check (\d+)\/(\d+)/);
      return c ? `Comparing section ${c[1]} of ${c[2]} with your original` : "Checking numbers, qualifiers, citations and quotations";
    }
    case "finalizing": return "Putting the result together";
    default: return "Working…";
  }
}

/** Merged sections carry several headings ("A / B / C"); show the first and how many follow. */
function headingLabel(h: string): string {
  const parts = h.split(" / ");
  return parts.length > 1 ? `${parts[0]} (+${parts.length - 1} more)` : h;
}

/** Text the model is producing, cleaned for display (block tags removed, whitespace collapsed). */
function cleanTail(t: string | undefined): string {
  if (!t) return "";
  return t.replace(/<\/?b(\s+id="?\d+"?)?>?/g, " ").replace(/⟦[A-Z]\d+⟧/g, "…").replace(/^\s*(?:b\s+)?(?:id=)?"?\d*"?>/, "").replace(/\s+/g, " ").trim();
}

export function ProcessingPanel() {
  const { processing: p, start, dismissProcessing } = useHumanizer();
  const reduced = useReducedMotion();
  const running = p?.status === "running" || p?.status === "cancelling";
  const now = useNow(!!running);
  const [stepsOpen, setStepsOpen] = useState(false);
  const eta = useMemo(() => (p ? estimate(p, now) : null), [p, now]);
  if (!p) return null;

  const elapsed = Math.max(0, Math.round((now - p.startedAt) / 1000));
  const progress = eta?.progress ?? 0.02;
  const failed = p.status === "failed";
  const partial = Object.entries(p.partial).sort((a, b) => Number(a[0]) - Number(b[0]));
  const plan = p.plan;
  const rw = p.stages.rewriting;
  const doneSections = rw.state === "completed" ? plan?.sections.length ?? 0 : rw.progress?.done ?? 0;
  const activeSection = rw.state === "active" ? doneSections : -1;
  const finishAt = eta?.remaining ? new Date(now + eta.remaining * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;

  return (
    <section aria-label="Progress" className="hw-paper flex min-h-[420px] flex-col rounded-[3px] lg:min-h-[560px]">
      <header className="flex items-center gap-2 border-b border-dashed border-foreground/10 px-5 py-2.5 text-[13px]">
        <h2 className="font-medium text-muted-foreground">
          {failed ? "Something went wrong" : p.status === "cancelled" ? "Stopped" : p.status === "cancelling" ? "Stopping…" : "Working on your text"}
        </h2>
        {p.variant && p.variant.total > 1 && (
          <span className="rounded-full bg-ink/10 px-2 py-0.5 text-[11.5px] font-medium text-ink">
            Variation {p.variant.index + 1} of {p.variant.total}{p.variant.label ? ` · ${p.variant.label}` : ""}
          </span>
        )}
        {p.model && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-foreground/[0.05] px-2 py-0.5 text-[11.5px] text-muted-foreground" title="Runs on this computer through Ollama">
            <Lock className="size-3" /> {p.model}
          </span>
        )}
      </header>

      <div className="px-6 pt-5 sm:px-8">
        {/* Headline: percentage, time left, elapsed */}
        <div className="flex items-end justify-between gap-4">
          <div aria-live="polite">
            <p className="font-display text-[44px] leading-none tabular-nums tracking-tight">
              {Math.round(progress * 100)}<span className="text-[24px] text-muted-foreground">%</span>
            </p>
          </div>
          <div className="text-right">
            {running && eta?.remaining != null ? (
              <>
                <p className="text-[15px] font-medium">{formatRemaining(eta.remaining)}</p>
                <p className="text-[12px] text-muted-foreground">
                  {eta.basis === "run" ? `Based on ${eta.measured} finished step${eta.measured === 1 ? "" : "s"}` : eta.basis === "history" ? "Rough guess from your last run" : "Rough guess; it improves after the first section"}
                  {finishAt && eta.remaining > 120 ? ` · done around ${finishAt}` : ""}
                </p>
              </>
            ) : running ? (
              <p className="text-[13px] text-muted-foreground">Estimating time…</p>
            ) : null}
            <p className="mt-0.5 text-[12px] tabular-nums text-muted-foreground">{fmt(elapsed)} elapsed</p>
          </div>
        </div>

        {/* Progress bar */}
        <div className="relative mt-4 h-1.5 overflow-hidden rounded-full bg-foreground/[0.08]" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)} aria-label="Overall progress">
          <motion.div className={cn("absolute inset-y-0 left-0 rounded-full", failed ? "bg-destructive" : "bg-ink")}
            initial={false} animate={{ width: `${progress * 100}%` }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }} />
          {running && !reduced && (
            <motion.div className="absolute inset-y-0 w-24 bg-gradient-to-r from-transparent via-white/45 to-transparent"
              animate={{ left: ["-6rem", "100%"] }} transition={{ duration: 2.2, repeat: Infinity, ease: "linear" }} />
          )}
        </div>

        {/* One segment per section, sized by its length */}
        {plan && plan.sections.length > 1 && (
          <div className="mt-4">
            <div className="flex gap-[3px]">
              {plan.sections.map((s, i) => (
                <div key={s.index} title={`Section ${i + 1}${s.heading ? `: ${s.heading}` : ""} — ${s.words} words`}
                  style={{ flexGrow: Math.max(s.words, 40) }}
                  className={cn("h-2 rounded-[2px] transition-colors duration-500",
                    i < doneSections ? "bg-ink/75" : i === activeSection ? "bg-ink/35" : "bg-foreground/[0.09]")}>
                  {i === activeSection && !reduced && (
                    <motion.div className="h-full rounded-[2px] bg-ink/50" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1.6, repeat: Infinity }} />
                  )}
                </div>
              ))}
            </div>
            <p className="mt-1.5 truncate text-[12px] text-muted-foreground">
              {rw.state === "completed"
                ? `All ${plan.sections.length} sections rewritten`
                : activeSection >= 0
                  ? <>Section {activeSection + 1} of {plan.sections.length}{plan.sections[activeSection]?.heading ? <> · <span className="text-foreground/75">{headingLabel(plan.sections[activeSection].heading!)}</span></> : null}</>
                  : `${plan.sections.length} sections, ${plan.words.toLocaleString()} words to rewrite`}
            </p>
          </div>
        )}
      </div>

      {/* Live view of the model */}
      {running && <LiveCard p={p} reduced={!!reduced} />}

      {(failed || p.status === "cancelled") && (
        <div className="mx-6 mt-4 rounded-md border border-dashed border-foreground/15 p-3 text-[13px] sm:mx-8">
          {failed && <p className="flex gap-2 text-foreground/90"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />{p.error}</p>}
          {p.status === "cancelled" && <p className="text-muted-foreground">The rewrite was stopped. Your text hasn&apos;t changed.</p>}
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={start}><RotateCcw /> Try again</Button>
            <Button size="sm" variant="ghost" onClick={dismissProcessing}>Close</Button>
          </div>
        </div>
      )}

      {/* Steps */}
      <div className="mx-6 mt-4 sm:mx-8">
        <button type="button" onClick={() => setStepsOpen((v) => !v)} aria-expanded={stepsOpen || failed}
          className="flex w-full items-center gap-2 text-[12px] font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground">
          Steps
          <span className="font-normal normal-case tracking-normal">
            {STAGE_ORDER.filter((k) => ["completed", "skipped"].includes(p.stages[k].state)).length} of {STAGE_ORDER.length} done
          </span>
          <ChevronDown className={cn("ml-auto size-3.5 transition-transform", (stepsOpen || failed) && "rotate-180")} />
        </button>
        <StepDots p={p} />
        <AnimatePresence initial={false}>
          {(stepsOpen || failed) && (
            <motion.ol initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="mt-2 overflow-hidden">
              {STAGE_ORDER.map((k, i) => <StepRow key={k} k={k} i={i} p={p} reduced={!!reduced} />)}
            </motion.ol>
          )}
        </AnimatePresence>
      </div>

      {partial.length > 0 && running && (
        <div className="mt-4 flex-1 border-t border-dashed border-foreground/10 px-6 py-4 sm:px-8">
          <p className="mb-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Rewritten so far</p>
          <div className="max-h-72 space-y-3 overflow-y-auto pr-2">
            <AnimatePresence initial={false}>
              {partial.slice(-6).map(([id, text]) => (
                <motion.p key={id} initial={{ opacity: 0, filter: "blur(3px)" }} animate={{ opacity: 1, filter: "blur(0px)" }} transition={{ duration: 0.5 }}
                  className="font-serif text-[14px] leading-relaxed text-foreground/85">
                  {text.trim()}
                </motion.p>
              ))}
            </AnimatePresence>
          </div>
        </div>
      )}
      <div className="pb-5" />
    </section>
  );
}

function LiveCard({ p, reduced }: { p: Processing; reduced: boolean }) {
  const a = p.activity;
  const active = STAGE_ORDER.find((k) => p.stages[k].state === "active");
  const live = a && a.stage === active ? a : null;
  const tail = cleanTail(live?.tail);
  const thinking = live?.activity === "thinking";
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [tail]);
  const usesModel = active && ["context", "rewriting", "vocabulary", "style", "grammar", "validating"].includes(active);

  return (
    <div className="mx-6 mt-5 rounded-lg border border-foreground/10 bg-background/70 sm:mx-8">
      <div className="flex items-center gap-2 border-b border-dashed border-foreground/10 px-3.5 py-2">
        <motion.span className="size-2 shrink-0 rounded-full bg-ink" animate={reduced ? undefined : { opacity: [1, 0.35, 1] }} transition={{ duration: 1.3, repeat: Infinity }} />
        <p className="min-w-0 flex-1 text-[13.5px] leading-snug font-medium sm:truncate">{nowText(p)}</p>
        {live && (live.chars || live.thinkingChars) ? (
          <span className="shrink-0 text-[11.5px] tabular-nums text-muted-foreground">
            {thinking ? `${(live.thinkingChars ?? 0).toLocaleString()} chars of reasoning` : `${(live.chars ?? 0).toLocaleString()} chars written`}
          </span>
        ) : null}
      </div>
      <div ref={ref} className="relative h-[92px] overflow-hidden px-3.5 py-2.5 [mask-image:linear-gradient(to_bottom,transparent,black_35%)]">
        {tail ? (
          <p className={cn("text-[13px] leading-relaxed", thinking ? "font-serif italic text-foreground/55" : "font-serif text-foreground/85")}>
            {tail}
            {!reduced && <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse bg-ink/60" />}
          </p>
        ) : (
          <p className="absolute bottom-2.5 text-[12.5px] text-muted-foreground">
            {usesModel ? "Waiting for the model's first words. Loading the model into memory can take a minute the first time." : "Working…"}
          </p>
        )}
      </div>
      <p className="border-t border-dashed border-foreground/10 px-3.5 py-1.5 text-[11.5px] text-muted-foreground">
        {thinking ? "The model's reasoning, live. It's private to this computer and isn't part of your text." : tail ? "The model's output, live. You'll review every change before anything is used." : "Live view of the model"}
      </p>
    </div>
  );
}

function StepDots({ p }: { p: Processing }) {
  return (
    <div className="mt-2 flex gap-1" aria-hidden>
      {STAGE_ORDER.map((k) => {
        const s = p.stages[k].state;
        return (
          <span key={k} title={STAGE_LABELS[k]}
            className={cn("h-1 flex-1 rounded-full", s === "completed" ? "bg-[oklch(0.55_0.1_150)]" : s === "skipped" ? "bg-foreground/20" : s === "active" ? "bg-ink" : s === "failed" ? "bg-destructive" : "bg-foreground/[0.09]")} />
        );
      })}
    </div>
  );
}

function StepRow({ k, i, p, reduced }: { k: StageKey; i: number; p: Processing; reduced: boolean }) {
  const s = p.stages[k];
  const active = s.state === "active";
  const dur = s.startedAt && s.endedAt ? Math.round((s.endedAt - s.startedAt) / 1000) : null;
  return (
    <li className={cn("flex gap-3 rounded-md px-1.5 py-1", active && "bg-ink/[0.035]")}>
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
        {s.state === "completed" && <span aria-label="done" className="text-[oklch(0.5_0.1_150)]"><Scribble variant="tick" className="size-3.5" strokeWidth={2.6} duration={0.35} /></span>}
        {s.state === "skipped" && <Minus className="size-3 text-muted-foreground" aria-label="skipped" />}
        {s.state === "failed" && <X className="size-3.5 text-destructive" aria-label="failed" />}
        {s.state === "pending" && <span className="size-1.5 rounded-full border border-foreground/25" aria-label="waiting" />}
        {active && (
          <motion.span className="size-2 rounded-full bg-ink" aria-label="in progress"
            animate={reduced ? undefined : { scale: [1, 1.4, 1], opacity: [1, 0.55, 1] }} transition={{ duration: 1.4, repeat: Infinity }} />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className={cn("text-[13px]", s.state === "pending" ? "text-muted-foreground/70" : "text-foreground", active && "font-medium")}>
            {i + 1}. {STAGE_LABELS[k]}
          </span>
          {dur !== null && s.state === "completed" && <span className="text-[11px] tabular-nums text-muted-foreground">{fmt(dur)}</span>}
          {s.state === "skipped" && <span className="text-[11px] text-muted-foreground">skipped</span>}
        </div>
        {s.detail && s.state !== "pending" && (
          <p className={cn("text-[12px] leading-snug", s.state === "failed" ? "text-destructive" : "text-muted-foreground")}>{s.detail}</p>
        )}
      </div>
    </li>
  );
}
