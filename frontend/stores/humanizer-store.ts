"use client";

import { create } from "zustand";
import { toast } from "sonner";
import { callUnits, recordRate } from "@/lib/humanizer-eta";
import {
  humanizer, type Approach, type DocContext, type Extracted, type Flag, type RunEvent, type RunResult, type RunSettings, type StageKey,
  type StageState, type WritingProfile,
} from "@/lib/humanizer";

/**
 * Humanizer workspace state. Everything lives in memory in this tab: the input is never
 * modified by a run, every run becomes a separate *version* with its own undo/redo history,
 * so switching mode or intensity never loses earlier results.
 */

export interface Version {
  id: string;
  createdAt: number;
  label: string;
  settings: RunSettings;
  original: string;
  result: RunResult | null; // null for "edit manually" versions (no AI run)
  history: string[];
  index: number;
  accepted: string[]; // signatures of changes the user explicitly accepted
  flags: Flag[];
  flagsFor: string; // the text the flags were computed for
  lastEdit?: { kind: "typing" | "action"; at: number };
  /** Versions produced by one run with several variations (and combinations of them) share a group id. */
  group?: string;
  approach?: Approach | "combined";
}

export interface StageStatus {
  state: StageState;
  detail: string | null;
  progress?: { done: number; total: number };
  startedAt?: number;
  endedAt?: number;
}

export interface CallTiming { units: number; seconds: number }

export interface Processing {
  runId: string;
  startedAt: number;
  input: string;
  settings: RunSettings;
  model: string | null;
  stages: Record<StageKey, StageStatus>;
  activity: {
    stage: StageKey; activity: string; label?: string; chars?: number; thinkingChars?: number; tail?: string; reason?: string; at: number;
  } | null;
  /** Sections and model calls the backend will make, sent before the slow work starts. */
  plan: Extract<RunEvent, { type: "plan" }> | null;
  /** Real durations of finished model calls in this run, used for the time estimate. */
  timings: CallTiming[];
  /** When the current section / check started (for the estimate of the call in progress). */
  stepStartedAt: number | null;
  partial: Record<string, string>;
  /** The variation being written now (several approaches run one after another). */
  variant: { index: number; total: number; label: string } | null;
  variantLabels: string[];
  profile: WritingProfile | null;
  context: DocContext | null;
  error: string | null;
  status: "running" | "cancelling" | "failed" | "cancelled";
}

export const STAGE_ORDER: StageKey[] = ["reading", "analyzing", "context", "rewriting", "vocabulary", "style", "grammar", "validating", "finalizing"];
export const STAGE_LABELS: Record<StageKey, string> = {
  reading: "Reading document",
  analyzing: "Analyzing writing patterns",
  context: "Understanding meaning & context",
  rewriting: "Restructuring sentences & paragraphs",
  vocabulary: "Refining vocabulary",
  style: "Applying writing style",
  grammar: "Grammar, readability & flow",
  validating: "Validating & repairing meaning",
  finalizing: "Preparing final output",
};
/** Stages that run once per variation. */
export const VARIANT_STAGES: StageKey[] = ["rewriting", "vocabulary", "style", "grammar", "validating", "finalizing"];

const DEFAULT_SETTINGS: RunSettings = {
  mode: "natural", intensity: "medium", profile_id: null, custom_instructions: "", document_type: "auto", audience: "", ai_validation: true, speed: "fast",
  approaches: ["balanced"],
};

const MAX_VERSIONS = 10;
const MAX_HISTORY = 100;
let controller: AbortController | null = null;

interface State {
  input: string;
  inputMeta: Pick<Extracted, "filename" | "source" | "warnings"> | null;
  settings: RunSettings;
  versions: Version[];
  activeId: string | null;
  processing: Processing | null;
  view: "compose" | "review" | "compare";
  /** Set when another page hands over text to be rewritten straight away (e.g. the document's "Humanize it"). */
  autoStart: boolean;

