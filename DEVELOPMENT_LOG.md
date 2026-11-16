# Development log

## 2026-11-14 — initial build

The repository was empty. Toolchain available:
- Python 3.14 and 3.15. 3.14 is used, because 3.15 is too new for some wheels.
- Node 26.
- Next.js 16.4.6.
- Ollama was **not** installed, so no model weights were downloaded.

### Phase 1 — Foundation

- **FastAPI backend, SQLite schema and data directory.** Originals are stored read-only and verified by SHA-256 on every read.
- **DOCX validation:**
  - extension, size and empty file;
  - encrypted / OLE container (also catches legacy `.doc`);
  - ZIP integrity and CRC, path traversal, zip-bomb size limits;
  - required parts, main document located via relationships;
  - XML well-formedness of every part, with entity resolution disabled;
  - missing relationship targets reported as warnings.
- **OOXML text model:** atoms for text, tabs, breaks, objects and field characters. Handles hyperlinks, `w:ins`, smart tags, content controls, `fldSimple`, complex fields, math and `mc:AlternateContent`. Deleted text and `mc:Fallback` duplicates are skipped.
- **Extractor output:**
  - paragraphs, with headings resolved through the style `basedOn` chain and outline levels;
  - lists, including style-based numbering;
  - tables, including nested tables;
  - images, hyperlinks, sections and page settings;
  - headers, footers, footnotes and endnotes;
  - field citations (Word, Zotero, Mendeley, EndNote), a references section, metadata;
  - rendered or estimated pages, sentences and words.
- **Stable paragraph IDs** from (file hash, part, structural path).
- **Ollama client:** status, tags, ps, chat streaming with cancellation, `think: false` for reasoning models, JSON-schema output, embeddings, pull (only on explicit request), delete, unload, latency test.

### Phase 2 — Scanning

- **Event bus:** typed, persisted events with a sequence number per document, delivered over SSE. Reconnects replay via `Last-Event-ID`.
- **Pipeline stages** match the specified list: upload, validate, structure, text, OCR, elements, paragraphs, originality, suggestions, workspace.
  - Each stage reports pending, active, completed, failed or skipped.
  - The pipeline is resumable: interrupted documents restart from the first incomplete stage.
  - Cancellation is checked between work units and aborts in-flight LLM streams.
- **Scan UI.** A `ScanEngine` paces the visuals over real events, keeping the *truth* panel separate from the *visual* cursor. It includes:
  - word-by-word recognition with a measured recognition box, a beam and a line sweep;
  - sentence flashes, paragraph boundary detection, and a sentence sweep during analysis;
  - a distinct state while the local model reviews a paragraph, which is marked complete only on the backend's `paragraph.completed`;
  - thumbnails, timeline and live counters;
  - pause, skip, jump to page, follow, continue in background, and cancel with confirmation;
  - reduced-motion support.
- **OCR** of embedded images uses the macOS Vision framework. It is opt-in and never applied to normal Word text.

### Phase 3 — Analysis

- **Separate modules:** writing (rules), readability, AI-writing patterns (statistical only, cautious labels and disclaimers, no probability), academic completeness and citations, formatting and structure, similarity.
- **Similarity sources:**
  - local corpus of PDF, DOCX, TXT and MD files or folders;
  - internal duplication within the document;
  - optional OpenAlex / Crossref search, off by default and disclosed.
- **Optional LLM paragraph review**, which drops any finding whose quoted passage isn't found verbatim.
- **False positives fixed after checking real output:**
  - British spellings no longer flagged;
  - table cells excluded from readability and pattern statistics.

### Phase 4 — Suggestions

- Rule findings with a correction become span suggestions automatically.
- **AI suggestions:**
  - 16 actions × 3 intensities, with context from the previous and next paragraph, section heading, document title, terminology and standing instructions;
  - streamed token by token over SSE;
  - cancelling keeps the partial output as an `incomplete` draft, which can't be accepted until edited;
  - alternatives share a `group_id` and can be compared; accepting one supersedes its siblings.
