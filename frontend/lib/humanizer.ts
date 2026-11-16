import { API_URL, ApiError, postSSE } from "./api";

export type Mode = "natural" | "conversational" | "academic" | "professional" | "simple" | "personal" | "custom";
export type Intensity = "light" | "medium" | "extensive";
export type StageKey = "reading" | "analyzing" | "context" | "rewriting" | "vocabulary" | "style" | "grammar" | "validating" | "finalizing";
export type Approach = "balanced" | "restructured" | "concise" | "flowing";
export type Tone = "formal" | "friendly" | "confident" | "neutral" | "persuasive" | "empathetic" | "casual";
export type StageState = "pending" | "active" | "completed" | "skipped" | "failed";
export type SelectionAction =
  | "improve" | "natural" | "simplify" | "professional" | "conversational" | "clarity" | "redundancy" | "rephrase" | "expand" | "shorten"
  | "flow" | "tone" | "custom";

export interface HumanizeOptions {
  modes: { value: Mode; label: string; hint: string }[];
  intensities: { value: Intensity; label: string; hint: string }[];
  document_types: string[];
  selection_actions: { value: SelectionAction; label: string }[];
  approaches: { value: Approach; label: string; hint: string }[];
  tones: { value: Tone; label: string; hint: string }[];
  max_variations: number;
  stages: { key: StageKey; label: string }[];
  provider: { name: string; url: string; local: boolean; available: boolean; model: string | null; model_ready: boolean; error?: string };
  speed: { fast_model: string | null; recommended_fast_model: string; thorough_model: string | null };
  upload: { extensions: string[]; max_mb: number; ocr_available: boolean };
  limits: { max_chars: number; max_selection_chars: number; min_profile_words: number };
  privacy: string;
}

export interface RunSettings {
  mode: Mode;
  intensity: Intensity;
  profile_id: string | null;
  custom_instructions: string;
  document_type: string;
  audience: string;
  ai_validation: boolean;
  /** fast: a model without a reasoning phase (or reasoning off); thorough: the Settings model with reasoning. */
  speed: Speed;
  /** One rewrite per approach (1–3); each becomes its own version that can be compared and combined. */
  approaches: Approach[];
}

export type Speed = "fast" | "thorough";

export interface Flag {
  id?: string;
  block_id: number | null;
  type: string;
  severity: "high" | "medium" | "low";
  source: "automated" | "ai" | "pipeline";
  point?: string;
  message: string;
  original_excerpt: string;
  revised_excerpt: string;
  grounded?: boolean;
}

export interface Issue {
  id: string;
  label: string;
  severity: "high" | "medium" | "low";
  detail: string;
  examples: string[];
  guidance?: string | null;
}

export interface Metrics {
  words: number;
  sentences: number;
  paragraphs?: number;
  avg_sentence_length: number;
  sentence_length_sd: number;
  sentence_length_variation: number;
  shortest_sentence?: number;
  longest_sentence: number;
  sentence_length_histogram?: { label: string; count: number }[];
  avg_paragraph_length?: number;
  lexical_variety: number | null;
  formality: number | null;
  transition_rate: number;
  passive_constructions: number;
  flesch_reading_ease: number | null;
  flesch_kincaid_grade: number | null;
  readability_band: string | null;
}

export interface WritingProfile {
  words: number;
  sentences: number;
  metrics: Metrics;
  issues: Issue[];
  guidance: string[];
  method?: string;
}

export interface DocContext {
  document_type?: string | null;
  audience?: string | null;
  purpose?: string;
  tone?: string;
  main_points?: string[];
  key_terms?: string[];
  claims_to_preserve?: string[];
  context_notes?: string[];
  source: "ai" | "automatic";
  /** Points each block makes (AI semantic map; only points grounded in the text are kept). */
  semantic_map?: Record<string, { points: string[]; role: string | null; dropped: number }>;
  /** Deterministic meaning ledger per block: qualifiers, negations, logical links and numbers to keep. */
  ledger?: Record<string, { qualifiers: string[]; negations: string[]; links: { kind: string; text: string }[]; numbers: string[] }>;
}

export interface TrailEntry { stage: string; status: "applied" | "rejected" | "auto" | "reverted"; items: string[]; reason?: string }

export interface ResultBlock {
  id: number; kind: string; rewritable: boolean; original: string; revised: string; changed: boolean;
  part_of?: number | null; prefix: string; sep: string; trail: TrailEntry[];
}

export interface StageReport {
  blocks?: number; flagged?: number; applied?: number; rejected?: number; calls?: number; examples?: string[]; flow_fixed?: number;
  checked?: number; changed?: number; repair_attempted?: number; repaired?: number; reverted?: number;
}

export interface Usage {
  calls: number; prompt_tokens: number; output_tokens: number; seconds: number;
  by_stage: Record<string, { calls: number; prompt_tokens: number; output_tokens: number; seconds: number }>;
  models: Record<string, number>;
}

export interface Validation {
  checked_blocks: number; changed_blocks: number; flags_by_severity: Record<string, number>; ai_checked: boolean; ai_note: string | null;
  statement: string; repaired?: number; reverted?: number; repair_attempted?: number;
}

export interface VariantResult {
  approach: Approach;
  label: string;
  output: string;
  blocks: ResultBlock[];
  metrics_after: Metrics;
  issues_after: { id: string; label: string; severity: string }[];
  flags: Flag[];
  notes: Flag[];
  validation: Validation;
  stage_report: Record<string, StageReport>;
  change_ratio: number;

}

