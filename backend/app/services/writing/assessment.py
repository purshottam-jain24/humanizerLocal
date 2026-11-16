"""Automated meaning-preservation and verification checks for proposed text.

These are deterministic, explainable checks — not a guarantee. They catch
the most damaging failure modes of rewriting: dropped or changed numbers,
citations, negations, named entities and protected elements, and newly
introduced facts that would need verification.
"""

from __future__ import annotations

import re

from app.services.extraction.sentences import WORD_RE

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*%?")
CITE_RE = re.compile(r"\([A-Z][^()]*\d{4}[a-z]?[^()]*\)|\[\d+(?:[,–\-]\s*\d+)*\]")
NEG_RE = re.compile(r"\b(not|no|never|none|neither|nor|without|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|hardly|rarely|unlikely)\b", re.I)
MODAL_RE = re.compile(r"\b(may|might|could|should|must|will|always|never|all|some|most|few|often|rarely|likely|unlikely|possibly|probably|significantly)\b", re.I)


def _proper_nouns(text: str) -> set[str]:
    out = set()
    for s in re.split(r"(?<=[.!?])\s+", text):
        words = WORD_RE.findall(s)
        for w in words[1:]:
            if w[:1].isupper() and not w.isupper() and len(w) > 1:
                out.add(w)
        for w in words:
            if w.isupper() and len(w) >= 2 and w.isalpha():
                out.add(w)
    return out


def assess(original: str, proposed: str, context: str = "") -> tuple[dict, dict]:
    """Return (meaning_assessment, verification)."""
    warnings: list[str] = []
    verify: list[str] = []

    o_nums, p_nums = NUM_RE.findall(original), NUM_RE.findall(proposed)
    missing_nums = [n for n in o_nums if n not in p_nums]
    new_nums = [n for n in p_nums if n not in o_nums and n not in context]
    if missing_nums:
        warnings.append(f"Numbers removed or changed: {', '.join(missing_nums[:5])}")
    if new_nums:
        verify.append(f"New numbers introduced: {', '.join(new_nums[:5])}")

    o_c, p_c = CITE_RE.findall(original), CITE_RE.findall(proposed)
    if [c for c in o_c if c not in p_c]:
        warnings.append("A citation was removed or altered.")
    new_c = [c for c in p_c if c not in o_c and c not in context]
    if new_c:
        verify.append(f"New citation(s) added: {', '.join(new_c[:3])} — confirm the source exists and supports the claim.")

    o_neg, p_neg = len(NEG_RE.findall(original)), len(NEG_RE.findall(proposed))
    if o_neg != p_neg:
        warnings.append("The number of negations changed (e.g. “not”, “never”), which can reverse meaning.")

    o_mod = sorted(m.lower() for m in MODAL_RE.findall(original))
    p_mod = sorted(m.lower() for m in MODAL_RE.findall(proposed))
    if o_mod != p_mod:
        removed = sorted(set(o_mod) - set(p_mod))
        added = sorted(set(p_mod) - set(o_mod))
        if removed or added:
            warnings.append("Certainty or scope words changed" + (f" (removed: {', '.join(removed)}" if removed else " (") +
                            (f"; added: {', '.join(added)})" if added else ")"))

    o_names, p_names = _proper_nouns(original), _proper_nouns(proposed)
    dropped = sorted(o_names - p_names - _proper_nouns(proposed.lower().title()))
    dropped = [d for d in dropped if d.lower() not in proposed.lower()]
    if dropped:
        warnings.append(f"Names or terms no longer present: {', '.join(dropped[:5])}")
    introduced = sorted(n for n in p_names - o_names if n.lower() not in original.lower() and n.lower() not in context.lower())
    if introduced:
        verify.append(f"New names or terms introduced: {', '.join(introduced[:5])}")

    if original.count("￼") != proposed.count("￼"):
        warnings.append("A footnote marker, image or field was moved or removed; this change cannot be exported safely.")

    o_words, p_words = len(WORD_RE.findall(original)), len(WORD_RE.findall(proposed))
    ratio = p_words / o_words if o_words else 1
    if ratio < 0.5 and o_words > 12:
        warnings.append(f"The text is {round((1 - ratio) * 100)}% shorter; check that no information was lost.")
    if ratio > 1.8 and o_words > 8:
        verify.append(f"The text is {round((ratio - 1) * 100)}% longer; check that nothing unsupported was added.")

    level = "likely_preserved" if not warnings else ("review" if len(warnings) == 1 else "possible_change")
    labels = {"likely_preserved": "Meaning likely preserved", "review": "Review: meaning may have shifted",
              "possible_change": "Warning: meaning may have changed"}
    meaning = {"level": level, "label": labels[level], "warnings": warnings, "method": "automated checks",
               "length_ratio": round(ratio, 2)}
    verification = {"required": bool(verify), "items": verify}
    return meaning, verification
