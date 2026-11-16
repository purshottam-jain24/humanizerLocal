"""Readability metrics (Flesch reading ease, Flesch–Kincaid grade) and findings."""

from __future__ import annotations

import re
import statistics

from app.services.analysis.common import analyzable, make_finding
from app.services.extraction.sentences import WORD_RE

MODULE = "readability"


def syllables(word: str) -> int:
    w = word.lower().strip("'’")
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    groups = re.findall(r"[aeiouy]{1,2}", w)
    return max(1, len(groups))


def metrics_for(text: str, sentence_count: int) -> dict:
    words = [w for w in WORD_RE.findall(text) if any(c.isalpha() for c in w)]
    n_words = len(words)
    n_sent = max(1, sentence_count)
    if n_words == 0:
        return {"words": 0}
    syl = [syllables(w) for w in words]
    n_syl = sum(syl)
    complex_words = sum(1 for s in syl if s >= 3)
    fre = 206.835 - 1.015 * (n_words / n_sent) - 84.6 * (n_syl / n_words)
    fk = 0.39 * (n_words / n_sent) + 11.8 * (n_syl / n_words) - 15.59
    return {
        "words": n_words,
        "sentences": n_sent,
        "avg_sentence_length": round(n_words / n_sent, 1),
        "avg_syllables_per_word": round(n_syl / n_words, 2),
        "complex_word_ratio": round(complex_words / n_words, 3),
        "flesch_reading_ease": round(fre, 1),
        "flesch_kincaid_grade": round(fk, 1),
    }


def band(fre: float) -> str:
    if fre >= 70:
        return "Easy"
    if fre >= 50:
        return "Standard"
    if fre >= 30:
        return "Difficult"
    return "Very difficult"


PASSIVE = re.compile(r"\b(am|is|are|was|were|be|been|being)\s+(\w+ly\s+)?(\w+(?:ed|en)|made|done|given|shown|found|known|seen|taken|built|held|written)\b", re.I)


def analyze(paragraphs: list[dict]) -> tuple[dict, list[dict]]:
    findings: list[dict] = []
    targets = [p for p in paragraphs if analyzable(p) and p["part"] == "body" and not p["table"] and p["kind"] != "caption"]
    per_par = []
    para_lengths = []
    sent_lengths = []
    passive = 0
    for p in targets:
        m = metrics_for(p["text"], len(p["sentences"]))
        if not m.get("words"):
            continue
        per_par.append({"paragraph_id": p["id"], **m})
        para_lengths.append(m["words"])
        for s in p["sentences"]:
            sent_lengths.append(len(WORD_RE.findall(p["text"][s["start"]:s["end"]])))
        passive += len(PASSIVE.findall(p["text"]))
        if m["words"] >= 220:
            findings.append(make_finding(MODULE, "paragraph_length", p, start=0, end=len(p["text"]), passage=p["text"][:160],
                                         priority="low",
                                         explanation=f"This paragraph has {m['words']} words. Long paragraphs are harder to follow; consider splitting it at a change of idea."))
        if m["words"] >= 40 and m["flesch_reading_ease"] < 20:
            findings.append(make_finding(MODULE, "difficulty", p, start=0, end=len(p["text"]), passage=p["text"][:160],
                                         priority="low",
                                         explanation=(f"Very dense paragraph (Flesch reading ease {m['flesch_reading_ease']}, "
                                                      f"average sentence {m['avg_sentence_length']} words). Shorter sentences or "
                                                      "plainer terms would help, unless the audience is specialist."),
                                         data=m))
    all_text = " ".join(p["text"] for p in targets)
    overall = metrics_for(all_text, sum(len(p["sentences"]) for p in targets)) if targets else {"words": 0}
    if overall.get("words"):
        overall["band"] = band(overall["flesch_reading_ease"])
        overall["avg_paragraph_length"] = round(statistics.mean(para_lengths), 1) if para_lengths else 0
        overall["longest_sentence"] = max(sent_lengths) if sent_lengths else 0
        overall["passive_constructions"] = passive
        overall["sentence_length_histogram"] = _histogram(sent_lengths, [0, 10, 20, 30, 40, 60, 10_000])
    return {"overall": overall, "paragraphs": per_par}, findings


def _histogram(values: list[int], edges: list[int]) -> list[dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        label = f"{lo}–{hi - 1}" if hi < 10_000 else f"{lo}+"
        out.append({"label": label, "count": sum(1 for v in values if lo <= v < hi)})
    return out
