import { diffWordsWithSpace } from "diff";

export const OBJ = "￼";

/** Protected elements (footnote markers, images, fields) are shown as ⟦n⟧ while editing. */
export function toEditable(text: string): string {
  let n = 0;
  return text.replace(/￼/g, () => `⟦${++n}⟧`);
}

export function fromEditable(text: string): string {
  return text.replace(/⟦\d+⟧/g, OBJ);
}

export function displayText(text: string): string {
  return text.replace(/￼/g, "◆");
}

export type DiffPart = { value: string; added?: boolean; removed?: boolean };

export function wordDiff(a: string, b: string): DiffPart[] {
  return diffWordsWithSpace(a, b) as DiffPart[];
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function timeAgo(ts: number): string {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export const MODULE_LABELS: Record<string, string> = {
  writing: "Writing quality",
  similarity: "Similarity",
  ai_detection: "AI detection",
  ai_patterns: "Writing patterns",
  readability: "Readability",
  academic: "Academic completeness",
  formatting: "Formatting & structure",
};

export const CATEGORY_LABELS: Record<string, string> = {
  grammar: "Grammar",
  spelling: "Spelling",
  punctuation: "Punctuation",
  wordiness: "Wordiness",
  redundancy: "Redundancy",
  filler: "Filler",
  complexity: "Complex sentence",
  repetition: "Repetition",
  terminology: "Terminology",
  awkward_wording: "Awkward wording",
  clarity: "Clarity",
  transition: "Transition",
  vague_statement: "Vague statement",
  tone: "Tone",
  flow: "Flow",
  readability: "Readability",
  paragraph_length: "Long paragraph",
  difficulty: "Dense text",
  stock_phrase: "Stock phrase",
  generic_statement: "Generic statement",
  paragraph_pattern: "Paragraph pattern",
  citation_style: "Citation style",
  missing_reference: "Missing reference",
  missing_reference_list: "No reference list",
  uncited_reference: "Uncited reference",
  reference_format: "Reference format",
  unsupported_claim: "Unsupported claim",
  undefined_term: "Undefined term",
  heading_hierarchy: "Heading hierarchy",
  heading_punctuation: "Heading punctuation",
  manual_heading: "Manual heading",
  manual_list: "Manual list",
  empty_paragraphs: "Empty paragraphs",
  no_headings: "No headings",
  exact: "Exact match",
  near_verbatim: "Near-verbatim",
  similar_phrasing: "Similar phrasing",
  semantic: "Semantic similarity",
  internal: "Internal duplication",
  quoted: "Quoted passage",
};

export const label = (c: string) => CATEGORY_LABELS[c] ?? c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
