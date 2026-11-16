"""Humanizer endpoints: options, extraction, analysis, full rewrite (SSE), selection rewrite (SSE),
validation, style profiles and export.

The humanizer is independent of the AI detector: nothing here reads detector scores, and
detector code is not imported.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.api.sse import sse_format, sse_response
from app.api.system import _auto_select_models
from app.services.extraction import ocr_service
from app.services.humanizer import analysis, documents, editing, llm, pipeline, prompts, style_profiles, validation
from app.services.humanizer.blocks import parse_blocks
from app.services.ollama.ollama_service import GenerationCancelled, ollama

router = APIRouter(prefix="/api/humanize", tags=["humanizer"])

Mode = Literal["natural", "conversational", "academic", "professional", "simple", "personal", "custom"]
Intensity = Literal["light", "medium", "extensive"]
Approach = Literal["balanced", "restructured", "concise", "flowing"]
SelectionAction = Literal["improve", "natural", "simplify", "professional", "conversational", "clarity", "redundancy", "rephrase",
                          "expand", "shorten", "flow", "tone", "custom"]
Tone = Literal["formal", "friendly", "confident", "neutral", "persuasive", "empathetic", "casual"]


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """Sliding-window limiter per client and bucket (in-process; the app is single-user and local)."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, request: Request, bucket: str, limit: int, window_s: float) -> None:
        key = (request.client.host if request.client else "local", bucket)
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            retry = int(window_s - (now - q[0])) + 1
            raise HTTPException(429, f"Too many requests. Try again in {retry} s.", headers={"Retry-After": str(retry)})
        q.append(now)


limiter = RateLimiter()
_active_runs: dict[str, asyncio.Event] = {}
MAX_CONCURRENT_RUNS = 2


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")


# ------------------------------------------------------------------ options
@router.get("/options")
async def options() -> dict:
    status = await ollama.status()
    _auto_select_models(status)  # same zero-config model choice as /api/system
    ms = llm.model_settings()
    installed = {m["name"] for m in status.get("models", [])}
    model_ok = bool(status.get("available") and ms["model"] and (ms["model"] in installed or f"{ms['model']}:latest" in installed))
    return {
        "modes": [{"value": k, "label": v["label"], "hint": v["hint"]} for k, v in prompts.MODES.items()],
        "intensities": [{"value": k, "label": v["label"], "hint": v["hint"]} for k, v in prompts.INTENSITIES.items()],
        "document_types": prompts.DOCUMENT_TYPES,
        "selection_actions": [{"value": k, "label": prompts.SELECTION_LABELS[k]} for k in prompts.SELECTION_ACTIONS],
        "approaches": [{"value": k, "label": v["label"], "hint": v["hint"]} for k, v in prompts.APPROACHES.items()],
        "tones": [{"value": k, "label": k.capitalize(), "hint": v} for k, v in prompts.TONES.items()],
        "max_variations": pipeline.MAX_VARIATIONS,
        "stages": [{"key": k, "label": l} for k, l in pipeline.STAGES],
        "provider": {
            "name": "Ollama", "url": ollama.base_url, "local": _is_local(ollama.base_url), "available": bool(status.get("available")),
            "model": ms["model"], "model_ready": model_ok, "error": status.get("error"),
        },
        # Fast: a model without a reasoning phase if installed (otherwise the selected model with reasoning off).
        # Thorough: the model selected in Settings, reasoning included.
        "speed": {"fast_model": next((m for m in llm.FAST_MODELS if m in installed), None), "recommended_fast_model": llm.FAST_MODELS[0],
                  "thorough_model": ms["model"]},
        "upload": {"extensions": documents.supported_extensions(), "max_mb": documents.MAX_FILE_BYTES // (1024 * 1024),
                   "ocr_available": ocr_service.available()},
        "limits": {"max_chars": pipeline.MAX_INPUT_CHARS, "max_selection_chars": editing.MAX_SELECTION_CHARS,
                   "min_profile_words": style_profiles.MIN_SAMPLE_WORDS},
        "privacy": ("Text is processed by Ollama on this computer and is not sent to any online service. "
                    "Humanizer inputs and results are not saved on the server; only writing profiles you create are stored."
                    if _is_local(ollama.base_url) else
                    f"Text is sent to the Ollama server at {ollama.base_url}, which is not on this computer."),
    }


