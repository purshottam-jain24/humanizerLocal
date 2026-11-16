import type { ProcessingEvent, Stage, VisualParagraph } from "@/lib/types";

/**
 * Turns real backend processing events into a paced visualisation.
 *
 * - "truth" holds the actual backend state (counts, stages) and updates the
 *   instant an event arrives.
 * - The visual cursor only advances through data that has really arrived;
 *   if the backend is faster than the animation, events wait in the queue
 *   (backend work is never delayed); if the backend is slower, the
 *   animation waits for it.
 */

export type ParaVisualState = "pending" | "recognizing" | "recognized" | "analyzing" | "analyzed";

interface AnalysisStep {
  pid: string;
  stage: "rules" | "ai_review";
  started: boolean;
  completed: boolean;
  findings?: number;
}

export interface ActivityItem {
  id: number;
  text: string;
  tone?: "info" | "ok" | "warn" | "error";
}

const STAGE_KEYS = ["upload", "validate", "structure", "text", "ocr", "elements", "paragraphs", "originality", "suggestions", "workspace"];

const FINISH_WITHIN_MS = 2500;

export class ScanEngine {
  // ------------- truth (actual backend values)
  stages: Stage[] = [];
  truth = {
    pagesProcessed: 0,
    paragraphsExtracted: 0,
    wordsExtracted: 0,
    headings: 0,
    tables: 0,
    images: 0,
    references: 0,
    paragraphsAnalyzed: 0,
    aiReviewed: 0,
    aiTotal: 0,
    analysisTotal: 0,
    findings: 0,
    suggestions: 0,
    ocrWords: 0,
  };
  pageSource: string | null = null;
  totalPages: number | null = null;
  status: "live" | "completed" | "failed" | "cancelled" = "live";
  failure: string | null = null;
  activity: ActivityItem[] = [];
  outline: { id: string; text: string; level: number | null; page: number | null }[] = [];
  lastSeq = 0;
  validation: { name: string; ok: boolean; detail: string }[] = [];

  // ------------- data
  paragraphs: VisualParagraph[] = [];
  byId = new Map<string, VisualParagraph>();
  pages = new Map<number, string[]>();
  private analysisQueue: AnalysisStep[] = [];
  private aqIndex = new Map<string, AnalysisStep>();

  // ------------- visual cursor
  vState = new Map<string, ParaVisualState>();
  recogIdx = 0; // index into paragraphs (body only)
  wordIdx = 0;
  private wordBudget = 0;
  aqPos = 0;
  sentenceIdx = 0;
  private sentenceTimer = 0;
  flashSentence: { pid: string; idx: number; at: number } | null = null;
  boundaryFlash: { pid: string; at: number } | null = null;
  instant = false;
  private doneAt: number | null = null;
  paused = false;
  dirty = true;
  private actSeq = 0;
  private extractionDone = false;
  now = 0;

  get bodyParagraphs() {
    return this.paragraphs;
  }

  get activePid(): string | null {
    if (this.recogIdx < this.paragraphs.length) return this.paragraphs[this.recogIdx].id;
    const step = this.analysisQueue[this.aqPos];
    return step ? step.pid : null;
  }

  get phase(): "waiting" | "recognizing" | "analyzing" | "ai_review" | "done" {
    if (this.recogIdx < this.paragraphs.length) return "recognizing";
    const step = this.analysisQueue[this.aqPos];
    if (step) return step.stage === "ai_review" ? "ai_review" : "analyzing";
    if (this.status !== "live") return "done";
    return "waiting";
  }

  get visualBacklog() {
    let words = 0;
    for (let i = this.recogIdx; i < this.paragraphs.length; i++) words += this.paragraphs[i].words.length;
    return { words: Math.max(0, words - this.wordIdx), steps: this.analysisQueue.length - this.aqPos };
  }

  get visualComplete() {
    return this.recogIdx >= this.paragraphs.length && this.aqPos >= this.analysisQueue.length && this.status !== "live";
  }

  private log(text: string, tone?: ActivityItem["tone"]) {
    this.activity.unshift({ id: ++this.actSeq, text, tone });
    if (this.activity.length > 40) this.activity.length = 40;
  }

  setStages(stages: Stage[]) {
    this.stages = stages.map((s) => ({ ...s }));
    this.dirty = true;
  }

