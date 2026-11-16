"""Experimental AI-writing *pattern* diagnostics.

This is NOT an authorship detector and produces no probability that a
passage was machine-written. It reports observable, explainable stylistic
patterns (uniform sentence lengths, stock transitions, repeated structures,
generic statements, low lexical variety) that are worth a human look
regardless of who wrote the text. Formal academic prose, non-native English
and short texts can all show these patterns naturally.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict

from app.services.analysis.common import analyzable, make_finding
from app.services.extraction.sentences import WORD_RE

MODULE = "ai_patterns"
MIN_WORDS_DOC = 150
MIN_WORDS_PARAGRAPH = 60

DISCLAIMER = [
    "This is an experimental diagnostic of writing patterns, not an authorship test.",
    "Automated AI detectors produce false positives; no tool can determine authorship with certainty.",
    "Formal academic writing is naturally structured and can look predictable.",
    "Writing by non-native English speakers is disproportionately misclassified by AI detectors.",
    "Short passages do not contain enough evidence for meaningful analysis.",
    "Human and AI writing patterns overlap substantially.",
]

STOCK_TRANSITIONS = [
    "furthermore", "moreover", "additionally", "in addition", "in conclusion", "overall", "ultimately",
    "consequently", "notably", "importantly", "thus", "hence", "in summary", "to summarize", "on the other hand",
]

STOCK_PHRASES = [
    "it is important to note", "it is worth noting", "plays a crucial role", "plays a vital role", "plays a pivotal role",
    "in today's fast-paced world", "in today's world", "in the modern era", "in the realm of", "a testament to",
    "delve into", "delves into", "navigate the complexities", "the intricate", "a rich tapestry", "tapestry of",
    "underscores the importance", "highlights the importance", "a multifaceted", "ever-evolving", "ever-changing landscape",
    "serves as a", "paving the way", "unlock the potential", "harness the power", "at the end of the day",
    "a wide range of", "a myriad of", "in essence", "it can be argued that", "shed light on", "sheds light on",
    "game-changer", "seamlessly", "robust framework", "foster a sense of", "landscape of",
]

GENERIC_PATTERNS = [
    r"\b(many|some|several|various) (people|experts|researchers|studies|scholars) (believe|argue|suggest|say|think)\b",
    r"\bstudies (have )?show(n)?\b",
    r"\bresearch (has )?(shows?|suggests?|indicates?)\b",
    r"\bit is (widely|generally|commonly) (known|accepted|believed)\b",
    r"\b(has|have) (become|been) increasingly (important|popular|relevant)\b",
    r"\bin (many|various|numerous) ways\b",
    r"\bplays? an? (important|significant|key|major) role\b",
]


def _sentences(p: dict) -> list[str]:
    return [p["text"][s["start"]:s["end"]] for s in p["sentences"]]


def _mattr(tokens: list[str], window: int = 50) -> float | None:
    if len(tokens) < window:
        return len(set(tokens)) / len(tokens) if tokens else None
    vals = [len(set(tokens[i:i + window])) / window for i in range(0, len(tokens) - window + 1, 5)]
    return statistics.mean(vals)


def level_for(count: int) -> str:
    if count == 0:
        return "No notable pattern detected"
    if count <= 2:
        return "Some patterns worth reviewing"
    return "Multiple patterns worth reviewing"


def analyze(paragraphs: list[dict]) -> tuple[dict, list[dict]]:
    targets = [p for p in paragraphs if analyzable(p) and p["part"] == "body" and not p["table"] and p["kind"] != "caption"]
    total_words = sum(p["word_count"] for p in targets)
    findings: list[dict] = []
    signals: list[dict] = []

    if total_words < MIN_WORDS_DOC:
        return {
            "level": "Insufficient text for meaningful analysis",
            "words_analyzed": total_words,
            "signals": [],
            "paragraphs": [],
            "disclaimer": DISCLAIMER,
            "method": "statistical",
        }, []

    all_sents = [s for p in targets for s in _sentences(p)]
    lengths = [len(WORD_RE.findall(s)) for s in all_sents if WORD_RE.findall(s)]
    tokens = [w.lower() for p in targets for w in WORD_RE.findall(p["text"])]

    # 1. Sentence length variation (burstiness).
    if len(lengths) >= 8:
        mean = statistics.mean(lengths)
        sd = statistics.pstdev(lengths)
        cv = sd / mean if mean else 0
        sig = {"id": "sentence_length_variation", "label": "Sentence length variation",
               "value": round(cv, 2), "detail": f"mean {mean:.1f} words, standard deviation {sd:.1f}",
               "observed": cv < 0.35,
               "explanation": ("Sentence lengths are unusually uniform. Varying sentence length — mixing short, direct "
                               "sentences with longer explanatory ones — usually reads more naturally.") if cv < 0.35 else
               "Sentence lengths vary naturally."}
        signals.append(sig)

    # 2. Lexical diversity.
    mattr = _mattr(tokens)
    if mattr is not None and len(tokens) >= 100:
        low = mattr < 0.62
        signals.append({"id": "lexical_variety", "label": "Vocabulary variety (MATTR)", "value": round(mattr, 3),
                        "detail": "moving-average type-token ratio over 50-word windows", "observed": low,
                        "explanation": "Vocabulary is relatively repetitive across the text." if low else
                        "Vocabulary variety is within a typical range."})

    # 3. Stock transitions at sentence starts.
    trans_counts = Counter()
    for s in all_sents:
        head = s.strip().lower()
        for t in STOCK_TRANSITIONS:
            if head.startswith(t + ",") or head.startswith(t + " "):
                trans_counts[t] += 1
    trans_total = sum(trans_counts.values())
    trans_ratio = trans_total / max(1, len(all_sents))
    observed = trans_ratio > 0.15 and trans_total >= 3
    signals.append({"id": "transitions", "label": "Formulaic sentence transitions", "value": round(trans_ratio, 2),
                    "detail": ", ".join(f"{k} ×{v}" for k, v in trans_counts.most_common(5)) or "none",
                    "observed": observed,
                    "explanation": (f"{trans_total} of {len(all_sents)} sentences open with stock transitions. Linking ideas "
                                    "through content rather than connective words often reads more naturally.") if observed else
                    "Transition use is moderate."})

    # 4. Stock phrases.
    phrase_hits: dict[str, list[tuple[dict, re.Match]]] = defaultdict(list)
    for p in targets:
        low = p["text"].lower()
        for ph in STOCK_PHRASES:
            for m in re.finditer(re.escape(ph), low):
                phrase_hits[ph].append((p, m))
    n_phrase = sum(len(v) for v in phrase_hits.values())
    per_1k = n_phrase / max(1, total_words) * 1000
    signals.append({"id": "stock_phrases", "label": "Generic stock phrases", "value": round(per_1k, 1),
                    "detail": f"{n_phrase} occurrence(s), {per_1k:.1f} per 1,000 words", "observed": per_1k >= 3 and n_phrase >= 2,
                    "explanation": "Several generic phrases appear that carry little specific meaning." if per_1k >= 3 and n_phrase >= 2
                    else "Few generic stock phrases."})
    for ph, hits in phrase_hits.items():
        for p, m in hits[:3]:
            findings.append(make_finding(MODULE, "stock_phrase", p, start=m.start(), end=m.end(), priority="low", source="statistical",
                                         explanation=f"“{p['text'][m.start():m.end()]}” is a generic phrase. Say specifically what you mean, ideally with evidence or an example."))

    # 5. Repeated sentence openings (document-wide).
    openers = Counter()
    for s in all_sents:
        ws = WORD_RE.findall(s.lower())
        if len(ws) >= 4:
            openers[" ".join(ws[:2])] += 1
    common = [(o, c) for o, c in openers.most_common(5) if c >= max(3, len(all_sents) * 0.08) and o not in ("this is",)]
    signals.append({"id": "repeated_openers", "label": "Repeated sentence structures",
                    "value": sum(c for _, c in common), "detail": ", ".join(f"“{o}…” ×{c}" for o, c in common) or "none",
                    "observed": bool(common),
                    "explanation": "Several sentences begin with the same words, which can make the text feel templated." if common
                    else "Sentence openings are varied."})

    # 6. Repeated n-grams across the document.
    tri = Counter(tuple(tokens[i:i + 4]) for i in range(len(tokens) - 3))
    rep = [(" ".join(k), c) for k, c in tri.most_common(20) if c >= 3 and sum(1 for x in k if len(x) > 3) >= 2]
    signals.append({"id": "repeated_ngrams", "label": "Repeated multi-word phrases", "value": len(rep),
                    "detail": "; ".join(f"“{k}” ×{c}" for k, c in rep[:5]) or "none", "observed": len(rep) >= 2,
                    "explanation": "The same multi-word phrases recur several times." if len(rep) >= 2 else "Little phrase-level repetition."})

    # 7. Generic, unsupported statements.
    generic_hits = []
    for p in targets:
        for pat in GENERIC_PATTERNS:
            for m in re.finditer(pat, p["text"], re.I):
                generic_hits.append((p, m))
    for p, m in generic_hits[:15]:
        findings.append(make_finding(MODULE, "generic_statement", p, start=m.start(), end=m.end(), priority="low", source="statistical",
                                     explanation="Generic claim without a specific source or example. Name the study, data or example you are relying on."))
    signals.append({"id": "generic_statements", "label": "Generic statements without specifics", "value": len(generic_hits),
                    "detail": f"{len(generic_hits)} occurrence(s)", "observed": len(generic_hits) >= 3,
                    "explanation": "Several broad claims lack concrete support." if len(generic_hits) >= 3 else "Few generic claims."})

    # 8. Uniformity across paragraphs (paragraph length similarity).
    plen = [p["word_count"] for p in targets if p["word_count"] >= 20]
    if len(plen) >= 5:
        cvp = statistics.pstdev(plen) / statistics.mean(plen)
        signals.append({"id": "paragraph_uniformity", "label": "Paragraph length uniformity", "value": round(cvp, 2),
                        "detail": f"{len(plen)} paragraphs", "observed": cvp < 0.15,
                        "explanation": "Paragraphs are almost identical in length, an unusually uniform structure." if cvp < 0.15
                        else "Paragraph lengths vary."})

    # Per-paragraph observations.
    par_obs = []
    for p in targets:
        if p["word_count"] < MIN_WORDS_PARAGRAPH:
            continue
        obs = []
        sl = [len(WORD_RE.findall(s)) for s in _sentences(p)]
        if len(sl) >= 4 and statistics.mean(sl) and statistics.pstdev(sl) / statistics.mean(sl) < 0.2:
            obs.append("Sentences in this paragraph are very similar in length.")
        heads = [s.strip().lower() for s in _sentences(p)]
        t = sum(1 for h in heads for tr in STOCK_TRANSITIONS if h.startswith(tr + ",") or h.startswith(tr + " "))
        if t >= 2 and t / max(1, len(heads)) >= 0.4:
            obs.append(f"{t} of {len(heads)} sentences start with a stock transition.")
        ph = sum(1 for x in STOCK_PHRASES if x in p["text"].lower())
        if ph >= 2:
            obs.append(f"{ph} generic stock phrases appear in this paragraph.")
        if obs:
            par_obs.append({"paragraph_id": p["id"], "observations": obs, "level": level_for(len(obs))})
            findings.append(make_finding(MODULE, "paragraph_pattern", p, start=0, end=len(p["text"]), passage=p["text"][:160],
                                         priority="low", source="statistical",
                                         explanation=" ".join(obs) + " Consider adding concrete detail and varying structure.",
                                         data={"observations": obs}))

    observed_count = sum(1 for s in signals if s["observed"])
    return {
        "level": level_for(observed_count),
        "words_analyzed": total_words,
        "signals": signals,
        "paragraphs": par_obs,
        "disclaimer": DISCLAIMER,
        "method": "statistical",
    }, findings