# ------------------------------------------------------------------ input
@router.post("/extract")
async def extract(request: Request, file: UploadFile = File(...)) -> dict:
    limiter.check(request, "extract", 30, 60)
    data = await file.read(documents.MAX_FILE_BYTES + 1)
    try:
        return await asyncio.to_thread(documents.extract, file.filename or "upload", data)
    except documents.ExtractionError as e:
        raise HTTPException(422, str(e)) from e


class TextBody(BaseModel):
    text: str = Field(..., max_length=pipeline.MAX_INPUT_CHARS)


@router.post("/analyze")
def analyze(body: TextBody) -> dict:
    blocks = parse_blocks(body.text)
    pipeline._mark_unrewritable(blocks)
    return analysis.analyze(blocks)


# ------------------------------------------------------------------ full rewrite
class RunBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=pipeline.MAX_INPUT_CHARS)
    mode: Mode = "natural"
    intensity: Intensity = "medium"
    profile_id: str | None = Field(None, max_length=64)
    custom_instructions: str | None = Field(None, max_length=2000)
    document_type: str | None = Field(None, max_length=60)
    audience: str | None = Field(None, max_length=200)
    ai_validation: bool = True
    speed: Literal["fast", "thorough"] = "fast"
    approaches: list[Approach] = Field(default_factory=lambda: ["balanced"], min_length=1, max_length=pipeline.MAX_VARIATIONS)
    run_id: str | None = Field(None, max_length=64)

    @field_validator("text")
    @classmethod
    def _has_words(cls, v: str) -> str:
        if not any(c.isalnum() for c in v):
            raise ValueError("Enter some text to rewrite.")
        return v


@router.post("/run")
async def run(body: RunBody, request: Request) -> Any:
    limiter.check(request, "run", 12, 60)
    if body.mode == "custom" and not (body.custom_instructions or "").strip():
        raise HTTPException(422, "Describe the writing style you want for Custom mode.")
    if body.profile_id and not style_profiles.get_profile(body.profile_id):
        raise HTTPException(404, "That writing profile no longer exists.")
    if len(_active_runs) >= MAX_CONCURRENT_RUNS:
        raise HTTPException(429, "Another rewrite is already running. Wait for it to finish or cancel it.")
    run_id = body.run_id or f"run_{int(time.time() * 1000)}"
    cancel = asyncio.Event()
    _active_runs[run_id] = cancel
    params = pipeline.Params(text=body.text, mode=body.mode, intensity=body.intensity, profile_id=body.profile_id,
                             custom_instructions=body.custom_instructions, document_type=body.document_type,
                             audience=(body.audience or "").strip() or None, ai_validation=body.ai_validation,
                             speed=body.speed, approaches=list(dict.fromkeys(body.approaches)))

    async def gen():
        try:
            yield sse_format({"type": "accepted", "run_id": run_id}, event="accepted")
            async for ev in pipeline.run(params, cancel):
                yield sse_format(ev, event=ev["type"])
                if await request.is_disconnected():
                    cancel.set()
                    break
        except asyncio.CancelledError:
            cancel.set()
            raise
        finally:
            _active_runs.pop(run_id, None)

    return sse_response(gen())


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    ev = _active_runs.get(run_id)
    if ev:
        ev.set()
    return {"cancelled": bool(ev)}


# ------------------------------------------------------------------ selection
class SelectionBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=editing.MAX_SELECTION_CHARS)
    before: str = Field("", max_length=20_000)
    after: str = Field("", max_length=20_000)
    action: SelectionAction = "improve"
    tone: Tone | None = None
    alternatives: int = Field(1, ge=1, le=3)
    mode: Mode = "natural"
    custom_instruction: str | None = Field(None, max_length=1000)
    profile_id: str | None = Field(None, max_length=64)
    audience: str | None = Field(None, max_length=200)