- **Meaning-preservation and verification checks:**
  - numbers, citations and names kept;
  - negations, and certainty or scope words;
  - length changes;
  - protected markers;
  - newly introduced facts flagged as *verify*.
- **Batch accept** skips any suggestion that needs verification or might change the meaning.

### Phase 5 — Editor

- **Three resizable panels:**
  - **navigator:** virtualized paragraphs, outline, findings, search, page and status filters, keyboard navigation;
  - **writing panel:** finding highlights, sentence selection, manual editing with `⟦n⟧` markers, per-paragraph history;
  - **live DOCX preview:** two-way selection, modified-paragraph marking, original/current toggle, zoom.
- **Revision log** with undo and redo (⌘Z / ⇧⌘Z), restore original, and reanalysis of modified paragraphs.

### Phase 6 — Export

- **Format-preserving paragraph editor:** token diff → existing runs; locked atoms enforced; empty runs pruned; tabs and breaks handled.
- **Package-preserving export,** with multi-check validation and a review report:
  - applied and rejected changes;
  - unresolved findings;
  - items that need verification;
  - failed edits.
- **Tests:** 16 OOXML preservation tests, including a byte-identical round-trip for all fixtures.

### Phase 7 — Polish & verification

- **Code quality:** `tsc`, ESLint (react-hooks v7 rules) and `next build` all pass.
- **Browser E2E run with Playwright and Chrome:**
  - dashboard → upload → scan → skip → workspace;
  - preview tagging and selection sync;
  - accepting a suggestion updates the preview;
  - clicking the preview selects the paragraph;
  - undo and redo;
  - analysis, similarity, export and settings pages;
  - **0 console errors.**
- **Long-document check:** 400 paragraphs scanned at about 60 fps. The navigator is virtualized, rendering 29 rows.

### Not done / limitations

- **AI paths not run against real models.** They're verified against a protocol-compatible stand-in (`tests/fake_ollama.py`). Model choice and quality on real hardware are untested.
- **Word not tested.** Exported DOCX files weren't opened in Microsoft Word, only through structural validation and python-docx.
- **Not supported:**
  - tracked-change export;
  - editing text-box text;
  - Strict OOXML;
  - OCR of scanned PDFs in the corpus (PDFs need a text layer).
- **Preview approximation:** docx-preview renders some Symbol-font bullets as boxes.
- **No automated frontend unit tests.** Frontend coverage is the E2E run described above. The Playwright script lives outside the repo and could be promoted to `frontend/e2e/`.

## 2026-11-14 — simplicity & design pass (open-source release prep)

Feedback: the UI was too complex and the scan screen too cluttered; the user wanted something premium that anyone can use, without generic "AI" styling.

- **Visual language** — editorial instead of sci-fi:
  - warm paper background, ink text, and one proofreader's-pen accent (vermilion);
  - Instrument Serif for display type;
  - no glows, grids, particles or gradients.
- **Home** — headline ("Better writing. Still yours.") and an upload area styled as a sheet of paper. Side panels removed; a single friendly banner appears when the AI is off.
- **Scan** — one sheet of paper and one status line with a thin progress bar:
  - words settle from grey into ink, with a pen hairline at the reading position;
  - a highlighter sweep marks each sentence being checked, and ticks appear in the margin when the backend confirms a paragraph;
  - technical stage details and counts moved into an optional Details dialog;
  - it all remains driven by real events.
- **Review (new default document screen)** — the real Word preview on the left, and one suggestion at a time on the right:
  - Accept / Ignore / Edit, with keyboard support (Enter, ← →);
  - "Fix all simple mistakes at once" (rule-based spelling, grammar and punctuation only; never items that need verification);
  - four one-click AI actions plus a free-text request;
  - a single "Download Word file" button that exports, validates and downloads.
  The three-panel editor moved to `/documents/[id]/advanced`.
