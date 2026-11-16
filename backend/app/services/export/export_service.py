"""Export orchestration: approved revisions → validated DOCX + review report."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import config
from app.services.document import document_service as docs
from app.services.export.document_export_service import build_revised_docx
from app.services.writing import revision_service as revisions
from app.storage import db

_preview_cache: dict[str, tuple[int, bytes]] = {}


def _edits(doc_id: str) -> dict[str, tuple[str, str]]:
    structure = docs.load_structure(doc_id)
    originals = {p["id"]: p["text"] for p in structure["paragraphs"]}
    return {pid: (originals[pid], text) for pid, text in revisions.current_texts(doc_id).items() if pid in originals}


def preview_bytes(doc_id: str, version: str = "current") -> bytes:
    doc = docs.get_document(doc_id)
    key = f"{doc_id}:{version}"
    head = doc["revision_head"] if version == "current" else -1
    cached = _preview_cache.get(key)
    if cached and cached[0] == head:
        return cached[1]
    edits = _edits(doc_id) if version == "current" else {}
    out = build_revised_docx(docs.original_bytes(doc), doc["summary"].get("main_part", "word/document.xml"), docs.salt(doc),
                             edits, preview_markers=True)
    _preview_cache[key] = (head, out.data)
    if len(_preview_cache) > 6:
        _preview_cache.pop(next(iter(_preview_cache)))
    return out.data


def export(doc_id: str) -> dict:
    doc = docs.get_document(doc_id)
    original = docs.original_bytes(doc)
    edits = _edits(doc_id)
    out = build_revised_docx(original, doc["summary"].get("main_part", "word/document.xml"), docs.salt(doc), edits)
    if not out.validation.get("ok"):
        # Never hand out a document that failed validation.
        return {"ok": False, "validation": out.validation, "failed": out.failed,
                "message": "The revised document failed validation, so it was not saved. Your original is unchanged."}
    stem = re.sub(r"[^\w\- .()]", "_", Path(doc["filename"]).stem)[:80]
    eid = db.new_id("exp")
    filename = f"{stem} (HumanWrite revised).docx"
    path = config.exports_dir / f"{eid}.docx"
    path.write_bytes(out.data)

    sugg = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) AS n FROM suggestions WHERE document_id=? GROUP BY status", (doc_id,))}
    hist = revisions.history(doc_id)
    applied_ops = [o for o in hist["ops"] if o["state"] == "applied"]
    manual = sorted({o["paragraph_id"] for o in applied_ops if o["op_type"] == "manual"})
    unresolved = db.query("SELECT module, COUNT(*) AS n FROM findings WHERE document_id=? AND status='open' GROUP BY module", (doc_id,))
    verification = db.query(
        "SELECT id, paragraph_id, category, proposed_text, verification_json FROM suggestions WHERE document_id=? AND status='accepted' "
        "AND verification_json LIKE '%\"required\":true%'", (doc_id,))
    structure = docs.load_structure(doc_id)
    warnings = list(structure.get("warnings", []))
    if out.failed:
        warnings.append(f"{len(out.failed)} paragraph change(s) could not be applied safely and were left as in the original.")
    report = {
        "ok": True,
        "export_id": eid,
        "filename": filename,
        "size_bytes": len(out.data),
        "paragraphs_changed": len(out.applied),
        "applied": out.applied,
        "failed": out.failed,
        "suggestions": {"accepted": sugg.get("accepted", 0), "rejected": sugg.get("rejected", 0), "pending": sugg.get("pending", 0),
                        "incomplete": sugg.get("incomplete", 0)},
        "manual_edits": len(manual),
        "unresolved_findings": {r["module"]: r["n"] for r in unresolved},
        "needs_verification": [{"suggestion_id": v["id"], "paragraph_id": v["paragraph_id"], "category": v["category"],
                                "items": (db.loads(v["verification_json"], {}) or {}).get("items", [])} for v in verification],
        "validation": out.validation,
        "warnings": warnings,
        "original_sha256": doc["sha256"],
        "original_unchanged": docs.original_bytes(doc) is not None,
    }
    db.execute("INSERT INTO exports(id, document_id, path, filename, report_json, created_at) VALUES (?,?,?,?,?,?)",
               (eid, doc_id, str(path), filename, db.dumps(report), db.now()))
    return report


def list_exports(doc_id: str) -> list[dict]:
    rows = db.query("SELECT id, filename, report_json, created_at FROM exports WHERE document_id=? ORDER BY created_at DESC", (doc_id,))
    for r in rows:
        rep = db.loads(r.pop("report_json"), {})
        r["paragraphs_changed"] = rep.get("paragraphs_changed")
        r["validation_ok"] = (rep.get("validation") or {}).get("ok")
        r["report"] = rep
    return rows