export interface RunResult {
  original: string;
  output: string;
  blocks: ResultBlock[];
  profile_before: WritingProfile;
  metrics_after: Metrics;
  issues_after: { id: string; label: string; severity: string }[];
  context: DocContext;
  flags: Flag[];
  notes: Flag[];
  validation: Validation;
  stage_report: Record<string, StageReport>;
  variants: VariantResult[];
  settings: RunSettings;
  model: string;
  duration_s: number;
  sections: number;
  usage: Usage;
  llm_calls: number;
  /** Set on a version's result when it comes from one variation of a run. */
  approach?: Approach;
  label?: string;
  change_ratio?: number;
}

export interface StyleProfile {
  id: string;
  name: string;
  description: string;
  traits: string[];
  expressions: string[];
  stats: Record<string, unknown>;
  sample_words: number;
  created_at: number;
  updated_at: number;
}

export interface ProfileDraft {
  stats: Record<string, unknown>;
  summary: string[];
  expressions: string[];
  description: string;
  traits: string[];
  ai_used: boolean;
  ai_error: string | null;
  sample_words: number;
}

export interface Extracted {
  text: string;
  source: "text" | "docx" | "pdf" | "ocr";
  filename: string;
  warnings: string[];
  characters: number;
  words: number;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export interface VariantTag { variant?: number; variants?: number; approach?: Approach }

export interface PlanSection { index: number; words: number; heading: string | null; preview: string }

export type RunEvent =
  | { type: "accepted"; run_id: string }
  | { type: "started"; stages: { key: StageKey; label: string }[]; model: string; provider: { name: string; url: string }; variants?: { approach: Approach; label: string }[] }
  | ({ type: "stage"; stage: StageKey; state: StageState; detail: string | null; progress?: { done: number; total: number }; context?: DocContext; fallback?: boolean; blocks?: any[] } & VariantTag)
  | ({ type: "activity"; stage: StageKey; activity: "thinking" | "writing" | "retrying"; label?: string; chars?: number; thinking_chars?: number; tail?: string; started?: number; reason?: string; model?: string } & VariantTag)
  | { type: "plan"; sections: PlanSection[]; words: number; context_call: boolean; semantic_calls?: number; flow: boolean; ai_validation: boolean; variants?: number }
  | { type: "profile"; profile: WritingProfile }
  | ({ type: "section"; index: number; total: number; block_ids: number[]; revised: Record<string, string> } & VariantTag)
  | { type: "completed"; result: RunResult }
  | { type: "cancelled" }
  | { type: "error"; message: string; code?: string };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, "The HumanWrite backend is not reachable. Start it with ./start.sh.", "offline");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const b = await res.json();
      msg = typeof b.detail === "string" ? b.detail : Array.isArray(b.detail) ? b.detail.map((x: { msg: string }) => x.msg.replace(/^Value error, /, "")).join("; ") : msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const humanizer = {
  options: () => req<HumanizeOptions>("/api/humanize/options"),
  extract: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Extracted>("/api/humanize/extract", { method: "POST", body: fd });
  },
  analyze: (text: string) => req<WritingProfile>("/api/humanize/analyze", json("POST", { text })),
  validate: (original: string, revised: string, key_terms: string[] = []) =>
    req<{ flags: Flag[]; summary: RunResult["validation"] }>("/api/humanize/validate", json("POST", { original, revised, key_terms })),
  run: (body: RunSettings & { text: string; run_id: string }, onEvent: (ev: RunEvent) => void, signal?: AbortSignal) =>
    postSSE("/api/humanize/run", { ...body, profile_id: body.profile_id || null }, (_e, data) => onEvent(data as RunEvent), signal),
  cancel: (runId: string) => req<{ cancelled: boolean }>(`/api/humanize/runs/${runId}/cancel`, json("POST")),
  rewriteSelection: (
    body: {
      text: string; before: string; after: string; action: SelectionAction; mode: Mode; custom_instruction?: string; profile_id?: string | null;
      audience?: string; tone?: Tone; alternatives?: number;
    },
    onEvent: (event: string, data: any) => void,
    signal?: AbortSignal,
  ) => postSSE("/api/humanize/rewrite-selection", body, onEvent, signal),
  profiles: () => req<StyleProfile[]>("/api/humanize/profiles"),
  analyzeProfile: (samples: string, signal?: AbortSignal) => req<ProfileDraft>("/api/humanize/profiles/analyze", { ...json("POST", { samples }), signal }),
  createProfile: (p: Omit<StyleProfile, "id" | "created_at" | "updated_at">) => req<StyleProfile>("/api/humanize/profiles", json("POST", p)),
  updateProfile: (id: string, patch: Partial<Omit<StyleProfile, "id">>) => req<StyleProfile>(`/api/humanize/profiles/${id}`, json("PATCH", patch)),
  deleteProfile: (id: string) => req<{ ok: boolean }>(`/api/humanize/profiles/${id}`, { method: "DELETE" }),
  exportFile: async (text: string, format: "txt" | "md" | "docx", filename: string) => {
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/humanize/export`, json("POST", { text, format, filename }));
    } catch {
      throw new ApiError(0, "The HumanWrite backend is not reachable.", "offline");
    }
    if (!res.ok) throw new ApiError(res.status, "Export failed.");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${filename}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  },
  detectText: (text: string) => req<import("./api").AiDetectionReport>("/api/ai-detector/analyze-text", json("POST", { text })),
  detectorStatus: () => req<import("./api").DetectorStatus>("/api/ai-detector"),
};
