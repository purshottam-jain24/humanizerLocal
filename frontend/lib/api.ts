import type {
  CorpusSource, DocumentListItem, DocumentRecord, DocumentStructure, ExportReport, Finding, RevisionHistory,
  Settings, Suggestion, SystemInfo,
} from "./types";

/** The FastAPI backend runs locally. Requests go straight to it (no proxy) so SSE streams are not buffered. */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(0, "The HumanWrite backend is not reachable. Start it with ./scripts/dev.sh (or see README).", "offline");
  }
  if (!res.ok) {
    let msg = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      const d = body.detail;
      if (typeof d === "string") msg = d;
      else if (d?.message) {
        msg = d.message;
        code = d.code;
      } else if (Array.isArray(d)) msg = d.map((x: { msg: string }) => x.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg, code);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  system: () => request<SystemInfo>("/api/system"),
  settings: () => request<Settings>("/api/settings"),
  updateSettings: (patch: Partial<{ [K in keyof Settings]: Partial<Settings[K]> }>) => request<Settings>("/api/settings", json("PATCH", patch)),

  documents: () => request<DocumentListItem[]>("/api/documents"),
  document: (id: string) => request<DocumentRecord>(`/api/documents/${id}`),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<DocumentRecord>("/api/documents", { method: "POST", body: fd });
  },
  deleteDocument: (id: string) => request<{ ok: boolean }>(`/api/documents/${id}`, { method: "DELETE" }),
  process: (id: string, body: { restart?: boolean; llm_review?: boolean } = {}) =>
    request<{ started: boolean }>(`/api/documents/${id}/process`, json("POST", body)),
  cancel: (id: string) => request<{ cancelled: boolean }>(`/api/documents/${id}/cancel`, json("POST")),
  structure: (id: string) => request<DocumentStructure>(`/api/documents/${id}/structure`),
  analysis: (id: string) => request<AnalysisResponse>(`/api/documents/${id}/analysis`),
  findings: (id: string, params: Record<string, string> = {}) =>
    request<Finding[]>(`/api/documents/${id}/findings?${new URLSearchParams(params)}`),
  patchFinding: (fid: string, status: "open" | "dismissed" | "resolved") => request(`/api/findings/${fid}`, json("PATCH", { status })),
  suggestions: (id: string, params: Record<string, string> = {}) =>
    request<Suggestion[]>(`/api/documents/${id}/suggestions?${new URLSearchParams(params)}`),
  accept: (id: string, sid: string, edited_text?: string) =>
    request<{ paragraph_id: string; text: string; applied_as: string }>(`/api/documents/${id}/suggestions/${sid}/accept`, json("POST", { edited_text })),
  reject: (id: string, sid: string) => request(`/api/documents/${id}/suggestions/${sid}/reject`, json("POST")),
  patchSuggestion: (id: string, sid: string, body: { proposed_text?: string; reviewed?: boolean }) =>
    request<Suggestion>(`/api/documents/${id}/suggestions/${sid}`, json("PATCH", body)),
  batch: (id: string, ids: string[], action: "accept" | "reject", include_verification_required = false) =>
    request<{ done: string[]; skipped: { id: string; reason: string }[] }>(`/api/documents/${id}/suggestions/batch`,
      json("POST", { ids, action, include_verification_required })),
  cancelGeneration: (genId: string) => request<{ cancelled: boolean }>(`/api/generations/${genId}/cancel`, json("POST")),
  editParagraph: (id: string, pid: string, text: string) => request<{ text: string }>(`/api/documents/${id}/paragraphs/${pid}/edit`, json("POST", { text })),
  checkParagraph: (id: string, pid: string, text: string) =>
    request<{ safe: boolean; reason: string | null }>(`/api/documents/${id}/paragraphs/${pid}/check`, json("POST", { text })),
  restoreParagraph: (id: string, pid: string) => request(`/api/documents/${id}/paragraphs/${pid}/restore`, json("POST")),
  paragraphHistory: (id: string, pid: string) => request<RevisionHistory["ops"]>(`/api/documents/${id}/paragraphs/${pid}/history`),
  revisions: (id: string) => request<RevisionHistory>(`/api/documents/${id}/revisions`),
  undo: (id: string) => request<{ can_undo: boolean; can_redo: boolean; op: unknown }>(`/api/documents/${id}/undo`, json("POST")),
  redo: (id: string) => request<{ can_undo: boolean; can_redo: boolean; op: unknown }>(`/api/documents/${id}/redo`, json("POST")),
  reanalyze: (id: string, paragraph_ids?: string[]) =>
    request<{ paragraphs: number; findings: number; suggestions: number }>(`/api/documents/${id}/reanalyze`, json("POST", { paragraph_ids })),
  exportDoc: (id: string) => request<ExportReport>(`/api/documents/${id}/export`, json("POST")),
  exports: (id: string) => request<{ id: string; filename: string; created_at: number; paragraphs_changed: number; validation_ok: boolean; report: ExportReport }[]>(`/api/documents/${id}/exports`),
  previewUrl: (id: string, version: "current" | "original" = "current") => `${API_URL}/api/documents/${id}/preview.docx?version=${version}`,
  originalUrl: (id: string) => `${API_URL}/api/documents/${id}/original.docx`,
  downloadUrl: (exportId: string) => `${API_URL}/api/exports/${exportId}/download`,

  corpus: () => request<{ sources: CorpusSource[]; stats: SystemInfo["corpus"] }>("/api/corpus"),
  corpusUpload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return request<{ imported: CorpusSource[]; failed: { file: string; error: string }[] }>("/api/corpus/upload", { method: "POST", body: fd });
  },
  corpusFolder: (path: string) => request<{ imported: CorpusSource[]; failed: { file: string; error: string }[] }>("/api/corpus/folder", json("POST", { path })),
  corpusDelete: (sid: string) => request(`/api/corpus/${sid}`, { method: "DELETE" }),

  testModel: (model: string) => request<{ ok: boolean; error?: string; reply?: string; first_token_s?: number; total_s?: number; tokens_per_s?: number }>("/api/ollama/test", json("POST", { model })),
  deleteModel: (name: string) => request(`/api/ollama/models/${encodeURIComponent(name)}`, { method: "DELETE" }),
  unloadModel: (model: string) => request("/api/ollama/unload", json("POST", { model })),
  detectorStatus: () => request<DetectorStatus>("/api/ai-detector"),
  removeDetector: () => request<DetectorStatus>("/api/ai-detector", { method: "DELETE" }),
  runDetection: (id: string) => request<AiDetectionReport>(`/api/documents/${id}/ai-detection`, json("POST")),
  clearHistory: () => request<{ deleted: number }>("/api/privacy/clear-history", json("POST")),
  privacyReport: () => request<{ local_only: boolean; ai_runtime: string; leaves_device: string[]; never_leaves_device: string[] }>("/api/privacy/report"),
};