- **Settings** — "AI assistant" status first and a one-sentence privacy summary; everything else under "Advanced settings". Installed models are selected automatically, so setup needs no configuration.
- **Fixes:**
  - Symbol/Wingdings list bullets now render in the preview;
  - React dev double-mount no longer resets the review selection;
  - link-style buttons keep link semantics.
- **Open source:**
  - README rewritten for non-technical users;
  - `start.sh` checks prerequisites, explains what's missing and opens the browser;
  - MIT LICENSE and CONTRIBUTING.md added.

## 2026-11-15 — AI detection

Request: show whether a document was written by AI, as a percentage, like other AI detectors.

**What was built**
- **Optional and local.** Settings → AI detection installs PyTorch and Transformers, plus a ~500 MB model, after the user confirms. Detection then runs offline; documents never leave the machine.
- **Passages.** Body prose is split into passages of 150–300 words. Passages never cross a heading. Headings, tables, captions, references and headers/footers are skipped, and passages under 60 words aren't scored.
- **Scoring.** Each passage gets a calibrated probability that it is AI-generated.
  - The headline figure is the share of scored words in passages at 50% or higher.
  - Verdicts: *Likely AI-generated* (≥ 80%), *Mix* (20–80%), *Likely human* (< 20%), or *Not enough text* (under 120 scored words).
- **Where it shows up:**
  - a card on the review screen, with a ring gauge and an optional highlighter on flagged paragraphs in the preview;
  - an "AI detection" section in the report, passage by passage;
  - re-checking includes accepted edits.
- **During scans.** It runs automatically when installed (can be turned off). It never fails a scan.

**Choosing the method (measured, not assumed)**

Evaluation set:
- 157 human passages: Ghostbuster student essays, plus MAGE stories, news and Q&A;
- 548 AI passages: Ghostbuster ChatGPT ×5 prompt styles, Ghostbuster Claude, 24 essays generated locally with qwen3:4b, and 12 current-generation assistant passages.

All texts were truncated to app-sized passages of about 200 words.

| Method | AUROC | Notes |
|---|---|---|
| Binoculars, Qwen2.5-0.5B base + instruct (2 GB) | 0.49 | Models too small; scores for human and AI text overlapped completely |
| Fast-DetectGPT, Qwen2.5-0.5B variants | 0.47–0.58 | Same problem |
| e5-small LoRA classifier (133 MB) | 0.90 | Flagged 66% of human essays at its default threshold |
| **Fakespot RoBERTa-base classifier (500 MB)** | **0.996** | Chosen |

**Calibration**
- Platt scaling on logit(raw score), with a balanced prior: `a = 0.8902`, `b = -4.3914`. A raw score of 0.993 maps to 50%.
- 5-fold held-out results: **98.7% of AI passages detected; 3.8% of human passages falsely flagged**. Calibrated probabilities matched reality: of passages scored ≥ 80%, 99% were AI; of those under 20%, 4% were.
- Known weak spots, from the data:
  - lower-cased or tokenised text (e.g. MAGE "tldr"/"squad" human samples) is often misread as AI;
  - the essay sets are 2023–2025 era.

The evaluation is small and is not a substitute for a large benchmark; the UI says so.

## 2026-11-15 — flow & motion pass

Feedback: the app felt like separate screens with hard cuts; it needed a continuous flow and a "wow" moment.

Built on React 19.3's `<ViewTransition>` and the browser View Transitions API (Next.js 16 runs every navigation as a transition), plus Motion for in-page choreography. Code: `components/motion/transitions.tsx`; CSS at the end of `app/globals.css`.

- **The document is one object.** The upload sheet, each document's thumbnail in My documents, the page being scanned and the first page of the review preview share the name `doc-<id>`. Moving between those screens morphs the page instead of cutting.
  - The review screen now renders its layout and a correctly sized placeholder page immediately, so the morph has a target.
  - The real Word rendering then fades in on top of the placeholder.
