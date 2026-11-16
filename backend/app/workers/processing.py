"""Background document processing pipeline.

Each stage emits real events as work happens. Stages are idempotent and
recorded, so a restarted server resumes from the first incomplete stage and
reconnecting clients replay events instead of triggering repeated work.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

from app.events.document_event_service import bus
from app.services.analysis import ai_writing_analysis_service as ai_patterns
from app.services.analysis import citation_analysis_service as citations
from app.services.analysis import readability_service as readability
from app.services.analysis import writing_analysis_service as writing
from app.services.detection import ai_detector_service as detector
from app.services.document import document_service as docs
from app.services.document.validation import ValidationError, validate_docx_bytes
from app.services.extraction import ocr_service
from app.services.extraction.docx_parser import extract_document, paragraph_words
from app.services.ollama.ollama_service import GenerationCancelled, OllamaUnavailable, ollama
from app.services.plagiarism import external_source_service as external
from app.services.plagiarism import similarity_service as similarity
from app.services.writing import suggestion_service as suggestions
from app.services.writing.llm_review_service import review_paragraph
from app.storage import db
from app.storage.settings_store import get_settings

log = logging.getLogger("humanwrite.processing")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.cancel = asyncio.Event()
        self.task: asyncio.Task | None = None


_jobs: dict[str, Job] = {}


def running(doc_id: str) -> bool:
    j = _jobs.get(doc_id)
    return bool(j and j.task and not j.task.done())


def start(doc_id: str, options: dict | None = None) -> bool:
    if running(doc_id):
        return False
    job = Job(doc_id)
    _jobs[doc_id] = job
    job.task = asyncio.create_task(_run(job, options or {}))
    return True


def cancel(doc_id: str) -> bool:
    j = _jobs.get(doc_id)
    if j and j.task and not j.task.done():
        j.cancel.set()
        return True
    return False


# --------------------------------------------------------------------------- helpers
def _stages(doc_id: str) -> list[dict]:
    return docs.get_document(doc_id)["stages"]


def _set_stage(doc_id: str, key: str, state: str, detail: Any = None) -> None:
    stages = _stages(doc_id)
    for s in stages:
        if s["key"] == key:
            s["state"] = state
            if detail is not None:
                s["detail"] = detail
            if state == "active":
                s["started_at"] = db.now()
            if state in ("completed", "failed", "skipped"):
                s["finished_at"] = db.now()
    docs.update_document(doc_id, stages=stages)
    bus.emit(doc_id, "document.stage.changed", {"stage": key, "state": state, "detail": detail})


def _stage_done(doc_id: str, key: str) -> bool:
    return any(s["key"] == key and s["state"] in ("completed", "skipped") for s in _stages(doc_id))


def _check(job: Job) -> None:
    if job.cancel.is_set():
        raise Cancelled()


async def _yield() -> None:
    await asyncio.sleep(0)


def _store_findings(doc_id: str, findings: list[dict], emit: bool = True) -> list[dict]:
    rows = []
    t = db.now()
    with db.transaction() as conn:
        for f in findings:
            fid = db.new_id("f")
            f["id"] = fid
            conn.execute(
                "INSERT INTO findings(id, document_id, paragraph_id, sentence_id, module, category, passage, start, end, explanation, "
                "correction, priority, confidence, source, data_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)",
                (fid, doc_id, f["paragraph_id"], f["sentence_id"], f["module"], f["category"], f["passage"], f["start"], f["end"],
                 f["explanation"], f["correction"], f["priority"], f["confidence"], f["source"], db.dumps(f["data"]), t),
            )
            rows.append(f)
    if emit:
        for f in rows:
            bus.emit(doc_id, "document.finding.created", {
                "id": f["id"], "paragraph_id": f["paragraph_id"], "module": f["module"], "category": f["category"],
                "priority": f["priority"], "start": f["start"], "end": f["end"]})
    return rows


# --------------------------------------------------------------------------- pipeline
async def _run(job: Job, options: dict) -> None:
    doc_id = job.doc_id
    settings = get_settings()
    docs.update_document(doc_id, status="processing", error=None, options={**options})
    try:
        await _stage_validate(job)
        structure = await _stage_extract(job)
        await _stage_ocr(job, structure, settings)
        await _stage_elements(job, structure)
        await _stage_paragraphs(job, structure, settings, options)
        await _stage_originality(job, structure, settings, options)
        await _stage_suggestions(job, structure)
        _set_stage(doc_id, "workspace", "active")
        counts = _counts(doc_id, structure)
        doc = docs.get_document(doc_id)
        summary = doc["summary"]
        summary["counts"] = counts
        docs.update_document(doc_id, summary=summary, status="ready")
        _set_stage(doc_id, "workspace", "completed")
        bus.emit(doc_id, "document.processing.completed", {"counts": counts})
    except Cancelled:
        for s in _stages(doc_id):
            if s["state"] == "active":
                _set_stage(doc_id, s["key"], "failed", "Cancelled by user")
        docs.update_document(doc_id, status="cancelled")
        bus.emit(doc_id, "document.processing.cancelled", {})
    except ValidationError as e:
        docs.update_document(doc_id, status="failed", error=e.message)
        bus.emit(doc_id, "document.processing.failed", {"message": e.message, "code": e.code})
    except Exception as e:  # noqa: BLE001
        log.error("processing failed: %s", traceback.format_exc())
        for s in _stages(doc_id):
            if s["state"] == "active":
                _set_stage(doc_id, s["key"], "failed", str(e)[:200])
        docs.update_document(doc_id, status="failed", error=str(e)[:500])
        bus.emit(doc_id, "document.processing.failed", {"message": str(e)[:500]})


async def _stage_validate(job: Job) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "validate"):
        return
    _set_stage(doc_id, "validate", "active")
    doc = docs.get_document(doc_id)
    data = docs.original_bytes(doc)
    try:
        report = validate_docx_bytes(data, doc["filename"], 1 << 40)
    except ValidationError as e:
        _set_stage(doc_id, "validate", "failed", e.message)
        raise
    bus.emit(doc_id, "document.validated", {"checks": report.checks, "warnings": report.warnings})
    _set_stage(doc_id, "validate", "completed", f"{len(report.checks)} checks passed")
    await _yield()


async def _stage_extract(job: Job) -> dict:
    doc_id = job.doc_id
    existing = docs.load_structure(doc_id)
    if existing and _stage_done(doc_id, "text"):
        return existing
    doc = docs.get_document(doc_id)
    pkg = docs.package(doc)
    _set_stage(doc_id, "structure", "active")
    _set_stage(doc_id, "text", "pending")
    state = {"page": None, "words": 0, "paragraphs": 0, "count": 0, "text_started": False}

    def on_stage(name: str, info: dict) -> None:
        bus.emit(doc_id, "document.structure.detected", {"phase": "package", **info})

    def on_paragraph(para: dict) -> None:
        if not state["text_started"]:
            _set_stage(doc_id, "structure", "completed")
            _set_stage(doc_id, "text", "active")
            state["text_started"] = True
        state["count"] += 1
        state["words"] += para["word_count"]
        if para["kind"] not in ("empty",):
            state["paragraphs"] += 1
        # Page events use provisional page numbers assigned after extraction; emit text now.
        bus.emit(doc_id, "document.text.extracted", {
            "paragraph": _visual_paragraph(para),
            "totals": {"paragraphs": state["paragraphs"], "words": state["words"]},
        })

    result = extract_document(pkg, docs.salt(doc), on_paragraph=on_paragraph, on_stage=on_stage)
    structure = result.structure
    docs.save_structure(doc_id, structure)
    # Page events once page assignment is known (Word stores no page geometry; see page_source).
    pages: dict[int, list[str]] = {}
    for p in structure["paragraphs"]:
        if p.get("page"):
            pages.setdefault(p["page"], []).append(p["id"])
    for page, pids in sorted(pages.items()):
        bus.emit(doc_id, "document.page.started", {"page": page, "paragraph_ids": pids})
        bus.emit(doc_id, "document.page.completed", {"page": page, "words": sum(
            next(q for q in structure["paragraphs"] if q["id"] == pid)["word_count"] for pid in pids)})
    if not state["text_started"]:
        _set_stage(doc_id, "structure", "completed")
    _set_stage(doc_id, "text", "completed", f"{structure['summary']['words']} words, {structure['summary']['paragraphs']} paragraphs")
    summary = doc["summary"]
    summary["structure"] = structure["summary"]
    summary["warnings"] = (summary.get("warnings") or []) + structure["warnings"]
    summary["metadata"] = structure["metadata"]
    docs.update_document(doc_id, summary=summary)
    await _yield()
    return structure


def _visual_paragraph(p: dict) -> dict:
    return {
        "id": p["id"], "part": p["part"], "kind": p["kind"], "heading_level": p["heading_level"],
        "list": p["list"], "table": p["table"], "page": p.get("page"), "text": p["text"],
        "words": paragraph_words(p), "sentences": [[s["start"], s["end"]] for s in p["sentences"]],
        "objects": p["objects"], "alignment": p["alignment"], "formatting": p["formatting"],
        "probable_heading": p["probable_heading"],
    }


async def _stage_ocr(job: Job, structure: dict, settings: dict) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "ocr"):
        return
    images = [i for i in structure["images"] if i.get("target")]
    if not settings["analysis"]["ocr"]:
        _set_stage(doc_id, "ocr", "skipped", "OCR disabled in settings")
        return
    if not images:
        _set_stage(doc_id, "ocr", "skipped", "No embedded images")
        return
    if not ocr_service.available():
        _set_stage(doc_id, "ocr", "skipped", "macOS Vision framework unavailable")
        return
    _set_stage(doc_id, "ocr", "active")
    doc = docs.get_document(doc_id)
    pkg = docs.package(doc)
    results = []
    for img in images:
        _check(job)
        target = img["target"]
        if not target.lower().endswith(ocr_service.OCR_FORMATS) or target not in pkg.names:
            continue
        data = pkg.read(target)
        words = await asyncio.to_thread(lambda: list(ocr_service.recognize(data)))
        for i, w in enumerate(words):
            bus.emit(doc_id, "document.ocr.word.recognized", {"image_id": img["id"], "paragraph_id": img["paragraph_id"],
                                                              "index": i, **w}, persist=i < 2000)
        text = " ".join(w["text"] for w in words)
        results.append({"image_id": img["id"], "paragraph_id": img["paragraph_id"], "text": text, "words": len(words)})
        await _yield()
    structure["ocr"] = results
    docs.save_structure(doc_id, structure)
    _set_stage(doc_id, "ocr", "completed", f"{sum(r['words'] for r in results)} words in {len(results)} image(s)")


async def _stage_elements(job: Job, structure: dict) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "elements"):
        return
    _set_stage(doc_id, "elements", "active")
    s = structure["summary"]
    bus.emit(doc_id, "document.structure.detected", {
        "phase": "elements",
        "summary": s,
        "headings": [{"id": p["id"], "text": p["text"][:120], "level": p["heading_level"], "page": p.get("page")}
                     for p in structure["paragraphs"] if p["kind"] in ("heading", "title") and p["part"] == "body"],
        "tables": [{"id": t["id"], "rows": t["rows"], "cols": t["cols"], "paragraph_ids": t["paragraph_ids"][:1]} for t in structure["tables"]],
        "images": [{"id": i["id"], "paragraph_id": i["paragraph_id"]} for i in structure["images"]],
        "references": len(structure["references"]),
    })
    _set_stage(doc_id, "elements", "completed",
               f"{s['headings']} headings, {s['tables']} tables, {s['images']} images, {s['references']} references")
    await _yield()


async def _stage_paragraphs(job: Job, structure: dict, settings: dict, options: dict) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "paragraphs"):
        return
    _set_stage(doc_id, "paragraphs", "active")
    db.execute("DELETE FROM findings WHERE document_id=? AND module='writing'", (doc_id,))
    paragraphs = structure["paragraphs"]
    bus.emit(doc_id, "document.analysis.started", {"module": "writing", "method": "rules"})

    from collections import Counter
    from app.services.analysis.common import analyzable
    from app.services.extraction.sentences import WORD_RE

    vocab = Counter(w.lower() for p in paragraphs for w in WORD_RE.findall(p["text"]))
    targets = [p for p in paragraphs if analyzable(p)]
    total = 0
    for i, p in enumerate(targets):
        _check(job)
        bus.emit(doc_id, "document.paragraph.started", {"paragraph_id": p["id"], "index": i, "total": len(targets), "stage": "rules"})
        fs = writing.analyze_paragraph(p, spellcheck=settings["analysis"]["spellcheck"], doc_vocab=vocab)
        fs = writing._dedupe(fs)
        _store_findings(doc_id, fs)
        total += len(fs)
        bus.emit(doc_id, "document.paragraph.completed", {"paragraph_id": p["id"], "index": i, "total": len(targets),
                                                          "findings": len(fs), "stage": "rules"})
        if i % 20 == 0:
            await _yield()
    _store_findings(doc_id, writing.terminology_consistency(paragraphs))
    bus.emit(doc_id, "document.analysis.completed", {"module": "writing", "method": "rules", "findings": total})

    # Optional AI review with the local model.
    model = settings["ollama"]["model"]
    llm_enabled = options.get("llm_review", settings["analysis"]["llm_review"])
    ai_note = None
    if llm_enabled and model:
        try:
            await ollama.ensure_model(model)
            limit = int(settings["analysis"]["llm_review_max_paragraphs"])
            ai_targets = [p for p in targets if p["word_count"] >= 12 and p["part"] == "body"][:limit]
            bus.emit(doc_id, "document.analysis.started", {"module": "writing", "method": "ai_review", "model": model,
                                                           "total": len(ai_targets)})
            prev = None
            n = 0
            for i, p in enumerate(ai_targets):
                _check(job)
                bus.emit(doc_id, "document.paragraph.started", {"paragraph_id": p["id"], "index": i, "total": len(ai_targets),
                                                                "stage": "ai_review"})
                try:
                    fs = await review_paragraph(model, p, prev, structure["metadata"].get("title"), settings, cancel=job.cancel)
                except GenerationCancelled as e:
                    raise Cancelled() from e
                stored = _store_findings(doc_id, fs)
                n += len(stored)
                bus.emit(doc_id, "document.paragraph.completed", {"paragraph_id": p["id"], "index": i, "total": len(ai_targets),
                                                                  "findings": len(stored), "stage": "ai_review"})
                prev = p["text"]
            bus.emit(doc_id, "document.analysis.completed", {"module": "writing", "method": "ai_review", "findings": n})
            ai_note = f"AI review of {len(ai_targets)} paragraph(s) with {model}"
            if len([p for p in targets if p['word_count'] >= 12]) > limit:
                ai_note += f" (limited to the first {limit}; review others on demand)"
        except OllamaUnavailable as e:
            ai_note = f"AI review skipped: {e}"
    elif llm_enabled:
        ai_note = "AI review skipped: no local model selected"
    _set_stage(doc_id, "paragraphs", "completed", "; ".join(filter(None, [f"{total} rule-based findings", ai_note])))


async def _stage_originality(job: Job, structure: dict, settings: dict, options: dict) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "originality"):
        return
    _set_stage(doc_id, "originality", "active")
    db.execute("DELETE FROM findings WHERE document_id=? AND module IN ('similarity','ai_patterns','readability','academic','formatting')",
               (doc_id,))
    paragraphs = structure["paragraphs"]
    analysis: dict[str, Any] = {}

    for module, fn in (("readability", readability.analyze), ("ai_patterns", ai_patterns.analyze)):
        _check(job)
        bus.emit(doc_id, "document.analysis.started", {"module": module})
        report, fs = fn(paragraphs)
        analysis[module] = report
        _store_findings(doc_id, fs)
        bus.emit(doc_id, "document.analysis.completed", {"module": module, "findings": len(fs)})
        await _yield()

    bus.emit(doc_id, "document.analysis.started", {"module": "academic"})
    summary, academic, formatting = citations.analyze(structure)
    analysis["academic"] = summary
    _store_findings(doc_id, academic)
    _store_findings(doc_id, formatting)
    bus.emit(doc_id, "document.analysis.completed", {"module": "academic", "findings": len(academic)})
    bus.emit(doc_id, "document.analysis.completed", {"module": "formatting", "findings": len(formatting)})

    if settings["analysis"].get("ai_detection", True) and detector.ready():
        _check(job)
        bus.emit(doc_id, "document.analysis.started", {"module": "ai_detection"})

        def det_prog(done: int, total: int) -> None:
            bus.emit(doc_id, "document.analysis.progress", {"module": "ai_detection", "done": done, "total": total}, persist=False)

        try:
            report = await asyncio.to_thread(detector.analyze, paragraphs, det_prog, lambda: job.cancel.is_set())
            report["text_version"] = "original"
        except InterruptedError:
            raise Cancelled() from None
        except Exception as e:  # noqa: BLE001  detection is optional; never fail the scan because of it
            report = {"available": False, "reason": f"AI detection failed: {str(e)[:160]}"}
        analysis["ai_detection"] = report
        bus.emit(doc_id, "document.analysis.completed", {"module": "ai_detection", "ai_percent": report.get("ai_percent")})

    sim_report: dict[str, Any] = {}
    if settings["analysis"]["similarity"]:
        _check(job)
        bus.emit(doc_id, "document.analysis.started", {"module": "similarity"})
        internal = similarity.internal_duplication(paragraphs)
        _store_findings(doc_id, internal)
        embeds = None
        emb_model = settings["ollama"]["embed_model"]
        emb_note = None
        if settings["analysis"]["semantic_similarity"] and emb_model:
            try:
                await ollama.ensure_model(emb_model)
                embeds = await similarity.embed_paragraphs(ollama, emb_model, paragraphs)
            except OllamaUnavailable as e:
                emb_note = f"Semantic similarity skipped: {e}"

        def prog(done: int, total: int) -> None:
            if done % 10 == 0 or done == total:
                bus.emit(doc_id, "document.analysis.progress", {"module": "similarity", "done": done, "total": total}, persist=False)

        corpus_report, corpus_findings = await asyncio.to_thread(
            similarity.analyze_against_corpus, paragraphs, embed_model=emb_model if embeds else None,
            paragraph_embeddings=embeds, on_progress=prog)
        _store_findings(doc_id, corpus_findings)
        sim_report = {"local": corpus_report, "internal_matches": len(internal), "semantic_note": emb_note}

        privacy = settings["privacy"]
        if privacy["external_sources"] and options.get("external_sources", True):
            _check(job)
            bus.emit(doc_id, "document.analysis.started", {"module": "similarity", "method": "external",
                                                           "providers": privacy["external_providers"]})
            try:
                ext_report, ext_findings = await external.search(paragraphs, privacy["external_providers"])
                _store_findings(doc_id, ext_findings)
                sim_report["external"] = ext_report
            except Exception as e:  # noqa: BLE001
                sim_report["external"] = {"checked": False, "error": str(e)[:200]}
        else:
            sim_report["external"] = {"checked": False, "reason": "External source discovery is disabled (Settings → Privacy)."}
        bus.emit(doc_id, "document.analysis.completed", {"module": "similarity",
                                                         "findings": len(internal) + len(corpus_findings)})
    else:
        sim_report = {"local": {"checked": False, "reason": "Similarity analysis disabled in settings."}}
    analysis["similarity"] = sim_report
    docs.save_analysis(doc_id, analysis)
    _set_stage(doc_id, "originality", "completed")


async def _stage_suggestions(job: Job, structure: dict) -> None:
    doc_id = job.doc_id
    if _stage_done(doc_id, "suggestions"):
        return
    _set_stage(doc_id, "suggestions", "active")
    db.execute("DELETE FROM suggestions WHERE document_id=? AND source IN ('rule','llm','statistical') AND status='pending' AND action='fix_finding'",
               (doc_id,))
    rows = db.query("SELECT * FROM findings WHERE document_id=? AND correction IS NOT NULL AND status='open'", (doc_id,))
    for r in rows:
        r["data"] = db.loads(r.pop("data_json"), {})
    pmap = docs.paragraph_map(structure)
    created = []
    for i in range(0, len(rows), 50):
        _check(job)
        batch = rows[i:i + 50]
        for f in batch:
            bus.emit(doc_id, "document.suggestion.started", {"finding_id": f["id"], "paragraph_id": f["paragraph_id"]}, persist=False)
        made = suggestions.from_findings(doc_id, batch, pmap)
        for s in made:
            bus.emit(doc_id, "document.suggestion.completed", {"id": s["id"], "paragraph_id": s["paragraph_id"],
                                                               "category": s["category"], "priority": s["priority"]})
        created.extend(made)
        await _yield()
    _set_stage(doc_id, "suggestions", "completed",
               f"{len(created)} ready-to-review corrections; AI rewrites are generated on demand in the workspace")


def _counts(doc_id: str, structure: dict) -> dict:
    rows = db.query("SELECT module, category, priority, COUNT(*) AS n FROM findings WHERE document_id=? GROUP BY module, category, priority",
                    (doc_id,))
    by_module: dict[str, int] = {}
    for r in rows:
        by_module[r["module"]] = by_module.get(r["module"], 0) + r["n"]
    sugg = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) AS n FROM suggestions WHERE document_id=? GROUP BY status", (doc_id,))}
    s = structure["summary"]
    return {"paragraphs": s["paragraphs"], "words": s["words"], "pages": s["pages"], "findings": by_module,
            "suggestions": sugg}


async def reanalyze(doc_id: str, paragraph_ids: list[str] | None = None) -> dict:
    """Re-run rule-based writing analysis on current (proposed) paragraph text."""
    from collections import Counter
    from app.services.analysis.common import analyzable
    from app.services.extraction.sentences import WORD_RE
    from app.services.writing import revision_service as revisions

    settings = get_settings()
    paragraphs = revisions.current_paragraphs(doc_id)
    vocab = Counter(w.lower() for p in paragraphs for w in WORD_RE.findall(p["text"]))
    targets = [p for p in paragraphs if analyzable(p) and (paragraph_ids is None or p["id"] in paragraph_ids)]
    ids = [p["id"] for p in targets]
    if not ids:
        return {"paragraphs": 0, "findings": 0, "suggestions": 0}
    marks = ",".join("?" * len(ids))
    db.execute(f"UPDATE findings SET status='superseded' WHERE document_id=? AND module='writing' AND status='open' AND paragraph_id IN ({marks})",
               (doc_id, *ids))
    db.execute(f"UPDATE suggestions SET status='superseded', updated_at=? WHERE document_id=? AND status='pending' AND action='fix_finding' "
               f"AND paragraph_id IN ({marks})", (db.now(), doc_id, *ids))
    fs = []
    for p in targets:
        fs.extend(writing._dedupe(writing.analyze_paragraph(p, spellcheck=settings["analysis"]["spellcheck"], doc_vocab=vocab)))
    stored = _store_findings(doc_id, fs, emit=False)
    made = suggestions.from_findings(doc_id, stored, {p["id"]: p for p in targets})
    return {"paragraphs": len(targets), "findings": len(stored), "suggestions": len(made)}
