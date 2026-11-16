"""Shared helpers for analysis modules."""

from __future__ import annotations

from typing import Any

ANALYZABLE_KINDS = {"paragraph", "list_item", "caption"}


def analyzable(para: dict, include_headings: bool = False) -> bool:
    if para["part"] not in ("body", "footnotes", "endnotes"):
        return False
    if para.get("is_reference_entry") or para.get("is_reference_heading"):
        return False
    kinds = ANALYZABLE_KINDS | ({"heading", "title"} if include_headings else set())
    if para["kind"] not in kinds:
        return False
    return para["word_count"] > 0


def sentence_at(para: dict, offset: int) -> str | None:
    for s in para.get("sentences", []):
        if s["start"] <= offset < s["end"]:
            return s["id"]
    return None


def make_finding(module: str, category: str, para: dict | None, *, start: int | None = None, end: int | None = None,
                 explanation: str, correction: str | None = None, priority: str = "medium",
                 confidence: float | None = None, source: str = "rule", data: dict | None = None,
                 passage: str | None = None) -> dict[str, Any]:
    if para is not None and start is not None and end is not None and passage is None:
        passage = para["text"][start:end]
    return {
        "module": module,
        "category": category,
        "paragraph_id": para["id"] if para else None,
        "sentence_id": sentence_at(para, start) if para is not None and start is not None else None,
        "passage": passage,
        "start": start,
        "end": end,
        "explanation": explanation,
        "correction": correction,
        "priority": priority,
        "confidence": confidence,
        "source": source,
        "data": data or {},
    }
