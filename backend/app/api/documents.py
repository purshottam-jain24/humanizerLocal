"""Document, analysis, suggestion, revision and export endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.api.sse import sse_format, sse_response
from app.events.document_event_service import TERMINAL, bus
from app.services.document import document_service as docs
from app.services.document.validation import ValidationError
from app.services.export import export_service
from app.services.writing import revision_service as revisions
from app.services.writing import suggestion_service as suggestions
from app.storage import db
from app.workers import processing

router = APIRouter(prefix="/api")


def _doc_or_404(doc_id: str) -> dict:
    d = docs.get_document(doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return d


# ------------------------------------------------------------------ documents
@router.post("/documents")
async def upload(file: UploadFile = File(...), autostart: bool = True) -> dict:
    data = await file.read()
    try:
        doc = docs.create_document(file.filename or "document.docx", data)
    except ValidationError as e:
        raise HTTPException(422, {"code": e.code, "message": e.message}) from e
    bus.emit(doc["id"], "document.uploaded", {"filename": doc["filename"], "size_bytes": doc["size_bytes"]})
    if autostart:
        processing.start(doc["id"])
    return docs.get_document(doc["id"])


@router.get("/documents")
def list_documents() -> list[dict]:
    rows = docs.list_documents()
    for r in rows:
        r["running"] = processing.running(r["id"])
    return rows


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    d = _doc_or_404(doc_id)
    d["running"] = processing.running(doc_id)
    d.pop("original_path", None)
    return d


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    processing.cancel(doc_id)
    docs.delete_document(doc_id)
    bus.reset(doc_id)
    return {"ok": True}


class ProcessBody(BaseModel):
    restart: bool = False
    llm_review: bool | None = None
    external_sources: bool | None = None


@router.post("/documents/{doc_id}/process")
async def process(doc_id: str, body: ProcessBody) -> dict:
    doc = _doc_or_404(doc_id)
    if processing.running(doc_id):
        return {"started": False, "reason": "already running"}
    stages = doc["stages"]
    if body.restart:
        for s in stages[1:]:
            s.update(state="pending", detail=None, started_at=None, finished_at=None)
    else:
        for s in stages:
            if s["state"] in ("failed", "active"):
                s.update(state="pending", detail=None)
    docs.update_document(doc_id, stages=stages)
    opts: dict[str, Any] = {}
    if body.llm_review is not None:
        opts["llm_review"] = body.llm_review
    if body.external_sources is not None:
        opts["external_sources"] = body.external_sources
    return {"started": processing.start(doc_id, opts)}


@router.post("/documents/{doc_id}/cancel")
def cancel(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    return {"cancelled": processing.cancel(doc_id)}


@router.get("/documents/{doc_id}/events")
async def events(doc_id: str, request: Request, after: int = 0) -> Any:
    _doc_or_404(doc_id)
    last = request.headers.get("last-event-id")
    if last and last.isdigit():
        after = max(after, int(last))

    async def gen():
        q = bus.subscribe(doc_id)
        try:
            sent = after
            for ev in bus.history(doc_id, after):
                sent = ev["seq"]
                yield sse_format(ev, id_=ev["seq"])
            doc = docs.get_document(doc_id)
            if not processing.running(doc_id) and doc and doc["status"] in ("ready", "failed", "cancelled", "uploaded"):
                yield sse_format({"type": "stream.idle", "status": doc["status"]}, event="idle")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev["seq"] <= sent:
                    continue
                sent = ev["seq"]
                yield sse_format(ev, id_=ev["seq"])
                if ev["type"] in TERMINAL:
                    yield sse_format({"type": "stream.idle"}, event="idle")
        finally:
            bus.unsubscribe(doc_id, q)

    return sse_response(gen())


# ------------------------------------------------------------------ structure
@router.get("/documents/{doc_id}/structure")
def structure(doc_id: str) -> dict:
    doc = _doc_or_404(doc_id)
    st = docs.load_structure(doc_id)
    if not st:
        raise HTTPException(409, "The document has not been extracted yet.")
    changed = revisions.current_texts(doc_id)
    fcounts: dict[str, dict] = {}
    for r in db.query("SELECT paragraph_id, module, COUNT(*) AS n FROM findings WHERE document_id=? AND status='open' "
                      "GROUP BY paragraph_id, module", (doc_id,)):
        fcounts.setdefault(r["paragraph_id"], {})[r["module"]] = r["n"]
    scounts: dict[str, dict] = {}
    for r in db.query("SELECT paragraph_id, status, COUNT(*) AS n FROM suggestions WHERE document_id=? GROUP BY paragraph_id, status",
                      (doc_id,)):
        scounts.setdefault(r["paragraph_id"], {})[r["status"]] = r["n"]
    paragraphs = []
    for p in st["paragraphs"]:
        q = {k: v for k, v in p.items() if k not in ("rendered_page_breaks", "inline_page_breaks")}
        q["original_text"] = p["text"]
        if p["id"] in changed:
            q["text"] = changed[p["id"]]
            q["modified"] = True
        else:
            q["modified"] = False
        q["findings"] = fcounts.get(p["id"], {})
        q["suggestions"] = scounts.get(p["id"], {})
        paragraphs.append(q)
    return {**{k: v for k, v in st.items() if k != "paragraphs"}, "paragraphs": paragraphs,
            "document": {"id": doc_id, "filename": doc["filename"], "status": doc["status"], "revision_head": doc["revision_head"]}}


# ------------------------------------------------------------------ analysis
@router.get("/documents/{doc_id}/analysis")
def analysis(doc_id: str) -> dict:
    doc = _doc_or_404(doc_id)
    rows = db.query("SELECT module, category, priority, status, COUNT(*) AS n FROM findings WHERE document_id=? "
                    "GROUP BY module, category, priority, status", (doc_id,))
    sugg = db.query("SELECT category, status, COUNT(*) AS n FROM suggestions WHERE document_id=? GROUP BY category, status", (doc_id,))
    return {"reports": docs.load_analysis(doc_id), "finding_counts": rows, "suggestion_counts": sugg,
            "summary": doc["summary"], "status": doc["status"], "stages": doc["stages"]}


@router.get("/documents/{doc_id}/findings")
def findings(doc_id: str, module: str | None = None, category: str | None = None, priority: str | None = None,
             paragraph_id: str | None = None, status: str | None = "open", limit: int = Query(2000, le=10000)) -> list[dict]:
    _doc_or_404(doc_id)
    q = "SELECT * FROM findings WHERE document_id=?"
    params: list[Any] = [doc_id]
    for col, val in (("module", module), ("category", category), ("priority", priority), ("paragraph_id", paragraph_id), ("status", status)):
        if val:
            q += f" AND {col}=?"
            params.append(val)
    q += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at LIMIT ?"
    params.append(limit)
    rows = db.query(q, params)
    for r in rows:
        r["data"] = db.loads(r.pop("data_json"), {})
    return rows


class FindingPatch(BaseModel):
    status: Literal["open", "dismissed", "resolved"]


@router.patch("/findings/{finding_id}")
def patch_finding(finding_id: str, body: FindingPatch) -> dict:
    db.execute("UPDATE findings SET status=? WHERE id=?", (body.status, finding_id))
    return {"ok": True}


class ReanalyzeBody(BaseModel):
    paragraph_ids: list[str] | None = None


@router.post("/documents/{doc_id}/reanalyze")
async def reanalyze(doc_id: str, body: ReanalyzeBody) -> dict:
    _doc_or_404(doc_id)
    return await processing.reanalyze(doc_id, body.paragraph_ids)


# ------------------------------------------------------------------ suggestions
@router.get("/documents/{doc_id}/suggestions")
def list_suggestions(doc_id: str, paragraph_id: str | None = None, status: str | None = None) -> list[dict]:
    _doc_or_404(doc_id)
    return suggestions.list_for(doc_id, paragraph_id, status)


class GenerateBody(BaseModel):
    paragraph_id: str
    scope: Literal["paragraph", "sentence"] = "paragraph"
    sentence_id: str | None = None
    action: str = "improve_clarity"
    mode: Literal["light", "balanced", "extensive"] | None = None
    custom_instruction: str | None = Field(None, max_length=2000)
    finding_id: str | None = None
    alt_style: str | None = None
    group_id: str | None = None


@router.post("/documents/{doc_id}/suggestions/generate")
async def generate(doc_id: str, body: GenerateBody, request: Request) -> Any:
    _doc_or_404(doc_id)

    async def gen():
        agen = suggestions.generate(doc_id, **body.model_dump())
        gen_id = None
        try:
            async for ev in agen:
                if ev["type"] == "started":
                    gen_id = ev["gen_id"]
                    bus.emit(doc_id, "document.suggestion.started", {"suggestion_id": ev["suggestion_id"], "paragraph_id": body.paragraph_id,
                                                                    "action": body.action}, persist=False)
                if ev["type"] == "completed":
                    bus.emit(doc_id, "document.suggestion.completed", {"id": ev["suggestion"]["id"], "paragraph_id": body.paragraph_id},
                             persist=False)
                yield sse_format(ev, event=ev["type"])
                if await request.is_disconnected() and gen_id:
                    suggestions.cancel(gen_id)
        except asyncio.CancelledError:
            if gen_id:
                suggestions.cancel(gen_id)
            raise

    return sse_response(gen())


@router.post("/generations/{gen_id}/cancel")
def cancel_generation(gen_id: str) -> dict:
    return {"cancelled": suggestions.cancel(gen_id)}


class AcceptBody(BaseModel):
    edited_text: str | None = None


@router.post("/documents/{doc_id}/suggestions/{sid}/accept")
def accept(doc_id: str, sid: str, body: AcceptBody) -> dict:
    try:
        return suggestions.accept(doc_id, sid, body.edited_text)
    except revisions.RevisionError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/documents/{doc_id}/suggestions/{sid}/reject")
def reject(doc_id: str, sid: str) -> dict:
    try:
        return suggestions.reject(doc_id, sid)
    except revisions.RevisionError as e:
        raise HTTPException(409, str(e)) from e


class SuggestionPatch(BaseModel):
    proposed_text: str | None = None
    reviewed: bool | None = None


@router.patch("/documents/{doc_id}/suggestions/{sid}")
def patch_suggestion(doc_id: str, sid: str, body: SuggestionPatch) -> dict:
    s = suggestions.get(sid)
    if not s or s["document_id"] != doc_id:
        raise HTTPException(404, "Suggestion not found")
    if body.proposed_text is not None:
        from app.services.writing.assessment import assess

        meaning, verification = assess(s["original_text"], body.proposed_text, s["base_text"])
        suggestions.update(sid, proposed_text=body.proposed_text, meaning=meaning, verification=verification,
                           source=s["source"] if "edited" in s["source"] else s["source"] + "+edited",
                           status="pending" if s["status"] in ("incomplete", "stale") else s["status"])
    if body.reviewed is not None:
        suggestions.mark_reviewed(sid, body.reviewed)
    return suggestions.get(sid)


class BatchBody(BaseModel):
    ids: list[str]
    action: Literal["accept", "reject"]
    include_verification_required: bool = False


@router.post("/documents/{doc_id}/suggestions/batch")
def batch(doc_id: str, body: BatchBody) -> dict:
    done, skipped = [], []
    for sid in body.ids:
        s = suggestions.get(sid)
        if not s or s["document_id"] != doc_id or s["status"] != "pending":
            skipped.append({"id": sid, "reason": "not pending"})
            continue
        if body.action == "accept":
            ver = s.get("verification") or {}
            if ver.get("required") and not body.include_verification_required:
                skipped.append({"id": sid, "reason": "requires verification — review individually"})
                continue
            if (s.get("meaning") or {}).get("level") == "possible_change" and not body.include_verification_required:
                skipped.append({"id": sid, "reason": "possible meaning change — review individually"})
                continue
        try:
            (suggestions.accept if body.action == "accept" else suggestions.reject)(doc_id, sid)
            done.append(sid)
        except revisions.RevisionError as e:
            skipped.append({"id": sid, "reason": str(e)})
    return {"done": done, "skipped": skipped}


# ------------------------------------------------------------------ paragraphs & revisions
class EditBody(BaseModel):
    text: str


@router.post("/documents/{doc_id}/paragraphs/{pid}/edit")
def edit_paragraph(doc_id: str, pid: str, body: EditBody) -> dict:
    _doc_or_404(doc_id)
    try:
        current = revisions.current_text(doc_id, pid)
        if current == body.text:
            return {"unchanged": True, "text": current}
        op = revisions.record(doc_id, "manual", paragraph_id=pid, after_text=body.text, note="Manual edit")
    except revisions.RevisionError as e:
        raise HTTPException(409, str(e)) from e
    return {"op": op, "text": body.text}


@router.post("/documents/{doc_id}/paragraphs/{pid}/restore")
def restore_paragraph(doc_id: str, pid: str) -> dict:
    _doc_or_404(doc_id)
    st = docs.load_structure(doc_id)
    orig = docs.paragraph_map(st)[pid]["text"]
    if revisions.current_text(doc_id, pid, st) == orig:
        return {"unchanged": True}
    op = revisions.record(doc_id, "restore", paragraph_id=pid, after_text=orig, note="Restored original", check_safety=False)
    # Accepted suggestions on this paragraph are no longer reflected.
    db.execute("UPDATE suggestions SET status='reverted', updated_at=? WHERE document_id=? AND paragraph_id=? AND status='accepted'",
               (db.now(), doc_id, pid))
    return {"op": op, "text": orig}


@router.post("/documents/{doc_id}/paragraphs/{pid}/check")
def check_paragraph(doc_id: str, pid: str, body: EditBody) -> dict:
    doc = _doc_or_404(doc_id)
    problem = revisions.export_safety(doc, pid, body.text)
    return {"safe": problem is None, "reason": problem}


@router.get("/documents/{doc_id}/paragraphs/{pid}/history")
def paragraph_history(doc_id: str, pid: str) -> list[dict]:
    _doc_or_404(doc_id)
    return revisions.paragraph_history(doc_id, pid)


@router.get("/documents/{doc_id}/revisions")
def revision_history(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    return revisions.history(doc_id)


@router.post("/documents/{doc_id}/undo")
def undo(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    op = revisions.undo(doc_id)
    return {"op": op, **{k: v for k, v in revisions.history(doc_id).items() if k != "ops"}}


@router.post("/documents/{doc_id}/redo")
def redo(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    op = revisions.redo(doc_id)
    return {"op": op, **{k: v for k, v in revisions.history(doc_id).items() if k != "ops"}}


# ------------------------------------------------------------------ files & export
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/documents/{doc_id}/preview.docx")
def preview(doc_id: str, version: Literal["current", "original"] = "current") -> Response:
    _doc_or_404(doc_id)
    data = export_service.preview_bytes(doc_id, version)
    return Response(data, media_type=DOCX_MIME, headers={"Cache-Control": "no-store"})


@router.get("/documents/{doc_id}/original.docx")
def original(doc_id: str) -> Response:
    doc = _doc_or_404(doc_id)
    return Response(docs.original_bytes(doc), media_type=DOCX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{_ascii(doc["filename"])}"'})


@router.post("/documents/{doc_id}/export")
def export(doc_id: str) -> dict:
    _doc_or_404(doc_id)
    return export_service.export(doc_id)


@router.get("/documents/{doc_id}/exports")
def exports(doc_id: str) -> list[dict]:
    _doc_or_404(doc_id)
    return export_service.list_exports(doc_id)


@router.get("/exports/{export_id}/download")
def download(export_id: str) -> FileResponse:
    r = db.query_one("SELECT * FROM exports WHERE id=?", (export_id,))
    if not r:
        raise HTTPException(404, "Export not found")
    return FileResponse(r["path"], media_type=DOCX_MIME, filename=r["filename"])


def _ascii(name: str) -> str:
    return name.encode("ascii", "replace").decode().replace('"', "'")