@router.post("/rewrite-selection")
async def rewrite_selection(body: SelectionBody, request: Request) -> Any:
    limiter.check(request, "selection", 40, 60)
    if not any(c.isalnum() for c in body.text):
        raise HTTPException(422, "Select some text to rewrite.")
    if body.action == "custom" and not (body.custom_instruction or "").strip():
        raise HTTPException(422, "Type an instruction for the custom rewrite.")
    cancel = asyncio.Event()

    async def gen():
        try:
            async for ev in editing.rewrite_selection(text=body.text, before=body.before, after=body.after, action=body.action,
                                                      mode=body.mode, custom_instruction=body.custom_instruction, tone=body.tone,
                                                      alternatives=body.alternatives,
                                                      profile_id=body.profile_id, audience=body.audience, cancel=cancel):
                yield sse_format(ev, event=ev["type"])
                if await request.is_disconnected():
                    cancel.set()
                    break
        except asyncio.CancelledError:
            cancel.set()
            raise

    return sse_response(gen())


# ------------------------------------------------------------------ validation
class ValidateBody(BaseModel):
    original: str = Field(..., max_length=pipeline.MAX_INPUT_CHARS)
    revised: str = Field(..., max_length=pipeline.MAX_INPUT_CHARS * 2)
    key_terms: list[str] = Field(default_factory=list, max_length=60)


@router.post("/validate")
def validate(body: ValidateBody) -> dict:
    """Deterministic re-check of an edited result against the original (no AI, instant)."""
    return validation.validate_texts(body.original, body.revised, body.key_terms)


# ------------------------------------------------------------------ style profiles
@router.get("/profiles")
def list_profiles() -> list[dict]:
    return style_profiles.list_profiles()


class ProfileAnalyzeBody(BaseModel):
    samples: str = Field(..., min_length=1, max_length=style_profiles.MAX_SAMPLE_CHARS * 2)


@router.post("/profiles/analyze")
async def analyze_profile(body: ProfileAnalyzeBody, request: Request) -> dict:
    """Analyse samples and return a draft profile. Samples are not stored."""
    limiter.check(request, "profile", 10, 60)
    cancel = asyncio.Event()
    task = asyncio.create_task(editing.build_style_profile(body.samples, cancel))
    try:
        while not task.done():
            await asyncio.sleep(0.5)
            if await request.is_disconnected():
                cancel.set()
                break
        return await task
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except GenerationCancelled as e:
        raise HTTPException(499, "Cancelled") from e


class ProfileBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=1500)
    traits: list[str] = Field(default_factory=list, max_length=20)
    expressions: list[str] = Field(default_factory=list, max_length=30)
    stats: dict = Field(default_factory=dict)
    sample_words: int = 0


@router.post("/profiles")
def create_profile(body: ProfileBody) -> dict:
    return style_profiles.create_profile(name=body.name, description=body.description, traits=body.traits,
                                         expressions=body.expressions, stats=body.stats, sample_words=body.sample_words)


class ProfilePatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    description: str | None = Field(None, max_length=1500)
    traits: list[str] | None = Field(None, max_length=20)
    expressions: list[str] | None = Field(None, max_length=30)
    stats: dict | None = None
    sample_words: int | None = None


@router.patch("/profiles/{pid}")
def update_profile(pid: str, body: ProfilePatch) -> dict:
    p = style_profiles.update_profile(pid, body.model_dump(exclude_none=True))
    if not p:
        raise HTTPException(404, "Profile not found")
    return p


@router.delete("/profiles/{pid}")
def delete_profile(pid: str) -> dict:
    if not style_profiles.delete_profile(pid):
        raise HTTPException(404, "Profile not found")
    return {"ok": True}


# ------------------------------------------------------------------ export
class ExportBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=pipeline.MAX_INPUT_CHARS * 2)
    format: Literal["txt", "md", "docx"] = "txt"
    filename: str = Field("humanwrite", max_length=120)


@router.post("/export")
def export(body: ExportBody) -> Response:
    base = "".join(c for c in body.filename if c.isalnum() or c in " -_().").strip() or "humanwrite"
    if body.format == "docx":
        data = documents.to_docx(body.text, title=base)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif body.format == "md":
        data, media = documents.to_markdown(body.text), "text/markdown; charset=utf-8"
    else:
        data, media = documents.to_plain(body.text), "text/plain; charset=utf-8"
    name = f"{base}.{body.format}"
    ascii_name = name.encode("ascii", "ignore").decode() or f"humanwrite.{body.format}"
    return Response(data, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"})