- **Scan → review flows on its own.** When scanning finishes, a pen-stroke tick draws and the button fills for 2.6 s, then the app continues to the review screen. Opening Details, flipping pages or clicking "Stay here" cancels it.
- **AI ring morph.** The ring travels from the review card into the report's hero ring. The report draws it from the review screen's cached result, so it doesn't count up a second time.
- **Directional navigation.** Links are tagged forward (deeper into a document) or back (towards My documents). The header is the fixed reference point and never slides. Untyped navigations crossfade.
- **Details:**
  - the home intro staggers in with a soft blur, once per session;
  - the review panel slides in beside the document;
  - an accepted edit briefly glows green in the preview.
- **Reduced motion.** With reduced motion on, transitions are instant. Browsers without the View Transitions API navigate normally.
- **Fix:** `start.sh` now refuses to start when port 3000 or 8000 is taken and explains why. It previously printed "running" and then failed.

Verified in Chrome with Playwright: all six transition types start with the expected names and types, there are 0 console errors, and the reduced-motion run passes.

## 2026-11-15 — "writing desk" visual pass

Goal: make the interface feel handled by a person rather than generated, with real depth.

- **Desk + paper.** Body is a warm, grainy desk (`--grain` SVG noise) lit by a lamp glow that trails the cursor (`components/human/desk.tsx`). Cards are sheets of paper (`.hw-card`, `.hw-paper`: fine fibre texture, contact + cast shadow) instead of bordered boxes.
- **Pen marks** (`components/human/ink.tsx`): `Scribble` (underline, strike, circle, caret, tick, arrow, squiggle; strokes draw themselves), `Handwrite` (Caveat, writes left→right), `Stamp` (rubber stamp with uneven ink mask), `Sticky`, `InkProgress` (hand-drawn progress line).
- **3D** (`components/human/tilt.tsx`): `Tilt` turns an object towards the pointer in perspective with spring physics and a sliding light glare; `Depth` places children at a Z depth so layers parallax. Inert on touch and with reduced motion.
- **Home**: headline is proofread live ("like ~~a machine~~ ^you."). The drop zone is a 3D pile of exercise-book paper; dragging a file over it lifts the top sheet and fans the pile. Documents are paper cards placed at slight hand-placed angles with a sticky note ("9 to fix" / "all done").
- **Review**: suggestion titles are red-pen margin notes, progress is an ink line, finishing stamps "Reviewed". **AI detection** gets a verdict stamp (Human / Mixed / AI-like). **Scan** status is handwritten with an ink progress line.
- Header: handwritten "write" logo, pen underline under the active nav item; icons-only nav on phones (fixes horizontal scroll at 390px).

## 2026-11-15 — AI detection v3: two models + trained layer

**Why.** On a real assignment (terse technical exam answers), v2 reported 0% AI while a hosted detector reported 34%.

Investigation showed the gap was the writing genre, not calibration:
- **AI technical notes.** Fakespot alone caught 33% of current-generation AI technical notes at its raw 0.5 threshold, and 8% after calibration.
- **Other open models.** Desklib caught 0% of the same notes, and SuperAnnotate also 0%.
- **Zero-shot methods** (Binoculars / Fast-DetectGPT with Qwen2.5-0.5B) scored at chance on this genre.
- **Uncalibrated thresholds** would wrongly flag 8–34% of *human* pre-2021 Stack Exchange answers.

**What v3 does.**
- **Two classifiers per passage:** Fakespot RoBERTa-base and Desklib DeBERTa-v3-large (MIT).
- **A combining layer:** logistic regression on each model's logit plus its mean-pooled hidden state (1794 features), shipped as `layer_v3.json` (57 KB).
- **Calibration:** Platt scaling, shifted so that P = 0.5 gives about 3% false alarms, with each human genre weighted equally.
- **Training data:** see `backend/scripts/detector/README.md`. Most important: pre-2021 human technical writing (Stack Exchange, arXiv) on the human side, and AI technical answers from two generator families on the AI side.

**Held-out results** (never trained on; the shipped layer is then refit on everything):

