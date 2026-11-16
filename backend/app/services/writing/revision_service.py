"""Proposed-document state as an append-only operation log.

The original DOCX is never touched. Every accept / manual edit / restore /
reject is an operation with ``seq``. ``documents.revision_head`` points at
the last applied operation: undo moves the head back, redo moves it
forward. A new operation after an undo marks the undone branch inactive
(it stays in the log for history — nothing is overwritten or deleted).
"""

from __future__ import annotations

from app.services.document import document_service as docs
from app.services.extraction.sentences import split_sentences
from app.services.ooxml.package import iter_paragraphs
from app.services.ooxml.paragraph_editor import check_edit_safety
from app.storage import db


class RevisionError(Exception):
    pass


def _ops(doc_id: str) -> list[dict]:
    return db.query("SELECT * FROM revision_ops WHERE document_id=? ORDER BY seq", (doc_id,))


def _head(doc_id: str) -> int:
    r = db.query_one("SELECT revision_head FROM documents WHERE id=?", (doc_id,))
    return r["revision_head"] if r else 0


def current_texts(doc_id: str) -> dict[str, str]:
    """{paragraph_id: text} for paragraphs that differ from the original."""
    head = _head(doc_id)
    out: dict[str, str] = {}
    for op in db.query("SELECT paragraph_id, after_text FROM revision_ops WHERE document_id=? AND active=1 AND seq<=? "
                       "AND after_text IS NOT NULL ORDER BY seq", (doc_id, head)):
        out[op["paragraph_id"]] = op["after_text"]
    structure = docs.load_structure(doc_id) or {"paragraphs": []}
    originals = {p["id"]: p["text"] for p in structure["paragraphs"]}
    return {pid: t for pid, t in out.items() if originals.get(pid) != t}


def current_text(doc_id: str, pid: str, structure: dict | None = None) -> str:
    structure = structure or docs.load_structure(doc_id)
    head = _head(doc_id)
    r = db.query_one("SELECT after_text FROM revision_ops WHERE document_id=? AND paragraph_id=? AND active=1 AND seq<=? "
                     "AND after_text IS NOT NULL ORDER BY seq DESC LIMIT 1", (doc_id, pid, head))
    if r:
        return r["after_text"]
    p = docs.paragraph_map(structure).get(pid)
    if p is None:
        raise RevisionError("Unknown paragraph.")
    return p["text"]


def current_paragraphs(doc_id: str) -> list[dict]:
    """Structure paragraphs with current (proposed) text and re-segmented sentences."""
    structure = docs.load_structure(doc_id)
    changed = current_texts(doc_id)
    out = []
    for p in structure["paragraphs"]:
        if p["id"] in changed:
            q = dict(p)
            q["text"] = changed[p["id"]]
            q["sentences"] = [{"id": f"{p['id']}.r{i}", "start": s, "end": e} for i, (s, e) in enumerate(split_sentences(q["text"]))]
            from app.services.extraction.sentences import count_words
            q["word_count"] = count_words(q["text"].replace("￼", ""))
            q["modified"] = True
            out.append(q)
        else:
            out.append(p)
    return out


def _original_element(doc: dict, pid: str):
    pkg = docs.package(doc)
    structure = docs.load_structure(doc["id"])
    para = docs.paragraph_map(structure).get(pid)
    if not para:
        raise RevisionError("Unknown paragraph.")
    root = pkg.xml(para["part_name"])
    part = next(p for p in pkg.text_parts() if p.name == para["part_name"])
    for loc in iter_paragraphs(root, part, docs.salt(doc)):
        if loc.id == pid:
            return loc.element, para
    raise RevisionError("Paragraph not found in the original package.")


def export_safety(doc: dict, pid: str, new_text: str) -> str | None:
    el, para = _original_element(doc, pid)
    return check_edit_safety(el, para["text"], new_text)


