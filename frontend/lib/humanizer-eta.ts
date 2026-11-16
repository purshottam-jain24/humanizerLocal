/**
 * Time estimate for a humanizer run.
 *
 * Work is counted in "units": every model call costs a fixed reasoning overhead plus roughly one
 * unit per word it reads or writes. The speed (seconds per unit) is measured from the calls that
 * have already finished in this run; before the first one finishes it falls back to the speed of
 * the last run with the same model on this computer, then to a measured default for a small local
 * reasoning model. The estimate is therefore rough at the start and improves after each section.
 */
import type { RunEvent, StageKey } from "./humanizer";

type Plan = Extract<RunEvent, { type: "plan" }>;

export const CALL_OVERHEAD = 120;
/** Seconds per unit measured with qwen3:4b on an Apple-silicon laptop (e-mail and academic test runs). */
const DEFAULT_RATE = 0.9;
/** Fast speed (no reasoning phase): the answer itself at ~27 tokens/s on the same laptop. */
const DEFAULT_RATE_FAST = 0.06;
const KEY = (model: string) => `hw.humanizer.rate.${model}`;

/** Rough units for one targeted-refinement stage (only flagged blocks go to the model, usually one short call). */
const refineUnits = (plan: Plan) => CALL_OVERHEAD * 0.5 + Math.min(plan.words, 1500) * 0.15;

export function callUnits(stage: StageKey, plan: Plan, index: number): number {
  const words = plan.sections[index]?.words ?? 0;
  switch (stage) {
    case "context":
      return (plan.context_call ? CALL_OVERHEAD + Math.min(700, plan.words) * 0.3 : 0)
        + (plan.semantic_calls ?? 0) * CALL_OVERHEAD * 0.5 + (plan.semantic_calls ? plan.words * 0.35 : 0);
    case "rewriting": return CALL_OVERHEAD + words;
    case "vocabulary": case "style": return refineUnits(plan);
    case "grammar": return refineUnits(plan) + (plan.flow ? CALL_OVERHEAD * 0.4 : 0);
    case "validating": return plan.ai_validation ? CALL_OVERHEAD + words * 0.5 : 0;
    default: return 0;
  }
}

export function recordRate(model: string, timings: { units: number; seconds: number }[]) {
  const u = timings.reduce((a, t) => a + t.units, 0);
  if (!u) return;
  try {
    localStorage.setItem(KEY(model), String(timings.reduce((a, t) => a + t.seconds, 0) / u));
  } catch {
    /* storage unavailable: the estimate just starts from the default next time */
  }
}

function savedRate(model: string | null): number | null {
  if (!model) return null;
  try {
    const v = Number(localStorage.getItem(KEY(model)));
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

interface StageLike { state: string; progress?: { done: number; total: number } }

export interface Estimate {
  /** 0–1, based on work units, so it moves with the real work rather than with stage count. */
  progress: number;
  /** Seconds left, or null when it cannot be estimated yet. */
  remaining: number | null;
  basis: "run" | "history" | "default";
  measured: number;
}

export function estimate(p: {
  plan: Plan | null; model: string | null; stages: Record<StageKey, StageLike>; timings: { units: number; seconds: number }[];
  stepStartedAt: number | null; settings?: { speed?: string }; variant?: { index: number } | null;
}, now: number): Estimate | null {
  const plan = p.plan;
  if (!plan) return null;
  const st = p.stages;
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
  const n = plan.sections.length;
  const all = (stage: StageKey) => sum(plan.sections.map((_, i) => callUnits(stage, plan, i)));

  const ctx = callUnits("context", plan, 0);
  const perVariant = all("rewriting") + callUnits("vocabulary", plan, 0) + callUnits("style", plan, 0) + callUnits("grammar", plan, 0)
    + all("validating");
  const variants = Math.max(1, plan.variants ?? 1);
  const vi = Math.min(variants - 1, Math.max(0, p.variant?.index ?? 0));
  const total = ctx + perVariant * variants;
  let remaining = perVariant * (variants - vi - 1); // variations not started yet
  let current = 0; // units of the call in progress

  if (st.context.state === "pending") remaining += ctx;
  else if (st.context.state === "active") {
    const done = st.context.progress?.done ?? 0;
    const tot = st.context.progress?.total ?? 1;
    remaining += ctx * (1 - done / Math.max(1, tot + (plan.context_call ? 1 : 0)));
    current = ctx / Math.max(1, (plan.semantic_calls ?? 0) + (plan.context_call ? 1 : 0));
  }

  if (st.rewriting.state === "pending") remaining += all("rewriting");
  else if (st.rewriting.state === "active") {
    const done = st.rewriting.progress?.done ?? 0;
    for (let i = done; i < n; i++) remaining += callUnits("rewriting", plan, i);
    current = callUnits("rewriting", plan, done);
  }

  for (const k of ["vocabulary", "style", "grammar"] as StageKey[]) {
    const u = callUnits(k, plan, 0);
    if (st[k].state === "pending") remaining += u;
    else if (st[k].state === "active") { remaining += u; current = u; }
  }

  if (st.validating.state === "pending") remaining += all("validating");
  else if (st.validating.state === "active" && plan.ai_validation) {
    const done = st.validating.progress?.done ?? 0;
    const groups = st.validating.progress?.total ?? n;
    // Only sections with changes are checked; scale the per-section cost to the real number of checks.
    const per = all("validating") / Math.max(1, n);
    remaining += per * Math.max(0, groups - done);
    current = per;
  }

  const measuredUnits = sum(p.timings.map((t) => t.units));
  let rate = p.settings?.speed === "thorough" ? DEFAULT_RATE : DEFAULT_RATE_FAST;
  let basis: Estimate["basis"] = "default";
  if (measuredUnits > 0) {
    rate = sum(p.timings.map((t) => t.seconds)) / measuredUnits;
    basis = "run";
  } else {
    const saved = savedRate(p.model && `${p.model}|${p.settings?.speed ?? "fast"}`);
    if (saved) { rate = saved; basis = "history"; }
  }

  // Credit the call in progress for the time it has been running, but never count it as finished.
  const inStep = p.stepStartedAt && current ? Math.min((now - p.stepStartedAt) / 1000 / rate, current * 0.9) : 0;
  const left = Math.max(0, remaining - inStep);
  const finishing = vi === variants - 1 && (st.finalizing.state === "active" || st.finalizing.state === "completed");
  return {
    progress: finishing ? 0.99 : total ? Math.min(0.98, Math.max(0.02, 1 - left / total)) : 0,
    remaining: finishing ? 0 : left * rate,
    basis,
    measured: p.timings.length,
  };
}

export function formatRemaining(s: number): string {
  if (s < 45) return "Almost done";
  const m = Math.round(s / 60);
  if (m < 2) return "About a minute left";
  if (m < 60) return `About ${m < 10 ? m : Math.round(m / 5) * 5} min left`;
  const h = Math.floor(m / 60);
  const rest = Math.round((m % 60) / 5) * 5;
  return `About ${h} h${rest ? ` ${rest} min` : ""} left`;
}