  // ------------------------------------------------------------ ingest
  ingest(ev: ProcessingEvent) {
    if (ev.seq && ev.seq <= this.lastSeq) return; // never apply an event twice (reconnects)
    if (ev.seq) this.lastSeq = ev.seq;
    const d = ev.data;
    this.dirty = true;
    switch (ev.type) {
      case "document.uploaded":
        this.log(`Received ${d.filename} (${Math.round(d.size_bytes / 1024)} KB)`);
        break;
      case "document.validated":
        this.validation = d.checks;
        this.log(`DOCX validated — ${d.checks.length} checks passed`, "ok");
        for (const w of d.warnings ?? []) this.log(w, "warn");
        break;
      case "document.stage.changed": {
        const s = this.stages.find((x) => x.key === d.stage);
        if (s) {
          s.state = d.state;
          if (d.detail) s.detail = d.detail;
        } else if (STAGE_KEYS.includes(d.stage)) {
          this.stages.push({ key: d.stage, label: d.stage, state: d.state, detail: d.detail, started_at: null, finished_at: null });
        }
        if (d.stage === "text" && d.state === "completed") this.extractionDone = true;
        if (d.state === "active") this.log(`${s?.label ?? d.stage}…`);
        if (d.state === "skipped") this.log(`${s?.label ?? d.stage}: skipped (${d.detail})`);
        break;
      }
      case "document.structure.detected":
        if (d.phase === "package") this.log(`Package parts: ${(d.parts as string[]).length} text parts · ${d.styles} styles`);
        if (d.phase === "elements") {
          const s = d.summary;
          this.truth.headings = s.headings;
          this.truth.tables = s.tables;
          this.truth.images = s.images;
          this.truth.references = s.references;
          this.pageSource = s.page_source;
          this.totalPages = s.pages;
          this.outline = d.headings;
          this.log(`Detected ${s.headings} headings, ${s.tables} tables, ${s.images} images, ${s.references} references`, "ok");
        }
        break;
      case "document.text.extracted": {
        const p = d.paragraph as VisualParagraph;
        this.truth.paragraphsExtracted = d.totals.paragraphs;
        this.truth.wordsExtracted = d.totals.words;
        if (p.kind === "heading" || p.kind === "title") this.truth.headings = Math.max(this.truth.headings, this.outline.length);
        if (p.part === "body") {
          this.paragraphs.push(p);
          this.byId.set(p.id, p);
          this.vState.set(p.id, "pending");
        }
        break;
      }
      case "document.page.started": {
        this.pages.set(d.page, d.paragraph_ids);
        for (const pid of d.paragraph_ids as string[]) {
          const p = this.byId.get(pid);
          if (p) p.page = d.page;
        }
        break;
      }
      case "document.page.completed":
        this.truth.pagesProcessed = Math.max(this.truth.pagesProcessed, d.page);
        break;
      case "document.ocr.word.recognized":
        this.truth.ocrWords += 1;
        break;
      case "document.paragraph.started": {
        const stage = d.stage === "ai_review" ? "ai_review" : "rules";
        if (stage === "rules") this.truth.analysisTotal = d.total;
        else this.truth.aiTotal = d.total;
        if (!this.byId.has(d.paragraph_id)) break;
        const key = `${stage}:${d.paragraph_id}`;
        if (!this.aqIndex.has(key)) {
          const step: AnalysisStep = { pid: d.paragraph_id, stage, started: true, completed: false };
          this.aqIndex.set(key, step);
          this.analysisQueue.push(step);
        }
        break;
      }
      case "document.paragraph.completed": {
        const stage = d.stage === "ai_review" ? "ai_review" : "rules";
        if (stage === "rules") this.truth.paragraphsAnalyzed = d.index + 1;
        else this.truth.aiReviewed = d.index + 1;
        const step = this.aqIndex.get(`${stage}:${d.paragraph_id}`);
        if (step) {
          step.completed = true;
          step.findings = d.findings;
        }
        break;
      }
      case "document.analysis.started":
        this.log(`Analysis: ${moduleLabel(d.module)}${d.method === "ai_review" ? ` with ${d.model}` : ""}${d.method === "external" ? " (external sources)" : ""}`);
        break;
      case "document.analysis.completed":
        this.log(`${moduleLabel(d.module)}${d.method === "ai_review" ? " (AI review)" : ""}: ${d.findings ?? 0} finding(s)`, "ok");
        break;
      case "document.finding.created":
        this.truth.findings += 1;
        break;
      case "document.suggestion.completed":
        this.truth.suggestions += 1;
        break;
      case "document.processing.completed":
        this.status = "completed";
        this.log("Processing complete — workspace ready", "ok");
        break;
      case "document.processing.cancelled":
        this.status = "cancelled";
        this.log("Processing cancelled", "warn");
        break;
      case "document.processing.failed":
        this.status = "failed";
        this.failure = d.message;
        this.log(`Failed: ${d.message}`, "error");
        break;
    }
  }

