"""Deterministic sentence and word segmentation (no model required)."""

from __future__ import annotations

import re

ABBREVIATIONS = {
    "e.g", "i.e", "etc", "et al", "al", "vs", "cf", "fig", "figs", "eq", "eqs", "no", "nos", "vol", "vols",
    "pp", "p", "ch", "sec", "dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "inc", "ltd", "co", "corp",
    "approx", "dept", "est", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "ed", "eds", "rev", "u.s", "u.k", "ph.d", "m.sc", "b.sc", "viz", "resp", "ca", "op", "cit", "ibid",
}

_SENT_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+|$)")
WORD_RE = re.compile(r"[\w’'\-]+", re.UNICODE)
TOKEN_RE = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]", re.UNICODE)


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character spans of sentences in ``text``."""
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    while start < n and text[start].isspace():
        start += 1
    for m in _SENT_END.finditer(text):
        end = m.end()
        if end <= start:
            continue
        # Check abbreviation / initials / decimal numbers before the period.
        before = text[start:m.start()]
        last_word = re.search(r"([\w.]+)$", before)
        if m.group().startswith(".") and last_word:
            lw = last_word.group(1).lower().rstrip(".")
            if lw in ABBREVIATIONS or (len(lw) == 1 and lw.isalpha()):
                continue
        # Next non-space char should look like a sentence start.
        rest = text[end:]
        nxt = re.match(r"\s*(\S)", rest)
        if nxt and not (nxt.group(1).isupper() or nxt.group(1).isdigit() or nxt.group(1) in "\"'“‘([" or not nxt.group(1).isalpha()):
            continue
        spans.append((start, end))
        start = end
        while start < n and text[start].isspace():
            start += 1
    if start < n and text[start:].strip():
        end = n
        while end > start and text[end - 1].isspace():
            end -= 1
        spans.append((start, end))
    return spans


def word_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text)]


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))
