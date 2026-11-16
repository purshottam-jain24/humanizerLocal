"""Thin, resilient wrapper over the existing Ollama client for humanizer calls.

Adds: an overall per-call timeout, retry with backoff for transient failures, cancellation,
removal of leaked reasoning (``<think>…</think>``), lenient JSON extraction, and a progress
callback that reports *real* model activity (thinking / writing, characters produced, and the
latest text the model produced, throttled).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
import time
from typing import Any, Awaitable, Callable

import httpx

from app.services.ollama.ollama_service import GenerationCancelled, OllamaUnavailable, ollama
from app.storage.settings_store import get_settings

CALL_TIMEOUT_S = 900.0  # local reasoning models can be slow; this is a hard ceiling per call
MAX_ATTEMPTS = 3
LIVE_INTERVAL_S = 0.6  # how often live model output is reported to the UI
LIVE_TAIL_CHARS = 420
MIN_CTX = 8192  # rewrite prompts + reasoning need more room than the app default of 4096

Activity = Callable[[dict], Awaitable[None] | None]

# Models that answer without a reasoning phase. With "fast" speed the humanizer uses the first one that is
# installed; reasoning models spend most of their time on hidden reasoning (measured: one 380-word section
# took 8 min 50 s with qwen3:4b reasoning, against ~25 s to write the answer itself).
FAST_MODELS = ("qwen3:4b-instruct-2507-q4_K_M", "qwen3:4b-instruct-2507", "qwen3:4b-instruct")
Speed = str  # "fast" | "thorough"
_speed: contextvars.ContextVar[str] = contextvars.ContextVar("humanizer_speed", default="fast")


_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar("humanizer_usage", default=None)

# Task tiers. "standard" tasks (semantic map, structural rewrite, meaning check, repair) use the model chosen by the
# Speed setting. "light" tasks (targeted vocabulary/style/grammar edits on a few flagged blocks) are simple
# instruction-following, so they always use a model without a reasoning phase when one is installed.
Tier = str  # "standard" | "light"


def use_speed(speed: str) -> None:
    """Select the speed for model calls made from the current task (and tasks it starts)."""
    _speed.set(speed if speed in ("fast", "thorough") else "fast")


def start_usage() -> dict:
    """Start counting model calls and tokens for the current task (and tasks it starts)."""
    u = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0.0, "by_stage": {}, "models": {}}
    _usage.set(u)
    return u


def _record(stage: str, model: str, stats: dict | None, seconds: float) -> None:
    u = _usage.get()
    if u is None:
        return
    p = int((stats or {}).get("prompt_eval_count") or 0)
    o = int((stats or {}).get("eval_count") or 0)
    u["calls"] += 1
    u["prompt_tokens"] += p
    u["output_tokens"] += o
    u["seconds"] = round(u["seconds"] + seconds, 1)
    st = u["by_stage"].setdefault(stage or "other", {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0.0})
    st["calls"] += 1
    st["prompt_tokens"] += p
    st["output_tokens"] += o
    st["seconds"] = round(st["seconds"] + seconds, 1)
    u["models"][model] = u["models"].get(model, 0) + 1


async def resolve_model(speed: str | None = None, tier: Tier = "standard") -> dict:
    """The model and reasoning setting the humanizer will actually use.

    fast: a non-reasoning model if one is installed, otherwise the selected model with reasoning off.
    thorough: the model selected in Settings, with its reasoning (if it has one).
    Light-tier tasks use the fast path whatever the speed setting.
    """
    ms = model_settings()
    speed = speed or _speed.get()
    if speed == "fast" or tier == "light":
        try:
            installed = await ollama.installed_names()
        except Exception:  # noqa: BLE001 - reported by ensure_model below
            installed = set()
        for m in FAST_MODELS:
            if m in installed:
                return {**ms, "model": m, "think": None, "speed": speed, "fast_model": True, "tier": tier}
        return {**ms, "think": False, "speed": speed, "fast_model": False, "tier": tier}
    return {**ms, "think": None, "speed": "thorough", "fast_model": False, "tier": tier}


class LLMError(Exception):
    pass


def model_settings() -> dict:
    s = get_settings()["ollama"]
    return {"model": s["model"], "temperature": float(s.get("temperature") or 0.3), "num_ctx": max(int(s.get("num_ctx") or 4096), MIN_CTX),
            "keep_alive": s.get("keep_alive") or "5m"}


THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_reasoning(text: str) -> str:
    text = THINK_RE.sub("", text)
    # Reasoning leaked without an opening tag: keep only what follows the closing tag.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def _transient(e: Exception) -> bool:
    if isinstance(e, (httpx.TransportError, httpx.TimeoutException, asyncio.TimeoutError)):
        return True
    if isinstance(e, OllamaUnavailable):
        msg = str(e)
        return "not reachable" in msg or re.search(r"error 5\d\d", msg) is not None
    return False


async def complete(messages: list[dict], *, temperature: float | None = None, cancel: asyncio.Event | None = None,
                   on_activity: Activity | None = None, num_predict: int | None = None, label: str = "", tier: Tier = "standard",
                   stage: str = "") -> str:
    """Run a chat completion and return the final answer text (reasoning removed)."""
    ms = await resolve_model(tier=tier)
    await ollama.ensure_model(ms["model"])
    opts: dict[str, Any] = {"temperature": ms["temperature"] if temperature is None else temperature, "num_ctx": ms["num_ctx"]}
    if num_predict:
        opts["num_predict"] = num_predict
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        parts: list[str] = []
        state: dict[str, Any] = {"thinking": False, "chars": 0, "thinking_chars": 0, "think_tail": "", "last": 0.0, "stats": None}

        async def live(kind: str, force: bool = False) -> None:
            # Throttled snapshot of what the model is producing right now, so the UI can show it.
            now = time.monotonic()
            if not force and now - state["last"] < LIVE_INTERVAL_S:
                return
            state["last"] = now
            tail = state["think_tail"] if kind == "thinking" else "".join(parts)[-LIVE_TAIL_CHARS:]
            await _emit(on_activity, {"activity": kind, "label": label, "chars": state["chars"], "thinking_chars": state["thinking_chars"],
                                      "tail": tail[-LIVE_TAIL_CHARS:], "started": call_started, "model": ms["model"]})

        call_started = time.time()

        async def run() -> None:
            async for ch in ollama.chat_stream(ms["model"], messages, options=opts, keep_alive=ms["keep_alive"], cancel=cancel,
                                               think=ms["think"]):
                if ch.thinking:
                    state["thinking_chars"] += len(ch.thinking)
                    state["think_tail"] = (state["think_tail"] + ch.thinking)[-LIVE_TAIL_CHARS:]
                    first = not state["thinking"]
                    state["thinking"] = True
                    await live("thinking", force=first)
                if ch.content:
                    first = not parts
                    parts.append(ch.content)
                    state["chars"] += len(ch.content)
                    await live("writing", force=first)
                if ch.done:
                    state["stats"] = ch.stats
        try:
            await asyncio.wait_for(run(), timeout=CALL_TIMEOUT_S)
            _record(stage or label, ms["model"], state["stats"], time.time() - call_started)
            return strip_reasoning("".join(parts))
        except GenerationCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if cancel is not None and cancel.is_set():
                raise GenerationCancelled() from e
            if attempt < MAX_ATTEMPTS and _transient(e):
                await _emit(on_activity, {"activity": "retrying", "label": label, "reason": _describe(e)})
                await asyncio.sleep(1.5 * 2 ** (attempt - 1))  # 1.5 s, 3 s
                continue
            break
    raise LLMError(_describe(last) if last else "The model call failed.")


def _describe(e: Exception | None) -> str:
    if isinstance(e, asyncio.TimeoutError):
        return f"The local model did not finish within {int(CALL_TIMEOUT_S // 60)} minutes."
    if isinstance(e, OllamaUnavailable):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "The local model stopped responding."
    return (str(e) or e.__class__.__name__)[:300]


async def _emit(cb: Activity | None, ev: dict) -> None:
    if cb is None:
        return
    r = cb(ev)
    if asyncio.iscoroutine(r):
        await r


def parse_json(text: str) -> Any:
    """Extract the first JSON object from a model reply (tolerates code fences and stray prose)."""
    t = strip_reasoning(text)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    start = t.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except ValueError:
                        break
        start = t.find("{", start + 1)
    return None


def parse_issues(text: str) -> dict:
    """Like parse_json for {"issues": [...]}, but if the reply was cut off (output cap) it keeps every
    complete issue object that arrived before the cut."""
    parsed = parse_json(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("issues"), list):
        return parsed
    t = strip_reasoning(text)
    items: list[dict] = []
    for m in re.finditer(r"\{[^{}]*\}", t):  # issue objects are flat
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(obj, dict) and "block" in obj:
            items.append(obj)
    return {"issues": items}


BLOCK_TAG = re.compile(r"<b\s+id=[\"']?(\d+)[\"']?\s*>(.*?)</b>", re.S)


def parse_blocks(text: str) -> dict[int, str]:
    t = strip_reasoning(text)
    out: dict[int, str] = {}
    for m in BLOCK_TAG.finditer(t):
        out[int(m.group(1))] = m.group(2).strip()
    return out


REVISION_TAG = re.compile(r"<revision(?:\s+n=[\"']?(\d+)[\"']?)?\s*>(.*?)</revision>", re.S)


def parse_revisions(text: str) -> list[str]:
    """All <revision>…</revision> (optionally numbered) passages in order; tolerates a missing final closing tag."""
    t = strip_reasoning(text)
    found = [m.group(2).strip() for m in REVISION_TAG.finditer(t) if m.group(2).strip()]
    if not found:
        m = re.search(r"<revision(?:\s+n=[\"']?\d+[\"']?)?\s*>(.*?)(?:<explanation>|$)", t, re.S)
        if m and m.group(1).strip():
            found = [m.group(1).strip()]
    return found