  setInput: (text: string, meta?: State["inputMeta"]) => void;
  setSettings: (patch: Partial<RunSettings>) => void;
  setView: (v: State["view"]) => void;
  /** Load text from elsewhere in the app and run the humanizer as soon as its page is ready. */
  queueRun: (text: string, meta?: State["inputMeta"]) => void;
  clearAutoStart: () => void;
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  dismissProcessing: () => void;
  startManual: () => void;
  setActive: (id: string) => void;
  removeVersion: (id: string) => void;
  /** Record a new state of the text. Consecutive typing within a short window becomes one undo step. */
  commit: (text: string, kind?: "typing" | "action") => void;
  undo: () => void;
  redo: () => void;
  markAccepted: (sigs: string[]) => void;
  setFlags: (flags: Flag[], forText: string) => void;
  /** Add a version built from parts of other versions (e.g. the best paragraph of each variation). */
  addCombined: (from: Version, text: string, label: string) => string;
}

const emptyStages = (): Record<StageKey, StageStatus> =>
  Object.fromEntries(STAGE_ORDER.map((k) => [k, { state: "pending", detail: null }])) as Record<StageKey, StageStatus>;

export function currentText(v: Version | null | undefined): string {
  return v ? v.history[v.index] : "";
}

export function versionLabel(s: RunSettings, modes?: { value: string; label: string }[]): string {
  const m = modes?.find((x) => x.value === s.mode)?.label ?? s.mode.replace(/^\w/, (c) => c.toUpperCase());
  return `${m} · ${s.intensity.replace(/^\w/, (c) => c.toUpperCase())}`;
}

