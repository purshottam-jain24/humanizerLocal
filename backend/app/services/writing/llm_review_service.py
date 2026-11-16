"""Optional AI paragraph review using the selected local Ollama model.

The model's findings are only kept when the passage it quotes exists
verbatim in the paragraph, which filters out hallucinated issues.
"""

from __future__ import annotations

import asyncio
import json

from app.services.analysis.common import make_finding
from app.services.ollama.ollama_service import ollama
from app.services.writing.prompts import REVIEW_SCHEMA, review_messages

CATEGORY_MAP = {
    "awkward_wording": "awkward_wording", "clarity": "clarity", "wordiness": "wordiness", "transition": "transition",
    "vague_statement": "vague_statement", "tone": "tone", "complexity": "complexity", "flow": "flow",
    "grammar": "grammar", "spelling": "spelling", "punctuation": "punctuation",
}


async def review_paragraph(model: str, para: dict, prev_text: str | None, title: str | None, settings: dict,
                           cancel: asyncio.Event | None = None) -> list[dict]:
    text = para["text"].replace("￼", "")
    if para["word_count"] < 6:
        return []
    raw = await ollama.chat(model, review_messages(text, prev_text, title), fmt=REVIEW_SCHEMA, cancel=cancel,
                            options={"temperature": 0.1, "num_ctx": settings["ollama"]["num_ctx"]},
                            keep_alive=settings["ollama"]["keep_alive"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    used = set()
    for it in (data.get("issues") or [])[:6]:
        passage = (it.get("passage") or "").strip()
        correction = it.get("correction")
        if len(passage) < 2 or passage not in para["text"]:
            continue  # quote not found verbatim → discard
        start = para["text"].find(passage)
        if (start, passage) in used:
            continue
        used.add((start, passage))
        if correction is not None and (correction.strip() == passage or not correction.strip()):
            correction = None
        out.append(make_finding(
            "writing", CATEGORY_MAP.get(it.get("category"), "clarity"), para, start=start, end=start + len(passage),
            explanation=(it.get("explanation") or "").strip()[:500] or "Suggested improvement.",
            correction=correction.strip() if correction else None,
            priority=it.get("priority") if it.get("priority") in ("high", "medium", "low") else "medium",
            source="llm", data={"model": model},
        ))
    return out