| Set | Result |
|---|---|
| Current-generation AI technical notes (12, a different author from the training notes) | 92% caught |
| AI essays reworded "like a student" (36) | 100% caught |
| AI essays prompted to sound human (20) | 100% caught |
| qwen3 technical answers, held-out quarter (29) | 100% caught |
| Human Stack Exchange answers, held-out quarter (~165) | 0–3.6% flagged, by site |
| Human arXiv abstracts (141) | 2.2% flagged on 91 (second set), 10% on the first 50; about 5% overall |
| Human essays (cross-validated) | about 5% flagged |
| SuperAnnotate RoBERTa-large | Rejected: weaker everywhere; licence is AGPL-style |

**Check for a style shortcut.** Adding "Answer 3." or "Label:" openings to human answers did not change their scores (1 of 30 flagged before and after).

**Other changes.**
- **"Possibly AI" band:** passages at 20–50% are labelled "Possibly AI" and reported as `possible_percent`. They aren't counted in the AI percentage, but they're shown and highlighted.
- **Existing installs:** a Fakespot-only install keeps working as v2. Settings offers the upgrade, a 1.7 GB download that needs confirmation.

**On the assignment that started this:** v3 reports 66% AI and 21% possibly AI. Without the "Answer N." and "Example:" labels, it reports 40% AI and 54% possibly AI. There is no ground truth for that document.

**Fix (same day).** The scan screen replayed its animation sentence by sentence after processing had finished, which showed "Finishing up" for minutes on long documents with no skip button. The replay now ends within 2.5 s of completion, and "Skip animation" stays visible until the page shows ready.

## 2026-11-16 — Humanizer

Request: an advanced humanizer that turns stiff, repetitive or generic writing into clear, natural prose. It must preserve meaning, validate the result, and stay independent of the AI detector.

**Architecture.** New backend package `services/humanizer/`, router `api/humanizer.py`, page `/humanize`.
- **Reuses the existing app:** the Ollama client, readability metrics, stock-phrase lists, `assess()` meaning checks, the DOCX parser and validator, pypdf, macOS OCR, SSE helpers and settings.
- **Detector independence:** nothing in the humanizer imports the detector (a test enforces this). A separate endpoint, `POST /api/ai-detector/analyze-text`, lets the user run the unchanged detector on any text. Its result is shown only in a dialog, never fed back into rewriting.

**Pipeline** (each stage event is emitted only when that work actually runs):
1. **Read.** Markdown-aware blocks that round-trip exactly. Citations, quotations, links, e-mail addresses and inline code are swapped for `⟦C1⟧`-style tokens. Headings, block quotes, code fences, signatures and short fragments are never rewritten.
2. **Analyse.** A deterministic writing profile: sentence rhythm, repeated openings, stock transitions, filler, redundancy, vocabulary repetition, formality, tone drift, readability and formulaic intros and conclusions. Its findings become prompt guidance.
3. **Context.** One model call on a ~700-word digest gives document type, audience, purpose, key terms and claims to preserve. AI-suggested terms are kept only if they occur in the text. Texts under 150 words skip this call, and the stage says so.
4. **Rewrite.** Sections of about 380 words, never crossing headings. Each prompt carries the previous section's revised tail and the next section's opening, to keep continuity. A rewrite that loses or alters a protected token is retried once with a reminder, then the original block is kept and noted. Writing a protected item out exactly instead of its token is accepted.
5. **Flow.** Medium and Extensive only. The model is called just for paragraphs whose openings repeat or that start with a stock transition at a section seam, and a fix is accepted only if the rest of the paragraph is unchanged.
6. **Validate.** Deterministic checks always run, plus an optional AI comparison per changed section. Its flags are grounded: an excerpt that isn't in the text downgrades the flag. Results are worded as "no problems found by these checks", never "meaning preserved".
7. **Assemble.** The output, plus before/after metrics.