  // ------------------------------------------------------------ pacing
  tick(dtMs: number, now: number) {
    this.now = now;
    if (this.paused) return;
    if (this.instant) {
      this.flush();
      return;
    }
    // Once the backend is done, the replay may only take a short beat more: on long documents the
    // sentence-by-sentence animation would otherwise keep the person waiting for minutes.
    if (this.status !== "live") {
      if (this.doneAt === null) this.doneAt = now;
      if (now - this.doneAt > FINISH_WITHIN_MS) {
        this.flush();
        return;
      }
      dtMs *= 8;
    }
    // Recognition phase
    if (this.recogIdx < this.paragraphs.length) {
      const backlog = this.visualBacklog.words;
      const wps = 70 * (1 + backlog / 900); // words per second, speeds up with backlog
      this.wordBudget += (dtMs / 1000) * wps;
      while (this.wordBudget >= 1 && this.recogIdx < this.paragraphs.length) {
        const p = this.paragraphs[this.recogIdx];
        this.vState.set(p.id, "recognizing");
        if (p.words.length === 0) {
          this.finishRecognition(p, now);
          this.wordBudget -= 0.5;
          continue;
        }
        this.wordIdx += 1;
        this.wordBudget -= 1;
        this.maybeFlashSentence(p, now);
        if (this.wordIdx >= p.words.length) this.finishRecognition(p, now);
      }
      this.dirty = true;
      return;
    }
    this.wordBudget = 0;
    // Analysis phase — only advances through steps the backend has started.
    const step = this.analysisQueue[this.aqPos];
    if (!step) return;
    const p = this.byId.get(step.pid);
    if (!p) {
      this.aqPos++;
      return;
    }
    this.vState.set(p.id, "analyzing");
    const backlog = this.analysisQueue.length - this.aqPos;
    const perSentence = step.stage === "ai_review" ? 260 : Math.max(18, 160 / (1 + backlog / 25));
    this.sentenceTimer += dtMs;
    const nS = Math.max(1, p.sentences.length);
    while (this.sentenceTimer >= perSentence && this.sentenceIdx < nS) {
      this.sentenceTimer -= perSentence;
      this.sentenceIdx++;
    }
    if (this.sentenceIdx >= nS) {
      if (step.completed) {
        this.vState.set(p.id, "analyzed");
        this.aqPos++;
        this.sentenceIdx = 0;
        this.sentenceTimer = 0;
      } else if (step.stage === "ai_review") {
        // The local model is still working on this paragraph: keep sweeping, never mark it complete early.
        this.sentenceIdx = 0;
      } else {
        this.sentenceIdx = nS;
      }
    }
    this.dirty = true;
  }

  private finishRecognition(p: VisualParagraph, now: number) {
    this.vState.set(p.id, "recognized");
    this.boundaryFlash = { pid: p.id, at: now };
    this.recogIdx++;
    this.wordIdx = 0;
  }

  private maybeFlashSentence(p: VisualParagraph, now: number) {
    const w = p.words[this.wordIdx - 1];
    if (!w) return;
    const sIdx = p.sentences.findIndex(([, e]) => w[1] >= e - 1 && w[1] <= e);
    if (sIdx >= 0) this.flashSentence = { pid: p.id, idx: sIdx, at: now };
  }

  togglePause() {
    this.paused = !this.paused;
    this.dirty = true;
  }

  /** Stop animating: from now on the view shows the live backend state directly. */
  skipAnimation() {
    this.instant = true;
    this.dirty = true;
  }

  /** Jump the visualisation to the real backend state. */
  flush() {
    for (let i = this.recogIdx; i < this.paragraphs.length; i++) this.vState.set(this.paragraphs[i].id, "recognized");
    this.recogIdx = this.paragraphs.length;
    this.wordIdx = 0;
    while (this.aqPos < this.analysisQueue.length) {
      const s = this.analysisQueue[this.aqPos];
      if (!s.completed) {
        this.vState.set(s.pid, "analyzing");
        break;
      }
      this.vState.set(s.pid, "analyzed");
      this.aqPos++;
    }
    this.sentenceIdx = 0;
    this.dirty = true;
  }

  pageOf(pid: string | null): number {
    if (!pid) return 1;
    return this.byId.get(pid)?.page ?? 1;
  }

  pageState(page: number): "pending" | "active" | "recognized" | "analyzed" {
    const ids = this.pages.get(page) ?? this.paragraphs.filter((p) => p.page === page).map((p) => p.id);
    if (!ids.length) return "pending";
    const states = ids.map((i) => this.vState.get(i));
    if (states.every((s) => s === "analyzed")) return "analyzed";
    if (states.includes("recognizing") || states.includes("analyzing")) return "active";
    if (states.every((s) => s === "recognized" || s === "analyzed")) return "recognized";
    return states.some((s) => s && s !== "pending") ? "active" : "pending";
  }

  get extractionFinished() {
    return this.extractionDone;
  }
}

function moduleLabel(m: string) {
  return (
    {
      writing: "Writing quality",
      readability: "Readability",
      ai_patterns: "AI-writing patterns",
      academic: "Academic completeness",
      formatting: "Formatting & structure",
      similarity: "Similarity",
    } as Record<string, string>
  )[m] ?? m;
}