def record(doc_id: str, op_type: str, *, paragraph_id: str | None = None, after_text: str | None = None,
           suggestion_id: str | None = None, new_status: str | None = None, note: str | None = None,
           check_safety: bool = True) -> dict:
    doc = docs.get_document(doc_id)
    if not doc:
        raise RevisionError("Document not found.")
    before = current_text(doc_id, paragraph_id) if paragraph_id else None
    if after_text is not None:
        if "￼" in (before or "") and after_text.count("￼") != (before or "").count("￼"):
            raise RevisionError("This change would remove a footnote marker, image or field. Keep the ⟦marker⟧ in place.")
        if check_safety:
            problem = export_safety(doc, paragraph_id, after_text)
            if problem:
                raise RevisionError(problem)
    prev_status = None
    if suggestion_id:
        s = db.query_one("SELECT status FROM suggestions WHERE id=?", (suggestion_id,))
        prev_status = s["status"] if s else None
    head = doc["revision_head"]
    with db.transaction() as conn:
        conn.execute("UPDATE revision_ops SET active=0 WHERE document_id=? AND seq>? AND active=1", (doc_id, head))
        mx = conn.execute("SELECT COALESCE(MAX(seq),0) FROM revision_ops WHERE document_id=?", (doc_id,)).fetchone()[0]
        seq = mx + 1
        conn.execute(
            "INSERT INTO revision_ops(document_id, seq, op_type, paragraph_id, before_text, after_text, suggestion_id, prev_status, "
            "new_status, active, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
            (doc_id, seq, op_type, paragraph_id, before, after_text, suggestion_id, prev_status, new_status, note, db.now()),
        )
        conn.execute("UPDATE documents SET revision_head=?, updated_at=? WHERE id=?", (seq, db.now(), doc_id))
        if suggestion_id and new_status:
            conn.execute("UPDATE suggestions SET status=?, reviewed=1, updated_at=? WHERE id=?", (new_status, db.now(), suggestion_id))
    return {"seq": seq, "op_type": op_type, "paragraph_id": paragraph_id}


def undo(doc_id: str) -> dict | None:
    head = _head(doc_id)
    op = db.query_one("SELECT * FROM revision_ops WHERE document_id=? AND seq=? AND active=1", (doc_id, head))
    if not op:
        return None
    prev = db.query_one("SELECT MAX(seq) AS m FROM revision_ops WHERE document_id=? AND active=1 AND seq<?", (doc_id, head))
    with db.transaction() as conn:
        if op["suggestion_id"]:
            conn.execute("UPDATE suggestions SET status=?, updated_at=? WHERE id=?",
                         (op["prev_status"] or "pending", db.now(), op["suggestion_id"]))
        conn.execute("UPDATE documents SET revision_head=?, updated_at=? WHERE id=?", ((prev["m"] or 0), db.now(), doc_id))
    return op


def redo(doc_id: str) -> dict | None:
    head = _head(doc_id)
    op = db.query_one("SELECT * FROM revision_ops WHERE document_id=? AND active=1 AND seq>? ORDER BY seq LIMIT 1", (doc_id, head))
    if not op:
        return None
    with db.transaction() as conn:
        if op["suggestion_id"] and op["new_status"]:
            conn.execute("UPDATE suggestions SET status=?, updated_at=? WHERE id=?", (op["new_status"], db.now(), op["suggestion_id"]))
        conn.execute("UPDATE documents SET revision_head=?, updated_at=? WHERE id=?", (op["seq"], db.now(), doc_id))
    return op


def history(doc_id: str) -> dict:
    head = _head(doc_id)
    ops = _ops(doc_id)
    for o in ops:
        o["state"] = "discarded" if not o["active"] else ("applied" if o["seq"] <= head else "undone")
    can_undo = any(o["active"] and o["seq"] <= head for o in ops)
    can_redo = any(o["active"] and o["seq"] > head for o in ops)
    return {"head": head, "ops": ops, "can_undo": can_undo, "can_redo": can_redo}


def paragraph_history(doc_id: str, pid: str) -> list[dict]:
    head = _head(doc_id)
    rows = db.query("SELECT * FROM revision_ops WHERE document_id=? AND paragraph_id=? ORDER BY seq", (doc_id, pid))
    for o in rows:
        o["state"] = "discarded" if not o["active"] else ("applied" if o["seq"] <= head else "undone")
    return rows