**Measured on the real model (qwen3:4b, Apple Silicon).**
- Reasoning plus Ollama's JSON `format` grammar took **932 s** for two sentences. Reasoning off (`think=false`) still leaked reasoning into the answer and took 154 s. So: reasoning stays on (the existing client's behaviour), JSON is requested in the prompt and parsed leniently, and leaked `<think>` text is stripped.
- **90-word e-mail** (Professional, Medium, AI check on): **270 s**.
  - The rewrite was clean; the date, time and sign-off were exact.
  - Flags: "attendance is mandatory" → "must attend" (certainty, a fair flag) and "56% shorter".
  - An idiom false positive ("do not hesitate" read as a negation) was found and fixed.
- **~330-word academic paper with 5 headings** (Academic, Medium, AI check on): **1,445 s**, 4 sections.
  - Before the fixes above, 2 of 5 blocks were kept:
    - one correctly, because the model changed `"…"` to `'…'` in a quotation;
    - one needlessly, because the model wrote "(Kaplan, 1995)" instead of its token.
  - After the fixes, the introduction was rerun: both blocks were revised, with citations and quotation exact.
  - The AI check made a correct, grounded flag: the conclusion overstated "the findings show… a crucial role" as "improve".
  - Stock-transition rate fell from 71% to 33%.
- **Known gap:** with the AI check off, the automated checks do not catch claim softening or strengthening that keeps the same words and numbers. The UI says this.

**Tests.**
- Backend: 41 tests in `tests/test_humanizer.py`, run against the fake Ollama. It now also answers humanizer prompts, including leaked reasoning, dropped tokens, skipped blocks and AI flags. The full suite is 71 tests.
- Frontend: `npm test`, 8 diff-engine tests run on Node's built-in runner.
- Browser end-to-end: Playwright, 24 steps, 0 console errors. Covers the full flow, accept/reject/undo, side by side, sentence editing, profiles, three download formats, the detector dialog, versions, validation errors, and 390 px mobile with no horizontal scroll.

**Small app-wide changes.**
- `HUMANWRITE_CORS_ORIGINS` (opt-in extra origins).
- A "Humanizer" item in the main navigation.
- `allowImportingTsExtensions` in `tsconfig.json`, for the Node-run test.

## 2026-11-16 — Humanizer speed

Measured on an Apple M5 (16 GB) with an 8-page assignment (2,535 words of prose, 66 headings):

- Before: `qwen3:4b` spent most of each call on hidden reasoning — one 380-word section took 8 min 50 s
  (6,600 tokens of reasoning, ~25 s of actual answer). The whole document was estimated at 1½–2½ hours.
- After: **4 min 04 s** for the full run (context, 13 sections, meaning check), no paragraphs skipped.

Changes:
- New **Speed** setting (Fast by default). Fast uses `qwen3:4b-instruct-2507-q4_K_M` (no reasoning phase) when
  installed, otherwise the selected model with reasoning switched off. Thorough keeps the old behaviour.
  `ollama_service.chat_stream` now honours an explicit `think` flag (default unchanged for other features).
- Sections: small sections are merged across headings (fewer calls), at most 12 blocks per section (small
  models stopped early on long bulleted lists), and skipped blocks are retried once on their own.
- Short labels such as "Answer 6." are kept as written (the model "completed" them with the next paragraph).
- Meaning check: ~700 words of original per call instead of one call per section, at most 6 issues per call,
  a 1,000-token output cap, and recovery of complete issues if a reply is cut off.
- Prompt: no added hedges ("runs" → "can run"), and symbols/notation kept exactly ("->" stayed "->").
- Progress panel: live model output, section map and a time estimate learned from real call timings.

## 2026-11-16 — Humanizer: multi-stage rewriting engine

Request: replace the single rewrite call with a multi-stage engine that does separate, real work per stage. Also add
multiple variations with compare/combine, more selection actions, and stronger output quality. The AI detector stays
independent.

