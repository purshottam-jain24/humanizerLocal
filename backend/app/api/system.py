"""Settings, local AI (Ollama), corpus and privacy endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.api.sse import sse_format, sse_response
from app.services.document import document_service as docs
from app.services.extraction import ocr_service
from app.services.ollama.ollama_service import RECOMMENDED_MODELS, OllamaUnavailable, ollama
from app.services.plagiarism import corpus_index_service as corpus
from app.services.plagiarism.external_source_service import PROVIDERS
from app.storage import db
from app.storage.settings_store import get_settings, update_settings
from app.workers import processing

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"ok": True}


PREFERRED_WRITING = ["qwen3:4b", "gemma3:4b", "qwen3:8b"]
PREFERRED_EMBED = ["embeddinggemma:latest", "embeddinggemma", "all-minilm:latest", "all-minilm"]


def _auto_select_models(status: dict) -> None:
    """Zero-config: if no model is chosen yet, pick the best installed one."""
    if not status.get("available"):
        return
    s = get_settings()
    names = [m["name"] for m in status["models"]]
    patch: dict = {}
    if not s["ollama"]["model"] or s["ollama"]["model"] not in names:
        writing = [m["name"] for m in status["models"] if not m["is_embedding"]]
        pick = next((n for n in PREFERRED_WRITING if n in names), writing[0] if writing else None)
        if pick and pick != s["ollama"]["model"]:
            patch["model"] = pick
    if not s["ollama"]["embed_model"]:
        pick = next((n for n in PREFERRED_EMBED if n in names), None)
        if pick:
            patch["embed_model"] = pick
    if patch:
        update_settings({"ollama": patch})


@router.get("/system")
async def system() -> dict:
    status = await ollama.status()
    _auto_select_models(status)
    return {
        "ollama": status,
        "recommended_models": RECOMMENDED_MODELS,
        "storage": docs.storage_info(),
        "corpus": corpus.corpus_stats(),
        "ocr_available": ocr_service.available(),
        "external_providers": PROVIDERS,
        "settings": get_settings(),
    }


@router.get("/settings")
def read_settings() -> dict:
    return get_settings()


@router.patch("/settings")
def patch_settings(patch: dict[str, Any]) -> dict:
    return update_settings(patch)


# ------------------------------------------------------------------ Ollama
@router.get("/ollama/status")
async def ollama_status() -> dict:
    return await ollama.status()


class PullBody(BaseModel):
    model: str
    confirm: bool = False


@router.post("/ollama/pull")
async def pull(body: PullBody, request: Request) -> Any:
    if not body.confirm:
        raise HTTPException(400, "Model downloads require explicit confirmation.")

    async def gen():
        try:
            async for ev in ollama.pull(body.model):
                if await request.is_disconnected():
                    break
                yield sse_format(ev, event="progress")
            yield sse_format({"status": "success"}, event="done")
        except Exception as e:  # noqa: BLE001
            yield sse_format({"error": str(e)[:300]}, event="error")

    return sse_response(gen())


@router.delete("/ollama/models/{name:path}")
async def delete_model(name: str) -> dict:
    try:
        await ollama.delete(name)
    except OllamaUnavailable as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


class ModelBody(BaseModel):
    model: str


@router.post("/ollama/test")
async def test_model(body: ModelBody) -> dict:
    try:
        await ollama.ensure_model(body.model)
        return await ollama.test(body.model)
    except OllamaUnavailable as e:
        return {"ok": False, "error": str(e)}


@router.post("/ollama/unload")
async def unload(body: ModelBody) -> dict:
    try:
        await ollama.unload(body.model)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True}


# ------------------------------------------------------------------ corpus
@router.get("/corpus")
def corpus_list() -> dict:
    return {"sources": corpus.list_sources(), "stats": corpus.corpus_stats()}


@router.post("/corpus/upload")
async def corpus_upload(files: list[UploadFile] = File(...)) -> dict:
    imported, failed = [], []
    for f in files:
        try:
            imported.append(corpus.import_bytes(f.filename or "file.txt", await f.read()))
        except Exception as e:  # noqa: BLE001
            failed.append({"file": f.filename, "error": str(e)[:200]})
    return {"imported": imported, "failed": failed}


class FolderBody(BaseModel):
    path: str
    recursive: bool = True


@router.post("/corpus/folder")
def corpus_folder(body: FolderBody) -> dict:
    p = Path(body.path).expanduser()
    try:
        return corpus.import_folder(p, body.recursive)
    except corpus.CorpusImportError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/corpus/{source_id}")
def corpus_delete(source_id: str) -> dict:
    corpus.delete_source(source_id)
    return {"ok": True}


@router.post("/corpus/embed")
async def corpus_embed(request: Request) -> Any:
    model = get_settings()["ollama"]["embed_model"]

    async def gen():
        try:
            await ollama.ensure_model(model)
            queue: list[dict] = []

            def prog(done: int, total: int) -> None:
                queue.append({"done": done, "total": total})

            import asyncio

            task = asyncio.create_task(corpus.embed_corpus(model, ollama, prog))
            while not task.done():
                await asyncio.sleep(0.3)
                while queue:
                    yield sse_format(queue.pop(0), event="progress")
            n = task.result()
            yield sse_format({"embedded": n, "model": model}, event="done")
        except Exception as e:  # noqa: BLE001
            yield sse_format({"error": str(e)[:300]}, event="error")

    return sse_response(gen())


# ------------------------------------------------------------------ privacy
@router.post("/privacy/clear-history")
def clear_history() -> dict:
    ids = [r["id"] for r in db.query("SELECT id FROM documents")]
    for i in ids:
        processing.cancel(i)
        docs.delete_document(i)
    return {"deleted": len(ids)}


@router.get("/privacy/report")
def privacy_report() -> dict:
    s = get_settings()
    return {
        "local_only": not s["privacy"]["external_sources"],
        "ai_runtime": f"Ollama at {ollama.base_url} (on this Mac)",
        "leaves_device": (["Short sentences from analysed documents are sent as search queries to: " +
                           ", ".join(PROVIDERS[p] for p in s["privacy"]["external_providers"] if p in PROVIDERS)]
                          if s["privacy"]["external_sources"] else []),
        "never_leaves_device": ["Your documents", "Analysis results", "Suggestions and revisions", "Reference corpus",
                                "AI prompts and outputs (processed by local Ollama)"],
        "storage": docs.storage_info(),
        "temp_dir": tempfile.gettempdir(),
    }
