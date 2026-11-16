"""Stages C–E — targeted refinement of the structural draft.

Each stage is split into a **deterministic check** (what is wrong with this block, measured against
the original and the requested style) and, only for blocks with findings, **one batched model call** that
applies just those edits. A model edit is accepted only if it passes ``guard``: protected material is
intact, the block stays close to its previous version, the stage's own findings go down, and no new
meaning problem (number, date, citation, negation, qualifier) is introduced. Otherwise the previous
version is kept and the rejection is recorded.

Stages:
- C ``vocabulary``: generic vocabulary the rewrite introduced or kept, needless "fancier" synonym swaps,
  technical terms that were replaced, words repeated within a block.
- D ``style``: conformance to the selected mode (and writing profile), measured, plus register
  consistency across the document.
- E ``grammar``: rule-based corrections applied directly, then overlong sentences and unbalanced
  punctuation the rewrite introduced.
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter
from dataclasses import dataclass

from app.services.analysis.ai_writing_analysis_service import STOCK_PHRASES, STOCK_TRANSITIONS
from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer.analysis import FILLER_PHRASES, FORMAL_WORDS, REDUNDANT_PAIRS, STOPWORDS, formality_score

# Vocabulary that small instruction-tuned models overuse; flagged when a rewrite introduces it, or keeps
# it at Medium/Extensive intensity. Never flagged when it is one of the document's key terms.
GENERIC_VOCAB = [
    "delve", "delves", "delving", "tapestry", "seamless", "seamlessly", "pivotal", "crucial", "vital", "robust", "leverage", "leverages",
    "leveraging", "utilize", "utilizes", "utilise", "utilises", "showcase", "showcases", "underscore", "underscores", "foster",
    "fosters", "navigate", "navigating", "realm", "landscape", "embark", "intricate", "multifaceted", "testament", "elevate",
    "streamline", "bolster", "paramount", "meticulous", "meticulously", "holistic", "commendable", "noteworthy", "myriad",
    "plethora", "endeavor", "endeavour", "invaluable", "nuanced", "groundbreaking", "transformative", "unparalleled", "vibrant",
    "furthermore", "moreover", "additionally", "notably", "ultimately", "essentially",
]
STOCK_OPENERS = [
    "i hope this email finds you well", "i hope this message finds you well", "i hope you are doing well", "i hope you're doing well",
    "i am writing to inform you", "i'm writing to inform you", "please be advised", "please do not hesitate", "do not hesitate to",
    "don't hesitate to", "i wanted to reach out", "it goes without saying", "needless to say", "in conclusion", "to sum up",
    "in today's", "in the modern era", "since the dawn of",
]
POLITE = re.compile(r"\b(please|kindly|would you|could you|would like|we'd like|i'd like|thank|thanks|appreciate|grateful|sorry|apologi[sz]e)\b", re.I)
CONTRACTION = re.compile(r"\b\w+['’](?:t|s|re|ve|ll|d|m)\b", re.I)
EXPANDABLE = re.compile(r"\b(it is|do not|does not|did not|is not|are not|was not|cannot|can not|will not|you are|we are|they are|that is|"
                        r"there is|i am|i have|we have|you will|we will|it will|should not|would not|could not|have not|has not)\b", re.I)
YOU = re.compile(r"\b(you|your|yours)\b", re.I)


# Phrases from the filler list that are usually literal ("this kind of knowledge", "in terms of cost").
_NOT_FILLER = {"kind of", "sort of", "in terms of", "a lot of", "the fact that"}


@dataclass
class Finding:
    kind: str
    instruction: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "instruction": self.instruction}


def _count(text: str, phrase: str) -> int:
    return len(re.findall(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", text, re.I))


def _sents(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def _wc(text: str) -> int:
    return len(WORD_RE.findall(text))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=a.split(), b=b.split(), autojunk=False).ratio()


def _norm_apos(t: str) -> str:
    return t.replace("’", "'")


# ------------------------------------------------------------------ C: vocabulary
_OPENERS = re.compile(r"(?:(?<=^)|(?<=[.!?]\s))(?:Furthermore|Moreover|Additionally|In addition|In conclusion|In essence|Overall|To sum up|"
                      r"In summary|Ultimately|Notably|Importantly),\s+([a-z])?")


def strip_stock_openers(text: str) -> str:
    """Remove stock transitions at the start of sentences ("Moreover, the data…" → "The data…"). Deterministic and
    meaning-neutral; used when a repair pass restores a sentence together with its stock opener."""
    return _OPENERS.sub(lambda m: (m.group(1) or "").upper(), text)


def stock_load(original: str, text: str) -> int:
    """How many stock phrases and generic words a text contains (used to stop a later pass re-introducing them)."""
    t = _norm_apos(text)
    return sum(_count(t, p) for p in set(STOCK_PHRASES + STOCK_OPENERS + REDUNDANT_PAIRS)) + \
        sum(_count(t, w) for w in GENERIC_VOCAB if w not in ("furthermore", "moreover", "additionally"))


def vocabulary_findings(original: str, draft: str, *, intensity: str, mode: str, key_terms: list[str]) -> list[Finding]:
    o, d = _norm_apos(original), _norm_apos(draft)
    keys = {k.lower() for k in key_terms}
    out: list[Finding] = []
    strict = intensity != "light"

    for w in GENERIC_VOCAB:
        if w in keys or w in ("furthermore", "moreover", "additionally"):
            continue
        n_d, n_o = _count(d, w), _count(o, w)
        if n_d > n_o:
            out.append(Finding("introduced_vocab", f"The rewrite added “{w}”, which is vague here. Use a plain, specific word instead."))
        elif n_d and strict and mode != "academic":
            out.append(Finding("generic_vocab", f"Replace the generic word “{w}” with plainer, more specific wording."))
    flagged: list[str] = []
    phrases = STOCK_PHRASES + STOCK_OPENERS + [f for f in FILLER_PHRASES if " " in f and f not in _NOT_FILLER] + REDUNDANT_PAIRS
    for p in sorted(set(phrases), key=len, reverse=True):
        if p in keys or any(p in q for q in flagged):
            continue  # a shorter phrase inside one already flagged ("it is important to note" ⊂ "… note that")
        n_d = _count(d, p)
        if n_d and (strict or n_d > _count(o, p)):
            flagged.append(p)
            out.append(Finding("stock_phrase", f"Remove or rephrase the stock phrase “{p}”; say it plainly or drop it if it adds nothing."))
    opens = [s for s in _sents(d) if any(s.lower().lstrip("“\"'(").startswith(t + ",") for t in STOCK_TRANSITIONS)]
    if len(opens) >= 2 or (opens and strict and any(t in opens[0].lower()[:15] for t in ("furthermore", "moreover", "additionally"))):
        first = WORD_RE.findall(opens[0])[0]
        out.append(Finding("stock_transition", f"Cut the stock transition “{first},” at the start of a sentence; let the logic connect the ideas."))
    for m in FORMAL_WORDS.finditer(d):
        w = m.group(0).lower()
        if w in keys or (mode == "academic" and w in ("subsequently", "thereby")):
            continue
        if _count(d, w) > _count(o, w) or strict:
            out.append(Finding("formal_word", f"Use a plainer word than “{m.group(0)}”."))
    # Key terms must survive exactly (a technical term replaced by a synonym changes precision).
    for t in key_terms:
        if 3 <= len(t) <= 60 and t.lower() in o.lower() and t.lower() not in d.lower():
            out.append(Finding("term_changed", f"Restore the exact term “{t}”; it was replaced or dropped."))
    # Needless swaps for a longer or fancier synonym (one word replaced by one word).
    ow, dw = WORD_RE.findall(o), WORD_RE.findall(d)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=[w.lower() for w in ow], b=[w.lower() for w in dw], autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
            a, b = ow[i1].lower(), dw[j1].lower()
            if a in STOPWORDS or not a.isalpha() or not b.isalpha() or a[:4] == b[:4]:
                continue
            if b in GENERIC_VOCAB or FORMAL_WORDS.fullmatch(b) or (len(b) >= len(a) + 4 and len(a) <= 6):
                out.append(Finding("fancier_synonym", f"Keep the original, simpler word “{ow[i1]}” instead of “{dw[j1]}”."))
    # Repetition the rewrite made worse.
    dc = Counter(w.lower() for w in dw if len(w) >= 5 and w.lower() not in STOPWORDS and w.isalpha())
    oc = Counter(w.lower() for w in ow if len(w) >= 5)
    for w, c in dc.most_common(3):
        if c >= 3 and c > oc.get(w, 0) and w not in keys and not any(w in k for k in keys) and _wc(d) < 160:
            out.append(Finding("repetition", f"“{w}” appears {c} times; restructure so it is not repeated so often (keep it where it is a technical term)."))
    return _dedupe(out)[:8]


# ------------------------------------------------------------------ D: style
MODE_TARGETS = {
    "conversational": {"max_avg": 20, "max_sentence": 32, "max_formality": 55},
    "natural": {"max_avg": 24, "max_sentence": 38, "max_formality": 72},
    "professional": {"max_avg": 24, "max_sentence": 36, "max_formality": 80},
    "simple": {"max_avg": 15, "max_sentence": 24, "max_formality": 60},
    "academic": {"max_avg": 30, "max_sentence": 45},
    "personal": {"max_avg": 24, "max_sentence": 38},
    "custom": {},
}


def block_style(text: str) -> dict:
    lengths = [_wc(s) for s in _sents(text) if _wc(s)]
    n = max(1, _wc(text))
    return {"words": _wc(text), "avg": statistics.mean(lengths) if lengths else 0, "max": max(lengths or [0]),
            "contractions": 100 * len(CONTRACTION.findall(text)) / n, "expandable": len(EXPANDABLE.findall(text)),
            "formality": formality_score(text), "you": len(YOU.findall(text)), "polite": len(POLITE.findall(text)),
            "exclaim": text.count("!")}


def style_findings(original: str, draft: str, *, mode: str, intensity: str, profile_stats: dict | None,
                   doc_formality: float | None = None) -> list[Finding]:
    s, o = block_style(draft), block_style(original)
    t = MODE_TARGETS.get(mode, {})
    out: list[Finding] = []
    if mode == "professional" and o["polite"] and not s["polite"]:
        out.append(Finding("courtesy", "The original makes a courteous request; keep it courteous (e.g. “please …”, “could your team …”) rather than "
                                       "a bare command — by rewording the existing sentence, not by adding a new one."))
    if s["words"] < 12:
        return out
    long_s = [x for x in _sents(draft) if _wc(x) > t.get("max_sentence", 999) and _wc(x) > max(o["max"], 0) * 0.9]
    if long_s:
        out.append(Finding("long_sentence", f"Split the {_wc(long_s[0])}-word sentence beginning “{' '.join(long_s[0].split()[:6])}…” into shorter sentences."))
    if t.get("max_avg") and s["avg"] > t["max_avg"] and s["avg"] > o["avg"] * 0.8 and intensity != "light":
        out.append(Finding("sentence_length", f"Sentences average {s['avg']:.0f} words; aim for about {t['max_avg'] - 4} or fewer for this style."))
    f = s["formality"]
    if f is not None and t.get("max_formality") and f > t["max_formality"] + 8 and intensity != "light":
        out.append(Finding("register", "The register is stiffer than this style calls for; use plainer, more direct wording."))
    if mode == "conversational" and s["words"] >= 30 and s["contractions"] == 0 and s["expandable"]:
        out.append(Finding("contractions", "Use natural contractions (e.g. “it is” → “it’s”, “do not” → “don’t”) where they read naturally."))
    if mode == "academic":
        if s["contractions"] > 0:
            out.append(Finding("contractions", "Expand contractions (e.g. “don’t” → “do not”); academic register avoids them."))
        if s["you"] > o["you"]:
            out.append(Finding("person", "Do not address the reader as “you”; keep an impersonal academic voice."))
        if s["exclaim"]:
            out.append(Finding("punctuation", "Remove exclamation marks; academic register does not use them."))
    if mode == "professional":
        if s["exclaim"] > o["exclaim"]:
            out.append(Finding("punctuation", "Remove the added exclamation marks."))
    if mode == "simple":
        long_words = [w for w in WORD_RE.findall(draft) if len(w) >= 13 and w.lower() not in original.lower()]
        if long_words:
            out.append(Finding("word_choice", f"Use simpler words than “{long_words[0]}”."))
    if profile_stats:
        pa = float(profile_stats.get("avg_sentence_length") or 0)
        if pa and abs(s["avg"] - pa) > max(6, 0.4 * pa) and s["words"] >= 30:
            out.append(Finding("profile_length", f"The author's sentences average about {pa:.0f} words; adjust sentence length towards that."))
        pc = float(profile_stats.get("contractions_per_100_words") or 0)
        if pc >= 1.5 and s["contractions"] == 0 and s["expandable"] and mode != "academic":
            out.append(Finding("profile_contractions", "The author uses contractions freely; use them where natural."))
        elif pc < 0.2 and s["contractions"] >= 1.5:
            out.append(Finding("profile_contractions", "The author rarely uses contractions; write the words out."))
    # Intensity conformance: the requested amount of rewriting should actually happen.
    sim = similarity(original, draft)
    if intensity == "extensive" and sim > 0.8 and s["words"] >= 25:
        out.append(Finding("intensity", "This is an extensive rewrite but the passage is almost unchanged: rework sentence construction and order "
                                        "for clarity and flow (same facts, qualifiers and points)."))
    elif intensity == "medium" and sim > 0.93 and s["words"] >= 25 and long_s == [] and o["avg"] > 18:
        out.append(Finding("intensity", "Improve the sentence construction where it helps readability; the passage is nearly unchanged."))
    elif intensity == "light" and sim < 0.5:
        out.append(Finding("intensity", "This is a light edit but most of the passage was rewritten: restore the author's original wording "
                                        "and sentence structure, keeping only fixes to awkward phrasing, grammar and filler."))
    if doc_formality is not None and f is not None and abs(f - doc_formality) > 25 and s["words"] >= 40 and mode != "custom":
        out.append(Finding("consistency", "This passage is noticeably " + ("more formal" if f > doc_formality else "more casual") +
                           " than the rest of the revised document; match the document's register."))
    return _dedupe(out)[:6]


# ------------------------------------------------------------------ E: grammar & readability
_AUTO_CATEGORIES = {"grammar", "punctuation", "spelling"}


_STRAY_PERIOD = re.compile(r"([.!?][\"”’])\.(?=\s|$)")


def auto_corrections(original: str, draft: str) -> tuple[str, list[str]]:
    """Apply high-confidence rule corrections to errors the rewrite introduced (not ones already in the original)."""
    from app.services.analysis.writing_analysis_service import analyze_paragraph

    pre: list[str] = []
    if _STRAY_PERIOD.search(draft) and not _STRAY_PERIOD.search(original):
        draft = _STRAY_PERIOD.sub(r"\1", draft)
        pre.append("Removed a full stop doubled after a closing quotation mark.")

    para = {"id": "h", "text": draft, "sentences": [{"start": s, "end": e} for s, e in split_sentences(draft)]}
    try:
        findings = analyze_paragraph(para, spellcheck=False, doc_vocab=Counter())
    except Exception:  # noqa: BLE001 - a rule failure must never break the pipeline
        return draft, pre
    fixes = []
    for f in sorted(findings, key=lambda x: -(x["start"] or 0)):
        if f["category"] not in _AUTO_CATEGORIES or f["correction"] is None or (f["confidence"] or 0) < 0.75:
            continue
        passage = f["passage"] or ""
        if passage and passage in original:
            continue  # present in the author's original: not ours to change silently
        s, e = f["start"], f["end"]
        draft = draft[:s] + f["correction"] + draft[e:]
        fixes.append(f["explanation"])
    return draft, pre + fixes[::-1]


def grammar_findings(original: str, draft: str) -> list[Finding]:
    out: list[Finding] = []
    o_max = max([_wc(s) for s in _sents(original)] or [0])
    for s in _sents(draft):
        n = _wc(s)
        if n > 38 and n > o_max:
            out.append(Finding("long_sentence", f"Split the {n}-word sentence beginning “{' '.join(s.split()[:6])}…”; it is hard to follow."))
    for a, b, name in (("(", ")", "parenthesis"), ("“", "”", "quotation mark"), ("[", "]", "bracket")):
        if draft.count(a) != draft.count(b) and original.count(a) == original.count(b):
            out.append(Finding("unbalanced", f"Fix the unmatched {name}."))
    for a, b in _near_duplicates(draft):
        if not _near_duplicates(original):
            out.append(Finding("duplicate_sentence", f"Remove the sentence “{' '.join(b.split()[:8])}…”; it repeats “{' '.join(a.split()[:8])}…”."))
            break
    if re.search(r"\b(\w+)\s+\1\b", draft, re.I) and not re.search(r"\b(\w+)\s+\1\b", original, re.I):
        m = re.search(r"\b(\w+)\s+\1\b", draft, re.I)
        if m.group(1).lower() not in ("had", "that", "is", "very", "so"):
            out.append(Finding("repeated_word", f"Remove the repeated word “{m.group(1)}”."))
    if re.search(r"[^.!?…:;\"”')\]]\s*$", draft.strip()) and re.search(r"[.!?…:;\"”')\]]\s*$", original.strip()):
        out.append(Finding("ending", "The passage ends mid-sentence; finish the sentence."))
    return _dedupe(out)[:5]


_CLAUSES = re.compile(r",\s+(?:and|but|so|while)\s+|;\s*|\s[—–]\s|—")


def _near_duplicates(text: str) -> list[tuple[str, str]]:
    """Pairs (earlier sentence, later sentence) where the later one, or one of its clauses, mostly repeats the
    earlier one: ≥ 55% of a sentence's content words (≥ 60% of a clause's) occur, by stem, in the other."""
    from app.services.humanizer.semantics import _content, overlap

    sents = [(s, _content(s)) for s in _sents(text)]
    out = []
    for i in range(len(sents)):
        for j in range(i + 1, len(sents)):
            (sa, a), (sb, b) = sents[i], sents[j]
            short, long_ = (a, b) if len(a) <= len(b) else (b, a)
            if len(short) >= 4 and overlap(short, long_) >= 0.55:
                out.append((sa, sb))
                continue
            for clause in _CLAUSES.split(sb):
                c = _content(clause)
                if len(c) >= 4 and c != b and overlap(c, a) >= 0.6:
                    out.append((sa, clause.strip()))
                    break
    return out


def _dedupe(items: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in items:
        k = f.instruction.lower()
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


# ------------------------------------------------------------------ acceptance guard
STAGE_LIMITS = {
    "vocabulary": {"min_sim": 0.55, "ratio": (0.7, 1.3)},
    "style": {"min_sim": 0.35, "ratio": (0.6, 1.45)},
    "grammar": {"min_sim": 0.55, "ratio": (0.75, 1.35)},
    "repair": {"min_sim": 0.3, "ratio": (0.5, 2.0)},
}


def guard(stage: str, before: str, candidate: str, *, n_findings_before: int, n_findings_after: int,
          high_flags_before: int, high_flags_after: int, introduced_vocab_after: int, may_split: bool = False) -> str | None:
    """Return None if the candidate is acceptable, otherwise the reason it was rejected.

    ``may_split``: the requested edits include splitting a sentence, so the sentence count may grow."""
    lim = STAGE_LIMITS[stage]
    if not candidate.strip():
        return "the model returned an empty block"
    if stage in ("style", "grammar") and not may_split and len(_sents(candidate)) > len(_sents(before)):
        return "it added a sentence instead of revising the existing ones"
    sim = similarity(before, candidate)
    if sim < lim["min_sim"]:
        return f"it rewrote far more than the requested edits (similarity {sim:.2f})"
    if stage == "vocabulary" and len(_sents(candidate)) > len(_sents(before)):
        return "it added a sentence instead of changing words"
    wb, wa = max(1, _wc(before)), _wc(candidate)
    lo, hi = lim["ratio"]
    if not lo <= wa / wb <= hi:
        return f"it changed the length too much ({wb} → {wa} words)"
    if high_flags_after > high_flags_before:
        return "it introduced a possible change of meaning (number, date, citation, negation or qualifier)"
    if stage != "repair" and n_findings_after >= n_findings_before > 0:
        return "it did not resolve the problems it was asked to fix"
    if introduced_vocab_after:
        return "it introduced generic vocabulary"
    return None