**Why the previous output was inconsistent** (from code reading plus a real-model baseline on four test texts — academic,
e-mail, technical, blog — with `qwen3:4b-instruct-2507`):
- **One call did everything.** A single call per section had to restructure, choose vocabulary, apply style and fix grammar,
  and nothing measured which of these happened.
- **Thin understanding.** It ran once on a digest (headings plus the first two sentences of each paragraph), so the rewriter
  never saw the claims, qualifiers or logic of the paragraph it was rewriting.
- **Validation reported but never fixed.** In the baseline, the e-mail's "22:00 to 02:00" became "00:00 to 02:00". The AI
  check flagged it, but the wrong time was shipped.
- **Hedges and points were lost.**
  - Academic text: "roughly 30–45 min" → "30–45 min", "could" → "may", "most of the evidence" → "commonly"; the thesis
    sentence and "interventions have shown promising results" were deleted.
  - Technical text: "approximately 3 ms" lost its qualifier, and "should not be used" became "fails".
- **Noisy checks.** A count-based negation check flagged every test text ("number of negations changed", high), and the AI
  check emitted "Same; no issue." items.
- **Variance.** Temperature reached 0.7 at Extensive, and rewrite calls had no output cap.

**Pipeline** (`services/humanizer/pipeline.py`). Read → analyse → understand, then, for each variation:
structure → vocabulary → style → grammar & flow → validate & repair → assemble.
- **A. Understanding** (`semantics.py`).
  - A deterministic meaning ledger per block: qualifiers, negations, logical links (cause, contrast, concession, condition,
    purpose), numbers, and requests to the reader.
  - At Medium and Extensive, an AI semantic map lists the points each block makes. Blocks are labelled P1…Pn, because the
    model renumbered real ids and every point was then (correctly) rejected. Only points grounded in the block are kept;
    a point is moved to another block only if it is grounded in exactly that one.
  - The ledger and map feed the rewrite prompt as "meaning to keep" and feed validation.
- **B. Structure.** The section rewrite as before, plus meaning notes and the variation's approach. Temperatures are lower
  (0.3/0.5/0.6), and there is an output cap on non-reasoning calls.
- **C–E. Vocabulary, style, grammar & flow** (`refine.py`). Each stage first runs a deterministic check per block and only
  calls the model for flagged blocks, with the exact edits to make.
  - Vocabulary: generic words the rewrite introduced, stock phrases, simple → fancier synonym swaps, replaced key terms,
    worsened repetition.
  - Style: mode targets (sentence length, register, contractions, courtesy, person), writing-profile statistics, register
    consistency across the document, intensity conformance, and a "tighten" check for the Concise approach.
  - Grammar: rule-based corrections applied directly (only for errors the rewrite introduced), plus overlong sentences,
    unbalanced punctuation and repeated sentences or clauses; the existing paragraph-opening flow fix.
- **Edit guard** (`refine.guard`). A model edit is kept only if all of these hold:
  - protected tokens are intact;
  - similarity and length stay within per-stage limits;
  - the problem count goes down;
  - no new high-severity meaning flag appears;
  - no generic vocabulary is introduced;
  - no sentence is added (unless a split was requested);
  - the edit does not move the text back towards the original.
- **F. Validate & repair.**
  - Deterministic checks.
    - Numbers are compared as whole tokens, so "02:00" vs "00:02" and "-02:00" are caught, while "1,840" = "1840" and
      "9%" = "9 per cent".
    - Qualifier classes: approximation, possibility, non-necessity, tendency, majority, recommendation, requirement and
      others. Rewording within a class is fine; losing the class is flagged. Exclusions cover "about the plan", "the most
      important", "it should be noted", "how hot it should be" and "at all".
    - Negation is tracked per negated word; a negation that merely moves is reported at medium.
    - Missing semantic-map points and unsupported added sentences or clauses are flagged.
  - Optional AI comparison, which now sees the points.
  - Repair: one targeted call per batch for high flags, missing points, added content, dropped qualifiers and grounded AI
    flags, then re-checked (and re-run through the AI check if it was on).
  - A block whose number, date, citation, quotation, link or high-severity certainty problem remains is reverted to the
    original, with a note.
