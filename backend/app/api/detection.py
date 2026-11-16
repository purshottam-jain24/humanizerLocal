"""AI-generated text detection endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.sse import sse_format, sse_response
from app.events.document_event_service import bus
from app.services.detection import ai_detector_service as detector
from app.services.document import document_service as docs
from app.services.writing import revision_service as revisions

router = APIRouter(prefix="/api")


class InstallBody(BaseModel):
    confirm: bool = False


@router.get("/ai-detector")
def detector_status() -> dict:
    return detector.status()


@router.post("/ai-detector/install")
async def install(body: InstallBody, request: Request) -> Any:
    if not body.confirm:
        raise HTTPException(400, "Installing the AI detector requires explicit confirmation.")

    async def gen():
        async for ev in detector.install():
            if await request.is_disconnected():
                break
            yield sse_format(ev, event=ev["phase"] if ev["phase"] in ("done", "error") else "progress")

    return sse_response(gen())


@router.delete("/ai-detector")
def remove() -> dict:
    detector.remove_models()
    return detector.status()


_running: set[str] = set()


@router.post("/documents/{doc_id}/ai-detection")
async def run_detection(doc_id: str) -> dict:
    """Run (or re-run) detection on the document's current text, including accepted edits."""
    doc = docs.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    if not detector.ready():
        raise HTTPException(409, "AI detection isn't set up yet. Turn it on in Settings → AI detection.")
    if doc_id in _running:
        raise HTTPException(409, "AI detection is already running for this document.")
    _running.add(doc_id)
    try:
        paragraphs = revisions.current_paragraphs(doc_id)
        bus.emit(doc_id, "document.analysis.started", {"module": "ai_detection"})

        def prog(done: int, total: int) -> None:
            bus.emit(doc_id, "document.analysis.progress", {"module": "ai_detection", "done": done, "total": total}, persist=False)

        report = await asyncio.to_thread(detector.analyze, paragraphs, prog)
        report["text_version"] = "current" if doc["revision_head"] else "original"
        analysis = docs.load_analysis(doc_id)
        analysis["ai_detection"] = report
        docs.save_analysis(doc_id, analysis)
        bus.emit(doc_id, "document.analysis.completed", {"module": "ai_detection", "ai_percent": report.get("ai_percent")})
        return report
    finally:
        _running.discard(doc_id)


class TextDetectionBody(BaseModel):
    text: str


_text_running = asyncio.Lock()


@router.post("/ai-detector/analyze-text")
async def analyze_text(body: TextDetectionBody) -> dict:
    """Score arbitrary text with the existing detector, using its unchanged methodology.

    This is a standalone check the user triggers (e.g. from the Humanizer workspace). Its result is
    returned to the user only; no other feature reads it or acts on it.
    """
    if not detector.ready():
        raise HTTPException(409, "AI detection isn't set up yet. Turn it on in Settings → AI detection.")
    if len(body.text) > 240_000:
        raise HTTPException(413, "The text is too long for a single check.")
    paragraphs = []
    for i, block in enumerate(p for p in body.text.replace("\r\n", "\n").split("\n\n") if p.strip()):
        stripped = block.strip()
        is_heading = stripped.startswith("#") and "\n" not in stripped
        paragraphs.append({"id": f"t{i}", "part": "body", "table": None, "kind": "heading" if is_heading else "paragraph",
                           "text": stripped.lstrip("#").strip() if is_heading else stripped})
    if _text_running.locked():
        raise HTTPException(409, "A detection check is already running.")
    async with _text_running:
        return await asyncio.to_thread(detector.analyze, paragraphs)