export const useHumanizer = create<State>((set, get) => ({
  input: "",
  inputMeta: null,
  settings: DEFAULT_SETTINGS,
  versions: [],
  activeId: null,
  processing: null,
  view: "compose",
  autoStart: false,

  setInput: (text, meta = null) => set({ input: text, inputMeta: meta }),
  setSettings: (patch) => set((s) => ({ settings: { ...s.settings, ...patch } })),
  setView: (view) => set({ view }),
  queueRun: (text, meta = null) => {
    if (get().processing?.status === "running") {
      toast("The humanizer is already working on another text.");
      return;
    }
    set({ input: text, inputMeta: meta, view: "compose", processing: null, autoStart: true });
  },
  clearAutoStart: () => set({ autoStart: false }),

  start: async () => {
    const { input, settings, processing } = get();
    if (processing?.status === "running") return;
    if (!input.trim()) {
      toast.error("Add some text first.");
      return;
    }
    if (settings.mode === "custom" && !settings.custom_instructions.trim()) {
      toast.error("Describe the style you want for Custom mode.");
      return;
    }
    const runId = `run_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    controller?.abort();
    controller = new AbortController();
    set({
      view: "compose",
      processing: {
        runId, startedAt: Date.now(), input, settings: { ...settings }, model: null, stages: emptyStages(), activity: null, partial: {},
        plan: null, timings: [], stepStartedAt: null, variant: null, variantLabels: [],
        profile: null, context: null, error: null, status: "running",
      },
    });
    const patch = (fn: (p: Processing) => Partial<Processing>) =>
      set((s) => (s.processing && s.processing.runId === runId ? { processing: { ...s.processing, ...fn(s.processing) } } : {}));

    const onEvent = (ev: RunEvent) => {
      switch (ev.type) {
        case "started":
          patch(() => ({ model: ev.model, variantLabels: (ev.variants ?? []).map((v) => v.label) }));
          break;
        case "plan":
          patch(() => ({ plan: ev }));
          break;
        case "stage":
          patch((p) => {
            const prev = p.stages[ev.stage];
            const now = Date.now();
            // Record how long each finished model call took, to estimate the rest of the run.
            let timings = p.timings;
            let stepStartedAt = p.stepStartedAt;
            const started = prev.state !== "active" && ev.state === "active";
            if (started) stepStartedAt = now;
            // A variation starting over resets the per-variation stages (the backend sends them as pending again).
            const newVariant = ev.variant !== undefined && ev.variant !== p.variant?.index;
            const variant = ev.variant !== undefined
              ? { index: ev.variant, total: ev.variants ?? 1, label: p.variantLabels[ev.variant] ?? ev.approach ?? "" }
              : p.variant;
            const finished = prev.state === "active" && ev.state === "completed" && !ev.fallback;
            const stepDone = ev.stage === "rewriting" || ev.stage === "validating"
              ? (ev.progress?.done ?? 0) > (prev.progress?.done ?? 0)
                || (ev.stage === "validating" && ev.state === "completed" && !!prev.progress && prev.progress.done < prev.progress.total)
              // Context, semantic map and targeted refinements: count them only when a model call actually ran.
              : ["context", "vocabulary", "style", "grammar"].includes(ev.stage) && finished && !!stepStartedAt && now - stepStartedAt > 1500;
            if (stepDone && stepStartedAt && p.plan) {
              const units = callUnits(ev.stage, p.plan, ev.stage === "context" ? 0 : (prev.progress?.done ?? 0));
              if (units) {
                timings = [...timings, { units, seconds: (now - stepStartedAt) / 1000 }];
                if (p.model) recordRate(`${p.model}|${p.settings.speed ?? "fast"}`, timings);
              }
              stepStartedAt = now;
            }
            return {
              timings, stepStartedAt, variant,
              partial: newVariant ? {} : p.partial,
              stages: {
                ...p.stages,
                [ev.stage]: {
                  state: ev.state, detail: ev.detail, progress: ev.progress ?? prev.progress,
                  startedAt: prev.startedAt ?? now, endedAt: ev.state === "active" ? undefined : now,
                },
              },
              context: ev.context ?? p.context,
              activity: ev.state === "active" ? p.activity : p.activity?.stage === ev.stage ? null : p.activity,
            };
          });
          break;
        case "activity":
          patch((p) => ({
            activity: {
              stage: ev.stage, activity: ev.activity, label: ev.label, chars: ev.chars, thinkingChars: ev.thinking_chars, reason: ev.reason, at: Date.now(),
              // Keep the last text shown while a retry notice replaces the live stream.
              tail: ev.tail ?? (ev.activity === "retrying" ? undefined : p.activity?.tail),
            },
          }));
          break;
        case "profile":
          patch(() => ({ profile: ev.profile }));
          break;
        case "section":
          patch((p) => ({ partial: { ...p.partial, ...ev.revised } }));
          break;
        case "completed": {
          const r = ev.result;
          const variants = r.variants?.length ? r.variants : [{ ...r, approach: "balanced" as Approach, label: "Balanced", change_ratio: 0 }];
          const group = variants.length > 1 ? runId : undefined;
          const created = Date.now();
          const vs: Version[] = variants.map((vr, i) => ({
            id: i ? `${runId}_${i}` : runId, createdAt: created, label: versionLabel(r.settings) + (group ? ` · ${vr.label}` : ""),
            settings: r.settings, original: r.original, result: { ...r, ...vr }, history: [vr.output], index: 0, accepted: [],
            flags: [...vr.flags, ...vr.notes], flagsFor: vr.output, group, approach: vr.approach,
          }));
          set((s) => ({
            versions: [...vs, ...s.versions].slice(0, MAX_VERSIONS),
            activeId: runId,
            processing: null,
            view: "review",
          }));
          toast.success(vs.length > 1
            ? `${vs.length} variations ready in ${Math.round(r.duration_s)} s — compare them, or combine the best parts.`
            : `Done in ${Math.round(r.duration_s)} s — review the changes before using them.`);
          break;
        }
        case "cancelled":
          patch(() => ({ status: "cancelled", activity: null }));
          break;
        case "error":
          patch((p) => {
            const active = STAGE_ORDER.find((k) => p.stages[k].state === "active");
            return {
              status: "failed", error: ev.message, activity: null,
              stages: active ? { ...p.stages, [active]: { ...p.stages[active], state: "failed", detail: ev.message } } : p.stages,
            };
          });
          break;
      }
    };

    try {
      await humanizer.run({ ...settings, text: input, run_id: runId }, onEvent, controller.signal);
      // Stream ended without a terminal event (e.g. server restarted).
      const p = get().processing;
      if (p && p.runId === runId && p.status === "running") patch(() => ({ status: "failed", error: "The connection to the backend ended unexpectedly." }));
    } catch (e) {
      const p = get().processing;
      if ((e as Error).name === "AbortError") {
        if (p && p.runId === runId) patch(() => ({ status: "cancelled", activity: null }));
        return;
      }
      if (p && p.runId === runId) patch(() => ({ status: "failed", error: (e as Error).message }));
    }
  },

  cancel: async () => {
    const p = get().processing;
    if (!p || p.status !== "running") return;
    set({ processing: { ...p, status: "cancelling" } });
    try {
      await humanizer.cancel(p.runId);
    } catch {
      /* the abort below still stops it */
    }
    controller?.abort();
  },

  dismissProcessing: () => set((s) => ({ processing: null, view: s.versions.length ? s.view : "compose" })),

  startManual: () => {
    const { input, settings } = get();
    if (!input.trim()) return;
    const id = `manual_${Date.now().toString(36)}`;
    const v: Version = {
      id, createdAt: Date.now(), label: "Manual edits", settings: { ...settings }, original: input, result: null,
      history: [input], index: 0, accepted: [], flags: [], flagsFor: input,
    };
    set((s) => ({ versions: [v, ...s.versions].slice(0, MAX_VERSIONS), activeId: id, view: "review" }));
  },

  setActive: (id) => set({ activeId: id, view: "review" }),

  removeVersion: (id) =>
    set((s) => {
      const versions = s.versions.filter((v) => v.id !== id);
      return { versions, activeId: s.activeId === id ? versions[0]?.id ?? null : s.activeId, view: versions.length ? s.view : "compose" };
    }),

  commit: (text, kind = "action") =>
    set((s) => ({
      versions: s.versions.map((v) => {
        if (v.id !== s.activeId || v.history[v.index] === text) return v;
        const now = Date.now();
        const coalesce = kind === "typing" && v.lastEdit?.kind === "typing" && now - v.lastEdit.at < 1500 && v.index === v.history.length - 1 && v.index > 0;
        const base = coalesce ? v.history.slice(0, v.index) : v.history.slice(0, v.index + 1);
        const history = [...base, text].slice(-MAX_HISTORY);
        return { ...v, history, index: history.length - 1, lastEdit: { kind, at: now } };
      }),
    })),

  undo: () =>
    set((s) => ({ versions: s.versions.map((v) => (v.id === s.activeId && v.index > 0 ? { ...v, index: v.index - 1, lastEdit: undefined } : v)) })),
  redo: () =>
    set((s) => ({
      versions: s.versions.map((v) => (v.id === s.activeId && v.index < v.history.length - 1 ? { ...v, index: v.index + 1, lastEdit: undefined } : v)),
    })),

  markAccepted: (sigs) =>
    set((s) => ({
      versions: s.versions.map((v) => (v.id === s.activeId ? { ...v, accepted: Array.from(new Set([...v.accepted, ...sigs])) } : v)),
    })),

  setFlags: (flags, forText) =>
    set((s) => ({ versions: s.versions.map((v) => (v.id === s.activeId ? { ...v, flags, flagsFor: forText } : v)) })),

  addCombined: (from, text, label) => {
    const id = `combined_${Date.now().toString(36)}`;
    const v: Version = {
      id, createdAt: Date.now(), label, settings: from.settings, original: from.original, result: null, history: [text], index: 0,
      accepted: [], flags: [], flagsFor: "", group: from.group, approach: "combined",
    };
    set((s) => ({ versions: [v, ...s.versions].slice(0, MAX_VERSIONS), activeId: id, view: "review" }));
    return id;
  },
}));

export function useActiveVersion(): Version | null {
  return useHumanizer((s) => s.versions.find((v) => v.id === s.activeId) ?? null);
}