- **Model tiers** (`llm.py`). Targeted edits always use a non-reasoning model when one is installed. Speed = Thorough applies
  only to understanding, structure, validation and repair.
- **Real usage per run**: calls, prompt/output tokens, seconds and models, by stage, from Ollama's stats. Three attempts with
  backoff. Single-block replies without tags are accepted.

**Variations.** Up to three approaches: Balanced, Restructured, Concise, Flowing. Understanding runs once; stages B–F run
per variation. Later variations see variation 1's text for contrast, because without it they came out 83–97 % identical;
with it they measured 67–84 % similar.
- UI: **Compare & combine** (`compare-variations.tsx`, `lib/combine.ts`) shows the variations paragraph by paragraph. You can
  pick a variation or the original, edit any pick, and create a combined version, which is re-validated against the original.

**Selection editing.** New actions *Improve flow* and *Change tone* (formal, friendly, confident, neutral, persuasive,
empathetic, casual), and **Show 3 alternatives**: one call returns three different versions, each restored and validated
separately and ranked by flags. Alternatives are also checked for added content. The prompts now forbid invented analogies
and examples, after a "Friendly" rewrite added one.

**UI.** Nine-stage progress panel with a variation badge and plain-language status for each new stage. A "Variations" menu
in the config bar. Review panel additions: **What each stage did** (flagged / applied / rejected, rule fixes, repairs,
reverts, model calls, tokens), the meaning map, and repaired notes separated from flags.

**Measured** (real model, Fast speed, AI check on; scorer independent of both pipelines, in the session scratchpad):

| Case | Old: time | Old: issues | New: time / calls | New: issues |
|---|---|---|---|---|
| Academic (190 w, Academic/Medium) | 26 s | lost 5 hedges incl. *roughly*, *could*, *promising*; thesis sentence deleted | 37 s / 7 | all hedges and points kept ("most" → "much of") |
| E-mail (140 w, Professional/Medium) | 15 s | **22:00 → 00:00**; "I hope this email finds you well" kept; request became a command | 14 s / 4 | all times exact; request kept as a request; stock phrases 7 → 0 |
| Technical (180 w, Natural/Medium) | 13 s | *approximately* lost; "should not be used" → "fails" | 35 s / 6 | "should not be used" kept; *approximately* → *about* (same class) |
| Blog (180 w, Conversational/Extensive) | 13 s | — | 34 s / 4 | — |
| Long (700 w, 4 sections, Natural/Medium) | 69 s | 7 hedges lost; 672 → 448 words | 169 s / 10 | 4 hedges lost; 2 approximation drops still flagged high for review; 672 → 516 words |
| 3 variations (academic) | — | — | 105–155 s / 16–19 | no remaining high-severity findings in any variation |

The new engine is 1–3× slower and uses 4–10 calls instead of 1–4. In exchange, every meaning error found in the baseline is
now either prevented, repaired, or reverted and noted.

**Known limits.**
- Extensive intensity on a 4B model stays conservative (the blog text changed 10–23 %); repairs that restore meaning often
  restore original wording too.
- The Concise approach was not reliably shorter.
- Block-level revert keeps the whole original paragraph, including its stock phrasing.
- The deterministic checks are heuristics (stemmed word overlap, qualifier classes) and can produce medium-severity false
  positives.
- Claim softening or strengthening that keeps the same words still needs the AI check.

**Tests.**
- Backend: 97 (was 71), including stage work, map grounding, repair and revert, guard rejection, variations rebuilding
  exactly, alternatives and tone, number tokens, qualifiers, duplicates, and regressions for every real-model failure above.
- Frontend: 11 (was 8), adding combine.
- `tsc`, ESLint and `next build` pass.
- Browser end-to-end (Playwright, real model): two variations → stage report → compare & combine with an edited pick →
  change tone → three alternatives → accept option 2; 390 px with no horizontal overflow; 0 console errors.