export interface DetectorStatus {
  ready: boolean;
  libraries_installed: boolean;
  models_installed: boolean;
  installing: boolean;
  download_mb: number;
  upgrade_available?: boolean;
  upgrade_mb?: number;
  method_version?: number;
  disk_mb: number;
  method: string;
  model: string;
  device: string | null;
}

export type AiLabel = "ai" | "likely_ai" | "possible" | "likely_human" | "human";

export interface AiDetectionReport {
  available: boolean;
  reason?: string;
  method?: string;
  model?: string;
  ai_percent?: number | null;
  human_percent?: number | null;
  possible_percent?: number | null;
  overall_probability?: number | null;
  verdict?: "ai" | "mixed" | "human" | "insufficient";
  words_total?: number;
  words_scored?: number;
  segments?: { paragraph_ids: string[]; words: number; score: number; probability: number; label: AiLabel; preview: string }[];
  paragraphs?: Record<string, { probability: number; label: AiLabel }>;
  duration_s?: number;
  analyzed_at?: number;
  text_version?: "original" | "current";
}

export interface AnalysisResponse {
  reports: {
    ai_detection?: AiDetectionReport;
    readability?: { overall: Record<string, unknown> & { sentence_length_histogram?: { label: string; count: number }[] }; paragraphs: unknown[] };
    ai_patterns?: {
      level: string;
      words_analyzed: number;
      signals: { id: string; label: string; value: number; detail: string; observed: boolean; explanation: string }[];
      paragraphs: { paragraph_id: string; observations: string[]; level: string }[];
      disclaimer: string[];
      method: string;
    };
    academic?: Record<string, unknown> & { citation_styles?: Record<string, number> };
    similarity?: {
      local?: { checked: boolean; reason?: string; corpus?: SystemInfo["corpus"]; methods?: string[] };
      internal_matches?: number;
      semantic_note?: string | null;
      external?: { checked: boolean; reason?: string; providers?: string[]; queries_sent?: number; coverage_note?: string; error?: string };
    };
  };
  finding_counts: { module: string; category: string; priority: string; status: string; n: number }[];
  suggestion_counts: { category: string; status: string; n: number }[];
  summary: DocumentRecord["summary"];
  status: string;
  stages: DocumentRecord["stages"];
}

/** POST an endpoint that answers with Server-Sent Events and dispatch each event. */
export async function postSSE(
  path: string,
  body: unknown,
  onEvent: (event: string, data: any) => void, // eslint-disable-line @typescript-eslint/no-explicit-any
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try {
      const b = await res.json();
      msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
    }
  }
}
