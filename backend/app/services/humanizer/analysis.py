"""Stage B — deterministic writing-quality profile.

Measures observable properties of the text (sentence rhythm, repetition, transitions,
filler, formality, readability, redundancy, paragraph shape, tone drift). The profile is
used to steer rewriting (``guidance``) and is shown to the user. It is not an authorship
or AI-detection signal and is never compared with detector output.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

from app.services.analysis.ai_writing_analysis_service import GENERIC_PATTERNS, STOCK_PHRASES, STOCK_TRANSITIONS
from app.services.analysis.readability_service import PASSIVE, band, metrics_for
from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer.blocks import TOKEN_RE, Block

STOPWORDS = set("""a an the and or but if then than so of to in on at by for with from as is are was were be been being it its this
that these those there their they them he she his her we our us you your i me my not no do does did done have has had
can could will would should may might must shall which who whom whose what when where why how all any each more most
other some such only own same too very just also into over under about after before between through during without
within upon while because although though however one two first new used use using make made""".split())

FILLER_PHRASES = [
    "it is important to note that", "it should be noted that", "it is worth mentioning that", "needless to say",
    "in order to", "due to the fact that", "at this point in time", "for all intents and purposes", "the fact that",
    "in terms of", "basically", "essentially", "actually", "really", "very", "quite", "rather", "a lot of",
    "kind of", "sort of", "as a matter of fact", "in the event that", "with regard to", "with respect to",
    "it goes without saying", "first and foremost", "last but not least", "all things considered",
]
REDUNDANT_PAIRS = [
    "each and every", "end result", "future plans", "past history", "final outcome", "basic fundamentals",
    "completely eliminate", "absolutely essential", "advance planning", "close proximity", "free gift",
    "unexpected surprise", "join together", "repeat again", "revert back", "still remains", "true facts",
    "added bonus", "brief summary", "collaborate together", "combine together", "general consensus",
]
INTRO_CLICHES = [
    r"^in today'?s\b", r"^in the (modern|current|digital) (era|age|world)\b", r"^since the dawn of\b",
    r"^throughout history\b", r"^in recent years\b", r"^this (essay|article|paper|report) (will|aims to)\b",
]
CONCLUSION_CLICHES = [
    r"^in conclusion\b", r"^to (sum up|summarize|summarise|conclude)\b", r"^in summary\b", r"^overall,", r"^ultimately,",
    r"^all in all\b", r"^in essence\b",
]
CONTRACTION = re.compile(r"\b\w+(?:'|’)(?:t|s|re|ve|ll|d|m)\b", re.I)
FIRST_SECOND = re.compile(r"\b(i|me|my|we|our|us|you|your)\b", re.I)
NOMINAL = re.compile(r"\b\w{4,}(?:tion|sion|ment|ness|ity|ance|ence)s?\b", re.I)
FORMAL_WORDS = re.compile(r"\b(utili[sz]e|facilitate|commence|endeavou?r|subsequently|aforementioned|henceforth|thereby|wherein|"
                          r"notwithstanding|heretofore|pursuant|in lieu of|leverage|paradigm|holistic|synergy|optimi[sz]e)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def _content_words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(TOKEN_RE.sub(" ", text)) if w.lower() not in STOPWORDS and len(w) > 2 and w.isalpha()]


def _opening(sentence: str, n: int = 1) -> str:
    ws = WORD_RE.findall(sentence)
    return " ".join(w.lower() for w in ws[:n])


def formality_score(text: str) -> float | None:
    """0 = very casual, 100 = very formal. Heuristic, explained in the UI."""
    words = WORD_RE.findall(text)
    if len(words) < 15:
        return None
    n = len(words)
    sents = max(1, len(_sentences(text)))
    long_words = sum(1 for w in words if len(w) >= 9) / n
    contractions = len(CONTRACTION.findall(text)) / n
    personal = len(FIRST_SECOND.findall(text)) / n
    nominal = len(NOMINAL.findall(text)) / n
    formal = len(FORMAL_WORDS.findall(text)) / n
    passive = len(PASSIVE.findall(text)) / sents
    s = 45 + long_words * 160 + nominal * 220 + formal * 600 + passive * 18 - contractions * 900 - personal * 250
    return round(max(0.0, min(100.0, s)), 1)


def _find_all(phrases: list[str], text: str) -> Counter:
    low = text.lower()
    c = Counter()
    for p in phrases:
        k = len(re.findall(r"\b" + re.escape(p) + r"\b", low))
        if k:
            c[p] = k
    return c


def analyze(blocks: list[Block]) -> dict:
    """Return the writing-quality profile for the rewritable prose in ``blocks``."""
    prose = [b for b in blocks if b.rewritable and b.kind in ("paragraph", "list_item")]
    text = "\n\n".join(b.text for b in prose)
    sents_by_block = {b.id: _sentences(b.text) for b in prose}
    sents = [s for b in prose for s in sents_by_block[b.id]]
    lengths = [len(WORD_RE.findall(s)) for s in sents if WORD_RE.findall(s)]
    words = len(WORD_RE.findall(text))
    issues: list[dict] = []
    per_block: dict[int, list[str]] = {}

    def note(bid: int, msg: str) -> None:
        per_block.setdefault(bid, []).append(msg)

    def issue(iid: str, label: str, severity: str, detail: str, examples: list[str] | None = None, guidance: str | None = None) -> None:
        issues.append({"id": iid, "label": label, "severity": severity, "detail": detail, "examples": (examples or [])[:4],
                       "guidance": guidance})

    if not words:
        return {"words": 0, "sentences": 0, "issues": [], "metrics": {}, "guidance": [], "per_block": {}}

    # Sentence rhythm.
    mean_len = statistics.mean(lengths) if lengths else 0
    sd = statistics.pstdev(lengths) if len(lengths) > 1 else 0
    cv = sd / mean_len if mean_len else 0
    if len(lengths) >= 5 and cv < 0.3:
        issue("uniform_length", "Little variation in sentence length", "medium",
              f"Sentences average {mean_len:.0f} words with little variation (spread {sd:.1f}), which makes the rhythm monotonous.",
              guidance="Vary sentence length: mix short, direct sentences with longer ones where ideas connect.")
    long_sents = [s for s in sents if len(WORD_RE.findall(s)) > 38]
    if long_sents:
        issue("long_sentences", "Very long sentences", "medium" if len(long_sents) > 1 else "low",
              f"{len(long_sents)} sentence(s) exceed 38 words.", [s[:140] for s in long_sents],
              guidance="Split overlong sentences where a reader would lose the thread.")

    # Repetitive openings.
    openings = Counter(_opening(s) for s in sents if _opening(s))
    two = Counter(_opening(s, 2) for s in sents if len(WORD_RE.findall(s)) > 2)
    rep_open = [(w, c) for w, c in openings.most_common(5) if c >= 3 and c / max(1, len(sents)) >= 0.2 and w not in ("i",)]
    rep_open += [(w, c) for w, c in two.most_common(3) if c >= 3]
    if rep_open:
        issue("repetitive_openings", "Sentences start the same way", "medium",
              "Repeated sentence openings: " + ", ".join(f"“{w}” ×{c}" for w, c in rep_open[:4]) + ".",
              guidance="Vary how sentences begin; avoid starting several sentences with the same word or phrase.")
    for b in prose:
        ss = sents_by_block[b.id]
        if len(ss) >= 3:
            o = Counter(_opening(s) for s in ss)
            top = o.most_common(1)[0]
            if top[1] >= 3:
                note(b.id, f"{top[1]} sentences start with “{top[0]}”.")

    # Transitions.
    trans_counts = Counter()
    trans_examples = []
    for s in sents:
        low = s.lower().lstrip("“\"'(")
        for t in STOCK_TRANSITIONS:
            if low.startswith(t + ",") or low.startswith(t + " "):
                trans_counts[t] += 1
                trans_examples.append(s[:100])
                break
    trans_rate = sum(trans_counts.values()) / max(1, len(sents))
    if sum(trans_counts.values()) >= 3 and trans_rate >= 0.15:
        issue("stock_transitions", "Heavy use of stock transitions", "high" if trans_rate > 0.3 else "medium",
              f"{sum(trans_counts.values())} of {len(sents)} sentences open with a stock transition (" +
              ", ".join(f"{t} ×{c}" for t, c in trans_counts.most_common(4)) + ").", trans_examples,
              guidance="Cut stock transitions (furthermore, moreover, additionally…). Let the logic connect sentences; use a transition only where it adds meaning.")
    for b in prose:
        k = sum(1 for s in sents_by_block[b.id] if any(s.lower().startswith(t) for t in STOCK_TRANSITIONS))
        if k >= 2:
            note(b.id, f"{k} sentences open with stock transitions.")

    # Stock, generic and filler phrases.
    stock = _find_all(STOCK_PHRASES, text)
    if stock:
        issue("stock_phrases", "Generic stock phrases", "high" if sum(stock.values()) >= 3 else "medium",
              "Found: " + ", ".join(f"“{p}”" + (f" ×{c}" if c > 1 else "") for p, c in stock.most_common(6)) + ".",
              list(stock), guidance="Replace generic stock phrases with specific, plain wording (or drop them).")
    filler = _find_all(FILLER_PHRASES, text)
    filler_total = sum(filler.values())
    if filler_total >= 3 or (words < 150 and filler_total >= 2):
        issue("filler", "Filler words and phrases", "medium",
              "Found: " + ", ".join(f"“{p}”" + (f" ×{c}" if c > 1 else "") for p, c in filler.most_common(6)) + ".",
              list(filler), guidance="Remove filler that adds no meaning (“in order to” → “to”, “due to the fact that” → “because”).")
    generic = []
    for pat in GENERIC_PATTERNS:
        generic += [m.group(0) for m in re.finditer(pat, text, re.I)]
    if generic:
        issue("generic_statements", "Vague, generic statements", "low", "Vague attributions or claims: " +
              ", ".join(f"“{g}”" for g in generic[:4]) + ".", generic,
              guidance="Keep vague claims as they are factually, but do not add evidence; prefer concrete wording that the text supports.")
    redundant = _find_all(REDUNDANT_PAIRS, text)
    if redundant:
        issue("redundant_pairs", "Redundant word pairs", "low", "Found: " + ", ".join(f"“{p}”" for p in redundant) + ".", list(redundant),
              guidance="Collapse redundant pairs (“end result” → “result”).")

    # Vocabulary repetition.
    cw = _content_words(text)
    vc = Counter(cw)
    expected = max(3, len(cw) * 0.025)
    overused = [(w, c) for w, c in vc.most_common(12) if c >= expected and c >= 4]
    if overused and len(cw) >= 60:
        issue("vocabulary_repetition", "Repeated vocabulary", "low",
              "Frequently repeated words: " + ", ".join(f"{w} ×{c}" for w, c in overused[:6]) + ". Key technical terms should stay consistent; other words can vary.",
              guidance="Avoid needless repetition of non-technical words; keep technical terms consistent.")

    # Redundancy: repeated 4-grams (same idea stated twice).
    toks = [w.lower() for w in WORD_RE.findall(TOKEN_RE.sub(" ", text))]
    grams = Counter(tuple(toks[i:i + 4]) for i in range(len(toks) - 3))
    rep_grams = [" ".join(g) for g, c in grams.items() if c >= 2 and sum(1 for w in g if w not in STOPWORDS) >= 2]
    if rep_grams:
        issue("redundancy", "Repeated phrasing", "medium" if len(rep_grams) > 2 else "low",
              "Phrases repeated word-for-word: " + ", ".join(f"“{g}”" for g in rep_grams[:4]) + ".", rep_grams,
              guidance="Where a point is restated in the same words, say it once.")

    # Vocabulary complexity.
    rd = metrics_for(text, len(sents))
    formal_hits = FORMAL_WORDS.findall(text)
    if len(formal_hits) >= 2:
        issue("complex_vocabulary", "Unnecessarily complicated words", "low",
              "Words with plainer alternatives: " + ", ".join(sorted({h.lower() for h in formal_hits})[:6]) + ".",
              guidance="Prefer plain words (use, help, start) unless the term is technical.")

    # Formality and tone consistency.
    formality = formality_score(text)
    per_para_formality = {b.id: formality_score(b.text) for b in prose}
    vals = [v for v in per_para_formality.values() if v is not None]
    tone_drift = []
    if len(vals) >= 3:
        med = statistics.median(vals)
        tone_drift = [bid for bid, v in per_para_formality.items() if v is not None and abs(v - med) > 30]
        if tone_drift:
            issue("tone_inconsistency", "Tone shifts between paragraphs", "medium",
                  f"{len(tone_drift)} paragraph(s) are much more or less formal than the rest.",
                  guidance="Keep the tone consistent across paragraphs.")
            for bid in tone_drift:
                note(bid, "Tone differs noticeably from the rest of the text.")
    if formality is not None and formality >= 80:
        issue("excess_formality", "Very formal register", "low",
              f"Formality {formality:.0f}/100: heavy nominalisation, long words or passive voice.",
              guidance="Reduce stiffness: prefer active verbs over nominalisations where the audience allows.")
    passive = len(PASSIVE.findall(text))
    if len(sents) >= 4 and passive / len(sents) > 0.35:
        issue("passive_voice", "Frequent passive voice", "low", f"{passive} passive constructions in {len(sents)} sentences.",
              guidance="Use active voice where the actor matters and is known.")

    # Paragraph structure.
    para_words = [b.words for b in prose if b.kind == "paragraph"]
    long_paras = [b for b in prose if b.kind == "paragraph" and b.words > 220 and b.part_of is None]
    if long_paras:
        issue("long_paragraphs", "Very long paragraphs", "low", f"{len(long_paras)} paragraph(s) exceed 220 words.",
              guidance="Long paragraphs can stay long, but make each sentence move the point forward.")
    if prose:
        first = prose[0].text.strip().lower()
        if any(re.search(p, first) for p in INTRO_CLICHES):
            issue("cliche_intro", "Formulaic introduction", "medium", "The opening uses a stock introduction (“" + prose[0].text[:60] + "…”).",
                  guidance="Open with the actual point instead of a stock scene-setter.")
            note(prose[0].id, "Formulaic opening.")
        concl = [b for b in prose if any(re.search(p, b.text.strip().lower()) for p in CONCLUSION_CLICHES)]
        if concl:
            issue("cliche_conclusion", "Formulaic conclusion", "low", "A paragraph opens with a stock conclusion phrase (“" + concl[-1].text[:40] + "…”).",
                  guidance="End by stating the conclusion directly, without a stock signpost, and do not just repeat earlier sentences.")
            for b in concl:
                note(b.id, "Stock conclusion signpost.")

    metrics = {
        "words": words,
        "sentences": len(sents),
        "paragraphs": len(para_words),
        "avg_sentence_length": round(mean_len, 1),
        "sentence_length_sd": round(sd, 1),
        "sentence_length_variation": round(cv, 2),
        "shortest_sentence": min(lengths) if lengths else 0,
        "longest_sentence": max(lengths) if lengths else 0,
        "sentence_length_histogram": _hist(lengths),
        "avg_paragraph_length": round(statistics.mean(para_words), 1) if para_words else 0,
        "lexical_variety": _mattr(toks),
        "formality": formality,
        "transition_rate": round(trans_rate, 2),
        "passive_constructions": passive,
        "flesch_reading_ease": rd.get("flesch_reading_ease"),
        "flesch_kincaid_grade": rd.get("flesch_kincaid_grade"),
        "readability_band": band(rd["flesch_reading_ease"]) if rd.get("flesch_reading_ease") is not None else None,
    }
    sev = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: sev[x["severity"]])
    return {
        "words": words,
        "sentences": len(sents),
        "metrics": metrics,
        "issues": issues,
        "guidance": [i["guidance"] for i in issues if i.get("guidance")],
        "per_block": {str(k): v for k, v in per_block.items()},
        "method": "Deterministic text statistics (no AI). Describes writing characteristics only.",
    }


def _hist(values: list[int]) -> list[dict]:
    edges = [0, 8, 15, 22, 30, 40, 10_000]
    out = []
    for lo, hi in zip(edges, edges[1:]):
        out.append({"label": f"{lo}–{hi - 1}" if hi < 10_000 else f"{lo}+", "count": sum(1 for v in values if lo <= v < hi)})
    return out


def _mattr(tokens: list[str], window: int = 50) -> float | None:
    if len(tokens) < window:
        return round(len(set(tokens)) / len(tokens), 3) if tokens else None
    vals = [len(set(tokens[i:i + window])) / window for i in range(0, len(tokens) - window + 1, 5)]
    return round(statistics.mean(vals), 3)


def guidance_for(profile: dict, block_ids: list[int]) -> list[str]:
    """Guidance relevant to a section: document-level guidance plus notes on its own paragraphs."""
    out = list(profile.get("guidance", []))[:6]
    for bid in block_ids:
        for msg in profile.get("per_block", {}).get(str(bid), []):
            out.append(f"Block {bid}: {msg}")
    return out
