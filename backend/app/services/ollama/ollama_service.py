"""Client for the local Ollama runtime (http://127.0.0.1:11434 by default).

Ollama is the only AI runtime. There is no cloud fallback: when Ollama is
not reachable or no model is selected, AI features report that state
instead of silently degrading to another provider.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.core.config import config

# Models HumanWrite has been designed around. Sizes are approximate download
# sizes of the default (Q4) tags; RAM is a practical working estimate.
RECOMMENDED_MODELS = [
    {"name": "qwen3:4b", "role": "writing", "size_gb": 2.5, "ram_gb": 4.5,
     "notes": "Recommended default for 16 GB Macs. A reasoning model: it thinks briefly before writing, which adds a few seconds per request."},
    {"name": "qwen3:4b-instruct-2507-q4_K_M", "role": "writing", "size_gb": 2.5, "ram_gb": 4.5,
     "notes": "Same size as qwen3:4b but answers without a thinking phase, so suggestions arrive noticeably faster."},
    {"name": "gemma3:4b", "role": "writing", "size_gb": 3.3, "ram_gb": 5.0,
     "notes": "Strong prose quality and multilingual support. Slightly slower than qwen3:4b."},
    {"name": "qwen3:8b", "role": "writing", "size_gb": 5.2, "ram_gb": 8.0,
     "notes": "Higher quality, roughly half the speed. Close other heavy apps on 16 GB machines."},
    {"name": "embeddinggemma", "role": "embedding", "size_gb": 0.6, "ram_gb": 1.0,
     "notes": "Local embeddings for semantic similarity against your corpus."},
    {"name": "all-minilm", "role": "embedding", "size_gb": 0.05, "ram_gb": 0.3,
     "notes": "Very small, fast embedding model; lower accuracy than embeddinggemma."},
]


class OllamaUnavailable(Exception):
    pass


class GenerationCancelled(Exception):
    pass


@dataclass
class StreamChunk:
    content: str
    done: bool
    stats: dict | None = None
    thinking: str = ""


class OllamaService:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or config.ollama_url).rstrip("/")
        self._gen_semaphore = asyncio.Semaphore(config.max_concurrent_generations)
        self._embed_semaphore = asyncio.Semaphore(1)
        self._caps: dict[str, list[str]] = {}

    async def capabilities(self, model: str) -> list[str]:
        """Model capabilities from /api/show (e.g. 'completion', 'thinking'); cached."""
        if model not in self._caps:
            try:
                async with self._client(10.0) as c:
                    r = await c.post("/api/show", json={"model": model})
                    self._caps[model] = (r.json().get("capabilities") or []) if r.status_code < 400 else []
            except (httpx.HTTPError, ValueError):
                return []
        return self._caps[model]

    def _client(self, timeout: float | None = 10.0) -> httpx.AsyncClient:
        # trust_env=False: never route local model traffic through a proxy.
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, trust_env=False)

    # ---------------------------------------------------------------- status
    async def status(self) -> dict[str, Any]:
        try:
            async with self._client(3.0) as c:
                v = (await c.get("/api/version")).json()
                tags = (await c.get("/api/tags")).json()
                try:
                    ps = (await c.get("/api/ps")).json()
                except Exception:  # noqa: BLE001
                    ps = {"models": []}
        except (httpx.HTTPError, ValueError) as e:
            return {"available": False, "url": self.base_url, "error": _friendly_error(e), "models": [], "loaded": []}
        models = []
        for m in tags.get("models", []):
            det = m.get("details") or {}
            models.append({
                "name": m.get("name"),
                "size_bytes": m.get("size"),
                "family": det.get("family"),
                "parameter_size": det.get("parameter_size"),
                "quantization": det.get("quantization_level"),
                "modified_at": m.get("modified_at"),
                "is_embedding": _looks_like_embedding(m.get("name", ""), det),
            })
        loaded = [{"name": m.get("name"), "size_vram": m.get("size_vram"), "expires_at": m.get("expires_at")}
                  for m in ps.get("models", [])]
        return {"available": True, "url": self.base_url, "version": v.get("version"), "models": models, "loaded": loaded}

    async def installed_names(self) -> set[str]:
        st = await self.status()
        return {m["name"] for m in st["models"]}

    async def ensure_model(self, model: str | None) -> str:
        if not model:
            raise OllamaUnavailable("No local model selected. Choose an installed model in Settings → Local AI.")
        st = await self.status()
        if not st["available"]:
            raise OllamaUnavailable(f"Ollama is not reachable at {self.base_url}. Start the Ollama app (or `ollama serve`).")
        names = {m["name"] for m in st["models"]}
        if model not in names and f"{model}:latest" not in names:
            raise OllamaUnavailable(f"Model '{model}' is not installed. Download it from Settings → Local AI.")
        return model

    # ------------------------------------------------------------- pull/delete
    async def pull(self, model: str) -> AsyncIterator[dict]:
        """Stream pull progress. Only ever called after explicit user confirmation."""
        async with self._client(None) as c:
            async with c.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r:
                if r.status_code >= 400:
                    body = await r.aread()
                    raise OllamaUnavailable(body.decode(errors="replace")[:300])
                async for line in r.aiter_lines():
                    if line.strip():
                        yield json.loads(line)

    async def delete(self, model: str) -> None:
        async with self._client(30.0) as c:
            r = await c.request("DELETE", "/api/delete", json={"model": model})
            if r.status_code >= 400:
                raise OllamaUnavailable(r.text[:300])

    async def unload(self, model: str) -> None:
        async with self._client(10.0) as c:
            await c.post("/api/generate", json={"model": model, "keep_alive": 0})

    # ------------------------------------------------------------- generation
    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        *,
        options: dict | None = None,
        keep_alive: str | None = None,
        fmt: Any = None,
        cancel: asyncio.Event | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "options": options or {}}
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if fmt is not None:
            payload["format"] = fmt
        # Reasoning models: some (e.g. qwen3 2507 "thinking" builds) cannot switch reasoning off and
        # would put it into the answer with think=false. Enabling it keeps reasoning separate;
        # we only ever use the final answer (`content`). Callers that need speed can pass think=False
        # explicitly; any reasoning that still leaks into the answer is removed by the caller.
        if "thinking" in await self.capabilities(model):
            payload["think"] = True if think is None else think
        async with self._gen_semaphore:
            try:
                async with self._client(httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)) as c:
                    async with c.stream("POST", "/api/chat", json=payload) as r:
                        if r.status_code >= 400:
                            body = (await r.aread()).decode(errors="replace")
                            if "think" in body and "think" in payload:
                                # Older Ollama / model without thinking support: retry without the flag.
                                payload.pop("think")
                                async for ch in self._retry_plain(c, payload, cancel):
                                    yield ch
                                return
                            raise OllamaUnavailable(f"Ollama error {r.status_code}: {body[:300]}")
                        async for ch in self._iter_chat(r, cancel):
                            yield ch
            except httpx.ConnectError as e:
                raise OllamaUnavailable(f"Ollama is not reachable at {self.base_url}.") from e

    async def _retry_plain(self, c: httpx.AsyncClient, payload: dict, cancel: asyncio.Event | None) -> AsyncIterator[StreamChunk]:
        async with c.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code >= 400:
                raise OllamaUnavailable(f"Ollama error {r.status_code}: {(await r.aread()).decode(errors='replace')[:300]}")
            async for ch in self._iter_chat(r, cancel):
                yield ch

    async def _iter_chat(self, r: httpx.Response, cancel: asyncio.Event | None) -> AsyncIterator[StreamChunk]:
        async for line in r.aiter_lines():
            if cancel is not None and cancel.is_set():
                # Closing the stream aborts generation server-side.
                raise GenerationCancelled()
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("error"):
                raise OllamaUnavailable(str(data["error"]))
            msg = data.get("message") or {}
            content = msg.get("content") or ""
            thinking = msg.get("thinking") or ""
            if data.get("done"):
                stats = {k: data.get(k) for k in ("total_duration", "eval_count", "eval_duration", "prompt_eval_count")}
                yield StreamChunk(content, True, stats)
                return
            if content or thinking:
                yield StreamChunk(content, False, thinking=thinking)

    async def chat(self, model: str, messages: list[dict], *, options: dict | None = None, keep_alive: str | None = None,
                   fmt: Any = None, cancel: asyncio.Event | None = None) -> str:
        parts = []
        async for ch in self.chat_stream(model, messages, options=options, keep_alive=keep_alive, fmt=fmt, cancel=cancel):
            parts.append(ch.content)
        return "".join(parts)

    # -------------------------------------------------------------- embeddings
    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        async with self._embed_semaphore:
            async with self._client(120.0) as c:
                r = await c.post("/api/embed", json={"model": model, "input": inputs, "truncate": True})
                if r.status_code >= 400:
                    raise OllamaUnavailable(f"Embedding failed: {r.text[:200]}")
                return r.json().get("embeddings", [])

    async def test(self, model: str) -> dict:
        import time
        t0 = time.perf_counter()
        first = None
        text = []
        stats = None
        async for ch in self.chat_stream(model, [{"role": "user", "content": "Reply with exactly: Ready."}],
                                         options={"temperature": 0, "num_predict": 1024}):
            if first is None and (ch.content or ch.thinking):
                first = time.perf_counter() - t0
            text.append(ch.content)
            if ch.done:
                stats = ch.stats
        total = time.perf_counter() - t0
        tps = None
        if stats and stats.get("eval_count") and stats.get("eval_duration"):
            tps = stats["eval_count"] / (stats["eval_duration"] / 1e9)
        return {"ok": True, "reply": "".join(text).strip()[:80], "first_token_s": first, "total_s": total, "tokens_per_s": tps}


def _looks_like_embedding(name: str, details: dict) -> bool:
    n = name.lower()
    fam = (details.get("family") or "").lower()
    return any(k in n for k in ("embed", "minilm", "bge", "nomic", "e5")) or fam in ("bert", "nomic-bert", "gemma-embedding")


def _friendly_error(e: Exception) -> str:
    if isinstance(e, httpx.ConnectError):
        return "Ollama is not running. Install it from https://ollama.com and open the app, or run `ollama serve`."
    if isinstance(e, httpx.TimeoutException):
        return "Ollama did not respond in time."
    return str(e)[:200]


ollama = OllamaService()
