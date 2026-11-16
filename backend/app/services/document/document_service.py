"""Document repository: originals, extracted structure, metadata."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.core.config import config
from app.services.document.validation import validate_docx_bytes
from app.services.ooxml.package import DocxPackage
from app.storage import db

STAGES = [
    ("upload", "Uploading document"),
    ("validate", "Validating DOCX"),
    ("structure", "Extracting document structure"),
    ("text", "Extracting text"),
    ("ocr", "Recognizing embedded image content"),
    ("elements", "Detecting document elements"),
    ("paragraphs", "Analyzing paragraphs"),
    ("originality", "Running originality analysis"),
    ("suggestions", "Generating improvement suggestions"),
    ("workspace", "Preparing the document workspace"),
]

_structure_cache: OrderedDict[str, dict] = OrderedDict()
_pkg_cache: OrderedDict[str, DocxPackage] = OrderedDict()
CACHE_SIZE = 3


def initial_stages() -> list[dict]:
    return [{"key": k, "label": label, "state": "pending", "detail": None, "started_at": None, "finished_at": None}
            for k, label in STAGES]


def create_document(filename: str, data: bytes) -> dict:
    report = validate_docx_bytes(data, filename, config.max_upload_bytes)
    doc_id = db.new_id("doc")
    sha = hashlib.sha256(data).hexdigest()
    config.ensure_dirs()
    original = config.originals_dir / f"{doc_id}.docx"
    original.write_bytes(data)
    # The original is the source of truth: make it read-only on disk.
    os.chmod(original, stat.S_IRUSR | stat.S_IRGRP)
    stages = initial_stages()
    stages[0].update(state="completed", started_at=db.now(), finished_at=db.now(), detail=f"{len(data) / 1024:.1f} KB")
    t = db.now()
    db.execute(
        "INSERT INTO documents(id, filename, original_path, sha256, size_bytes, status, summary_json, stages_json, options_json, "
        "revision_head, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
        (doc_id, filename, str(original), sha, len(data), "uploaded",
         db.dumps({"validation": {"checks": report.checks, "warnings": report.warnings}, "main_part": report.main_part}),
         db.dumps(stages), db.dumps({}), t, t),
    )
    return get_document(doc_id)


def get_document(doc_id: str) -> dict | None:
    r = db.query_one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not r:
        return None
    r["summary"] = db.loads(r.pop("summary_json"), {})
    r["stages"] = db.loads(r.pop("stages_json"), [])
    r["options"] = db.loads(r.pop("options_json"), {})
    return r


def list_documents() -> list[dict]:
    rows = db.query("""
        SELECT d.id, d.filename, d.status, d.size_bytes, d.created_at, d.updated_at, d.summary_json, d.error,
          (SELECT COUNT(*) FROM suggestions s WHERE s.document_id=d.id AND s.status='pending') AS pending,
          (SELECT COUNT(*) FROM suggestions s WHERE s.document_id=d.id AND s.status='accepted') AS accepted,
          (SELECT COUNT(*) FROM suggestions s WHERE s.document_id=d.id AND s.status='rejected') AS rejected,
          (SELECT COUNT(*) FROM findings f WHERE f.document_id=d.id) AS findings,
          (SELECT COUNT(*) FROM exports e WHERE e.document_id=d.id) AS exports
        FROM documents d ORDER BY d.updated_at DESC""")
    for r in rows:
        s = db.loads(r.pop("summary_json"), {})
        r["summary"] = s.get("counts")
    return rows


def update_document(doc_id: str, **fields: Any) -> None:
    sets, vals = [], []
    for k, v in fields.items():
        if k in ("summary", "stages", "options"):
            k = f"{k}_json"
            v = db.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(db.now())
    db.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id=?", (*vals, doc_id))


def original_bytes(doc: dict) -> bytes:
    data = Path(doc["original_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != doc["sha256"]:
        raise RuntimeError("The stored original document failed its integrity check.")
    return data


def package(doc: dict) -> DocxPackage:
    if doc["id"] in _pkg_cache:
        _pkg_cache.move_to_end(doc["id"])
        return _pkg_cache[doc["id"]]
    pkg = DocxPackage(original_bytes(doc), doc["summary"].get("main_part", "word/document.xml"))
    _pkg_cache[doc["id"]] = pkg
    while len(_pkg_cache) > CACHE_SIZE:
        _pkg_cache.popitem(last=False)
    return pkg


def salt(doc: dict) -> str:
    return doc["sha256"][:16]


def structure_path(doc_id: str) -> Path:
    return db.document_dir(doc_id) / "structure.json"


def save_structure(doc_id: str, structure: dict) -> None:
    p = structure_path(doc_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(structure, ensure_ascii=False))
    tmp.replace(p)
    _structure_cache[doc_id] = structure
    _structure_cache.move_to_end(doc_id)
    while len(_structure_cache) > CACHE_SIZE:
        _structure_cache.popitem(last=False)


def load_structure(doc_id: str) -> dict | None:
    if doc_id in _structure_cache:
        _structure_cache.move_to_end(doc_id)
        return _structure_cache[doc_id]
    p = structure_path(doc_id)
    if not p.exists():
        return None
    st = json.loads(p.read_text())
    _structure_cache[doc_id] = st
    while len(_structure_cache) > CACHE_SIZE:
        _structure_cache.popitem(last=False)
    return st


def analysis_path(doc_id: str) -> Path:
    return db.document_dir(doc_id) / "analysis.json"


def save_analysis(doc_id: str, analysis: dict) -> None:
    p = analysis_path(doc_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(analysis, ensure_ascii=False))
    tmp.replace(p)


def load_analysis(doc_id: str) -> dict:
    p = analysis_path(doc_id)
    return json.loads(p.read_text()) if p.exists() else {}


def delete_document(doc_id: str) -> None:
    doc = get_document(doc_id)
    if not doc:
        return
    _structure_cache.pop(doc_id, None)
    _pkg_cache.pop(doc_id, None)
    for e in db.query("SELECT path FROM exports WHERE document_id=?", (doc_id,)):
        Path(e["path"]).unlink(missing_ok=True)
    orig = Path(doc["original_path"])
    if orig.exists():
        os.chmod(orig, stat.S_IWUSR | stat.S_IRUSR)
        orig.unlink()
    shutil.rmtree(config.data_dir / "documents" / doc_id, ignore_errors=True)
    with db.transaction() as conn:
        for t in ("findings", "suggestions", "revision_ops", "events", "exports"):
            conn.execute(f"DELETE FROM {t} WHERE document_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))


def paragraph_map(structure: dict) -> dict[str, dict]:
    return {p["id"]: p for p in structure["paragraphs"]}


def storage_info() -> dict:
    total = 0
    files = 0
    for root, _, fs in os.walk(config.data_dir):
        for f in fs:
            try:
                total += (Path(root) / f).stat().st_size
                files += 1
            except OSError:
                pass
    return {"data_dir": str(config.data_dir), "bytes": total, "files": files,
            "documents": db.query_one("SELECT COUNT(*) AS n FROM documents")["n"]}
