export type StageState = "pending" | "active" | "completed" | "failed" | "skipped";

export interface Stage {
  key: string;
  label: string;
  state: StageState;
  detail: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface StructureSummary {
  pages: number;
  page_source: "rendered" | "estimated";
  word_metadata_pages?: number | null;
  paragraphs: number;
  paragraphs_total: number;
  words: number;
  headings: number;
  probable_headings: number;
  list_items: number;
  tables: number;
  nested_tables: number;
  images: number;
  hyperlinks: number;
  sections: number;
  headers: number;
  footers: number;
  footnotes: number;
  endnotes: number;
  references: number;
  field_citations: number;
  textboxes: number;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  status: "uploaded" | "processing" | "ready" | "failed" | "cancelled" | "interrupted";
  size_bytes: number;
  sha256?: string;
  created_at: number;
  updated_at: number;
  revision_head?: number;
  error?: string | null;
  running?: boolean;
  stages?: Stage[];
  summary?: {
    validation?: { checks: { name: string; ok: boolean; detail: string }[]; warnings: string[] };
    structure?: StructureSummary;
    warnings?: string[];
    metadata?: Record<string, string | number>;
    counts?: DocCounts;
  };
}

export interface DocCounts {
  paragraphs: number;
  words: number;
  pages: number;
  findings: Record<string, number>;
  suggestions: Record<string, number>;
}

export interface DocumentListItem {
  id: string;
  filename: string;
  status: DocumentRecord["status"];
  size_bytes: number;
  created_at: number;
  updated_at: number;
  error: string | null;
  pending: number;
  accepted: number;
  rejected: number;
  findings: number;
  exports: number;
  running: boolean;
  summary: DocCounts | null;
}

export interface FormatSpan {
  s: number;
  e: number;
  f: { b?: boolean; i?: boolean; u?: boolean; s?: boolean; va?: "sup" | "sub" };
}

export interface Paragraph {
  id: string;
  part: "body" | "header" | "footer" | "footnotes" | "endnotes";
  part_name: string;
  note_id: string | null;
  kind: "paragraph" | "heading" | "title" | "list_item" | "caption" | "toc" | "empty" | "object";
  probable_heading: boolean;
  style_id: string | null;
  style_name: string | null;
  heading_level: number | null;
  list: { num_id: string | null; level: number; format: string } | null;
  alignment: string | null;
  table: { table_id: string; row: number; col: number }[] | null;
  text: string;
  original_text: string;
  modified: boolean;
  sentences: { id: string; start: number; end: number }[];
  word_count: number;
  formatting: FormatSpan[];
  objects: { offset: number; kind: string }[];
  page: number | null;
  order: number;
  is_reference_entry?: boolean;
  findings: Record<string, number>;
  suggestions: Record<string, number>;
}

export interface DocumentStructure {
  document: { id: string; filename: string; status: string; revision_head: number };
  metadata: Record<string, string | number>;
  summary: StructureSummary;
  sections: { index: number; orientation: string; columns: number; page_width_twips?: number | null; page_height_twips?: number | null }[];
  paragraphs: Paragraph[];
  tables: { id: string; rows: number; cols: number; nested_depth: number; paragraph_ids: string[] }[];
  images: { id: string; paragraph_id: string; description: string | null }[];
  hyperlinks: { paragraph_id: string; text: string; url: string | null }[];
  references: { paragraph_id: string; text: string }[];
  warnings: string[];
  ocr?: { image_id: string; paragraph_id: string; text: string; words: number }[];
}

export type Module = "writing" | "similarity" | "ai_patterns" | "readability" | "academic" | "formatting";
export type Priority = "high" | "medium" | "low";

export interface Finding {
  id: string;
  document_id: string;
  paragraph_id: string | null;
  sentence_id: string | null;
  module: Module;
  category: string;
  passage: string | null;
  start: number | null;
  end: number | null;
  explanation: string;
  correction: string | null;
  priority: Priority;
  confidence: number | null;
  source: string;
  status: string;
  data: Record<string, unknown> & {
    label?: string;
    method?: string;
    source?: { title?: string; url?: string | null; kind?: string; origin?: string; meta?: Record<string, unknown> };
    matching_passage?: string;
    citation_status?: string;
    recommendations?: string[];
    coverage?: number | null;
    longest_run?: number;
    needs_verification?: boolean;
    other_paragraph_id?: string;
    overlap_spans?: [number, number][];
  };
}

export type SuggestionStatus =
  | "pending" | "accepted" | "rejected" | "generating" | "incomplete" | "failed" | "stale" | "superseded" | "reverted";

export interface Suggestion {
  id: string;
  document_id: string;
  paragraph_id: string;
  sentence_id: string | null;
  finding_id: string | null;
  group_id: string;
  scope: "span" | "sentence" | "paragraph";
  action: string;
  mode: string | null;
  base_text: string;
  original_text: string;
  proposed_text: string;
  span_start: number | null;
  span_end: number | null;
  category: string;
  explanation: string | null;
  reason: string | null;
  meaning: { level: "likely_preserved" | "review" | "possible_change"; label: string; warnings: string[]; method: string } | null;
  verification: { required: boolean; items: string[] } | null;
  status: SuggestionStatus;
  reviewed: number;
  source: string;
  model: string | null;
  priority: Priority;
  created_at: number;
  updated_at: number;
}

export interface ProcessingEvent {
  seq: number;
  type: string;
  document_id: string;
  ts: number;
  data: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface VisualParagraph {
  id: string;
  part: string;
  kind: Paragraph["kind"];
  heading_level: number | null;
  list: Paragraph["list"];
  table: Paragraph["table"];
  page: number | null;
  text: string;
  words: [number, number][];
  sentences: [number, number][];
  objects: { offset: number; kind: string }[];
  alignment: string | null;
  formatting: FormatSpan[];
  probable_heading: boolean;
}

export interface OllamaModel {
  name: string;
  size_bytes: number;
  family: string | null;
  parameter_size: string | null;
  quantization: string | null;
  is_embedding: boolean;
}

export interface SystemInfo {
  ollama: {
    available: boolean;
    url: string;
    version?: string;
    error?: string;
    models: OllamaModel[];
    loaded: { name: string; size_vram: number }[];
  };
  recommended_models: { name: string; role: string; size_gb: number; ram_gb: number; notes: string }[];
  storage: { data_dir: string; bytes: number; files: number; documents: number };
  corpus: { sources: number; chunks: number; words: number; embedded_chunks: number; embed_models: string[] };
  ocr_available: boolean;
  external_providers: Record<string, string>;
  settings: Settings;
}

export interface Settings {
  ollama: { model: string | null; embed_model: string | null; temperature: number; num_ctx: number; keep_alive: string };
  writing: { tone: string; intensity: "light" | "balanced" | "extensive"; custom_instructions: string; language: string; preserve_style: boolean };
  analysis: { llm_review: boolean; llm_review_max_paragraphs: number; spellcheck: boolean; similarity: boolean; semantic_similarity: boolean; ocr: boolean; ai_detection: boolean };
  document: { preview_zoom: number; autosave: boolean; scan_animation: boolean };
  privacy: { external_sources: boolean; external_providers: string[] };
}

export interface RevisionOp {
  document_id: string;
  seq: number;
  op_type: "accept" | "reject" | "manual" | "restore";
  paragraph_id: string | null;
  before_text: string | null;
  after_text: string | null;
  suggestion_id: string | null;
  prev_status: string | null;
  new_status: string | null;
  active: number;
  note: string | null;
  created_at: number;
  state: "applied" | "undone" | "discarded";
}

export interface RevisionHistory {
  head: number;
  ops: RevisionOp[];
  can_undo: boolean;
  can_redo: boolean;
}

export interface ExportReport {
  ok: boolean;
  message?: string;
  export_id: string;
  filename: string;
  size_bytes: number;
  paragraphs_changed: number;
  applied: { paragraph_id: string; strategy: string; runs_touched: number; notes: string[] }[];
  failed: { paragraph_id: string; reason: string }[];
  suggestions: { accepted: number; rejected: number; pending: number; incomplete: number };
  manual_edits: number;
  unresolved_findings: Record<string, number>;
  needs_verification: { suggestion_id: string; paragraph_id: string; category: string; items: string[] }[];
  validation: { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] };
  warnings: string[];
}

export interface CorpusSource {
  id: string;
  title: string;
  kind: string;
  origin: string;
  chunk_count: number;
  word_count: number;
  embed_model: string | null;
  embedded: boolean;
  added_at: number;
  meta: Record<string, unknown>;
}
