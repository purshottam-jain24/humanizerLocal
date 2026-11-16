"""Stage F — meaning-preservation validation (separate from rewriting).

Two layers:
1. Deterministic checks (always): numbers, dates, citations, quotations, links, negations,
   certainty words, names and key terms, length changes, missing content. Reuses the
   app's existing ``writing.assessment.assess``.
2. An optional AI comparison (in the pipeline) that reports semantic changes the
   deterministic checks cannot see.

Neither layer proves semantic equivalence; results are phrased as "no problems found by
these checks", never "meaning preserved".
"""

from __future__ import annotations

import difflib
import re

from app.services.analysis.ai_writing_analysis_service import STOCK_TRANSITIONS
from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer.blocks import CITATION_RE, QUOTE_RE, URL_RE
from app.services.writing.assessment import assess

MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RE = re.compile(rf"\b(?:\d{{1,2}}\s+{MONTHS}\.?(?:\s+\d{{4}})?|{MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|{MONTHS}\s+\d{{4}}|"
                     r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b", re.I)
WEEKDAY_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)

SEVERITY = {"high": 0, "medium": 1, "low": 2}

COMMON_CAPITALISED = set(STOCK_TRANSITIONS) | {
    "also", "however", "therefore", "first", "second", "third", "finally", "next", "then", "still", "yet", "instead", "meanwhile",
    "similarly", "likewise", "indeed", "besides", "nevertheless", "nonetheless", "accordingly", "specifically", "in", "the", "this",
    "these", "that", "it", "we", "they", "our", "a", "an", "as", "for", "but", "and", "so", "or", "if", "when", "while", "although",
}


def _excerpt(t: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n - 1] + "…"


NEG_IDIOMS = re.compile(r"\b(?:please\s+)?(?:do not|don't|dont)\s+hesitate\b|\bnot only\b|\bno doubt\b|\bnone the less\b|\bnot least\b", re.I)


def _strip_idioms(t: str) -> str:
    return NEG_IDIOMS.sub(" ", t)


def compare_block(block_id: int | None, original: str, revised: str, *, context: str = "", key_terms: list[str] | None = None) -> list[dict]:
    flags: list[dict] = []

    def flag(kind: str, severity: str, message: str) -> None:
        flags.append({"block_id": block_id, "type": kind, "severity": severity, "message": message, "source": "automated",
                      "original_excerpt": _excerpt(original), "revised_excerpt": _excerpt(revised)})

    if original.strip() == revised.strip():
        return flags
    if not revised.strip():
        flag("missing_information", "high", "This passage was removed entirely.")
        return flags

    for q in QUOTE_RE.findall(original):
        if q not in revised:
            flag("quotation", "high", f"A quotation was changed or removed: {_excerpt(q, 80)}")
    # Whole numeric tokens ("22:00", "1,840", "0.4", "9%"): a time or figure with its digits rearranged must not pass.
    lost = [n for n in dict.fromkeys(NUMERIC_TOKEN.findall(_strip_tokens(original))) if not _number_kept(n, revised)]
    if lost:
        flag("number_or_date", "high", f"Numbers removed or changed: {', '.join(lost[:5])}")
    for c in CITATION_RE.findall(original):
        if c not in revised:
            flag("citation", "high", f"A citation was changed or removed: {c}")
    for c in CITATION_RE.findall(revised):
        if c not in original and c not in context:
            flag("citation", "high", f"A citation appears that was not in the original: {c}. Never accept invented sources.")
    for u in URL_RE.findall(original):
        if u not in revised:
            flag("link", "medium", f"A link was changed or removed: {u}")
    o_dates = [d.lower() for d in DATE_RE.findall(original)] + [d.lower() for d in WEEKDAY_RE.findall(original)]
    r_low = revised.lower()
    for d in o_dates:
        if d not in r_low:
            flag("number_or_date", "high", f"A date was changed or removed: {d}")

    # Idioms whose negation carries no logical meaning ("do not hesitate to…") would otherwise look like a reversed claim.
    meaning, verification = assess(_strip_idioms(original), _strip_idioms(revised), context)
    for w in meaning["warnings"]:
        low = w.lower()
        if low.startswith("numbers removed"):
            continue  # replaced by the whole-token check above
        elif low.startswith("a citation"):
            continue  # reported above with the exact citation
        elif "negations" in low or low.startswith("certainty"):
            continue  # replaced by the sentence-aware negation and qualifier checks below
        elif low.startswith("names or terms"):
            # Sentence-internal capitalised connectives ("…, Furthermore") are not names.
            names = [n.strip() for n in w.split(":", 1)[-1].split(",")]
            real = [n for n in names if n and n.lower() not in COMMON_CAPITALISED]
            if real:
                flag("terminology", "medium", f"Names or terms no longer present: {', '.join(real)}")
        elif "shorter" in low:
            flag("missing_information", "medium", w)
        elif "footnote marker" in low:
            continue
        else:
            flag("changed_claim", "medium", w)
    for v in verification["items"]:
        if v.lower().startswith("new citation"):
            continue
        if v.lower().startswith("new numbers"):
            new_nums = [n for n in NUMERIC_TOKEN.findall(_strip_tokens(revised)) if not _number_kept(n, original) and n not in context]
            if new_nums:
                flag("added_information", "medium", f"New numbers introduced: {', '.join(dict.fromkeys(new_nums))}")
            continue
        flag("added_information", "medium", v)

    for sev, msg in negation_changes(original, revised):
        flag("certainty", sev, msg)
    for sev, msg in qualifier_changes(original, revised):
        flag("certainty", sev, msg)

    for term in key_terms or []:
        t = term.strip()
        if len(t) < 3 or len(t) > 60:
            continue
        if t.lower() in original.lower() and t.lower() not in revised.lower():
            flag("terminology", "medium", f"Key term no longer present: “{t}”.")
    # De-duplicate identical messages.
    seen = set()
    out = []
    for f in flags:
        k = (f["type"], f["message"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


NUMBER_WORDS = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
                "10": "ten", "11": "eleven", "12": "twelve", "20": "twenty", "100": "hundred", "1000": "thousand"}


# A minus sign is part of the number ("-02:00" ≠ "02:00"); in a range like "10-12" the hyphen follows a digit and is not.
NUMERIC_TOKEN = re.compile(r"(?<![\w.,:])-?\d+(?:[.,:]\d+)*%?(?![\w])")


def _strip_tokens(t: str) -> str:
    return re.sub(r"⟦[A-Z]\d+⟧", " ", t)


def _number_kept(n: str, revised: str) -> bool:
    """Is the number ``n`` still in ``revised``? Matching is by whole token, so '02:00' is not found inside '00:02'.
    Thousands separators may change ('1,840' = '1840'), a percentage may be written 'per cent', and small numbers
    may be spelled out ('3' = 'three')."""
    toks = set(NUMERIC_TOKEN.findall(revised))
    if n in toks:
        return True
    base = n.replace(",", "").rstrip("%")
    if n.endswith("%"):
        return any(t.endswith("%") and t.replace(",", "").rstrip("%") == base for t in toks) or \
            bool(re.search(rf"(?<![\d.]){re.escape(base)}\s*(?:%|per ?cent)", revised, re.I))
    if base in {t.replace(",", "").rstrip("%") for t in toks}:
        return True  # same figure; the unit is written differently ("9%" ↔ "9 per cent")
    w = NUMBER_WORDS.get(base)
    return bool(w and re.search(rf"\b{w}\b", revised, re.I))


# ------------------------------------------------------------------ negations and qualifiers
_NEG = re.compile(r"\b(not|no|never|none|neither|nor|without|cannot|\w+n't|unless)\b", re.I)
_NEG_IGNORE = re.compile(r"\b(not only|no doubt|not least|no longer than|no more than|no less than|not just|rather than|"
                         r"(?:please\s+)?(?:do not|don't) hesitate)\b", re.I)


def _neg_heads(text: str) -> list[str]:
    """For every negation, the first content word it applies to ("not necessarily causal" → "necessarily")."""
    t = _NEG_IGNORE.sub(" ", text.replace("’", "'"))
    heads = []
    for m in _NEG.finditer(t):
        after = [w.lower() for w in WORD_RE.findall(t[m.end():m.end() + 80])]
        content = [w for w in after if len(w) > 2 and w not in ("the", "and", "any", "yet", "been", "have", "has", "had", "was", "were",
                                                                  "are", "will", "would", "should", "could", "can", "this", "that", "its")]
        heads.append(content[0] if content else "")
    return heads


def negation_changes(original: str, revised: str) -> list[tuple[str, str]]:
    """Negations the rewrite lost (the negated word survives without a negation nearby) or added.

    When the number of negations is unchanged the negation has usually just moved ("this is not true" →
    "you don't need…"), so the finding is reported at medium rather than high severity."""
    o_heads, r_heads = _neg_heads(original), _neg_heads(revised)
    sev = "medium" if len(o_heads) == len(r_heads) else "high"
    out = []
    r_low = revised.replace("’", "'").lower()
    for h in o_heads:
        if not h or h in r_heads or h[:5] in {x[:5] for x in r_heads if x}:
            continue
        # The negated word is still there, but nothing negates it any more → the claim may be reversed.
        if re.search(rf"\b{re.escape(h)}", r_low) and not re.search(rf"\b(un|in|im|non-?){re.escape(h[:4])}", r_low):
            out.append((sev, f"A negation was lost: the original negates “{h}”, the revision no longer does. Check the claim is not reversed."))
    added = [h for h in r_heads if h and h not in o_heads and h[:5] not in {x[:5] for x in o_heads if x}
             and re.search(rf"\b{re.escape(h)}", original.lower())]
    for h in added[:2]:
        out.append((sev, f"A negation was added before “{h}”, which the original states positively. Check the claim is not reversed."))
    return out[:3]


QUALIFIER_CLASSES: dict[str, list[str]] = {
    "approximation": ["approximately", "roughly", "about", "around", "nearly", "almost", "some", "~", "or so", "up to", "estimated"],
    "possibility": ["may", "might", "could", "can", "possibly", "perhaps", "potentially", "possible"],
    "tendency": ["often", "usually", "typically", "generally", "tends to", "tend to", "frequently", "commonly", "likely", "probably"],
    "non-necessity": ["not necessarily", "not always", "doesn't necessarily", "does not necessarily", "need not", "isn't necessarily",
                      "is not necessarily"],
    "majority": ["most", "majority", "mostly", "largely", "much of", "the bulk of"],
    "average": ["on average", "average", "averaged", "mean"],
    "recommendation": ["should", "ought to", "consider", "recommend", "recommended", "advisable"],
    "requirement": ["must", "need to", "needs to", "have to", "has to", "required", "mandatory"],
    "limitation": ["only", "solely", "exclusively"],
    "universality": ["always", "never", "all", "every", "entirely", "completely", "fully"],
    "evidence": ["suggests", "suggest", "indicates", "indicate", "appears to", "seems to", "promising", "preliminary"],
}
# Classes whose loss most often turns a careful claim into an overstatement.
HIGH_CLASSES = {"approximation", "non-necessity", "possibility"}
_QUANTITY = (r"(?:\d|one|two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand|million|billion|half|a half|a third|"
             r"a quarter|third|quarter|twice|double|a dozen|dozen|percent)")
_NEEDS_QUANTITY = {"about", "around", "some", "almost", "nearly", "up to"}
# "the most important" is a superlative, not a claim about a majority.
_SUPERLATIVE_MOST = re.compile(r"\b(?:the|a|an|one of the|among the)\s+most\b", re.I)
# Phrases that contain a modal or frequency word without qualifying any claim: "how hot the pan should be"
# describes, it does not recommend; "not true at all" is emphasis, not a universal claim.
_EMPTY_QUALIFIER_PHRASES = re.compile(
    r"\b(?:how|what|when|where|whether|which|who)\b(?:\s+\S+){0,3}?\s+should\b|\bat all\b|\ball the (?:time|way|same)\b|"
    r"\bit (?:should|must|can|could|may|might) be (?:noted|said|argued|seen|mentioned|stressed|emphasi[sz]ed)(?: that)?\b|"
    r"\bit is (?:important|worth|crucial|essential) (?:to note|noting|mentioning)(?: that)?\b|"
    r"\b(?:more|less|as) often (?:than|as)\b|\bcan be found\b|\bas soon as possible\b", re.I)
_CONDITIONAL_SHOULD = re.compile(r"\bshould\s+(?:you|they|we|anyone|there|this|that|it)\s+(?:have|need|require|wish|want|find|be|encounter|run)\b", re.I)


def _qualifier_hits(text: str, cls: str, words: list[str]) -> list[str]:
    hits = []
    for w in words:
        if w in _NEEDS_QUANTITY:
            rx = r"(?<![\w-])" + re.escape(w) + r"\s+(?:\S+\s+){0,2}?" + _QUANTITY
        else:
            rx = r"(?<![\w-])" + re.escape(w) + r"(?![\w-])"
        for m in re.finditer(rx, text, re.I):
            if w == "most" and _SUPERLATIVE_MOST.search(text[max(0, m.start() - 14):m.end()]):
                continue
            hits.append(w)
            break
    return hits


def qualifier_changes(original: str, revised: str) -> list[tuple[str, str]]:
    """Qualifier classes (approximation, possibility, majority…) present in the original but absent from the revision.

    Swapping one qualifier for another of the same class ("roughly" → "about") is fine; losing the class
    entirely changes how strong the claim is ("roughly 30 minutes" → "30 minutes")."""
    o = _EMPTY_QUALIFIER_PHRASES.sub(" ", _CONDITIONAL_SHOULD.sub(" ", original.replace("’", "'")))
    r = _EMPTY_QUALIFIER_PHRASES.sub(" ", _CONDITIONAL_SHOULD.sub(" ", revised.replace("’", "'")))
    out = []
    for cls, words in QUALIFIER_CLASSES.items():
        hits = _qualifier_hits(o, cls, words)
        if hits and not _qualifier_hits(r, cls, words):
            # "can" mostly expresses ability; losing it alone is worth a look but rarely an overstatement.
            sev = "high" if cls in HIGH_CLASSES and hits != ["can"] else "medium"
            effect = "optional rather than required" if cls == "requirement" else \
                "stronger (a recommendation became a requirement or a plain statement)" if cls == "recommendation" else \
                "broader than the original" if cls == "limitation" else "more certain or exact than the original"
            article = "An" if cls in ("approximation", "average", "evidence") else "A"
            out.append((sev, f"{article} {cls} qualifier was dropped (“{hits[0]}”), so the claim may now sound {effect}."))
    return out[:3]


_CLAUSE_SPLIT = re.compile(r"\s*(?:;|—|–|\s-\s)\s*")


def unsupported_sentences(original: str, revised: str, context: str = "") -> list[str]:
    """Sentences or clauses of the revision whose content words are mostly absent from the original (and its
    neighbouring text): likely additions — new claims, examples or generic filler the author did not write."""
    from app.services.humanizer.semantics import _content, overlap

    allowed = _content(original + " " + context)
    out = []
    for s, e in split_sentences(revised):
        for clause in _CLAUSE_SPLIT.split(revised[s:e]):
            cw = _content(clause)
            if len(cw) < 4:
                continue
            if overlap(cw, allowed) < 0.45:
                out.append(clause.strip())
    return out[:3]


def point_coverage(points: list[str], revised: str) -> list[str]:
    """Points from the semantic map whose content no longer appears in the revision (heuristic, conservative)."""
    from app.services.humanizer.semantics import grounded

    return [p for p in points if not grounded(p, revised, min_share=0.34)]


def summarize(flags: list[dict], *, checked: int, changed: int, ai_checked: bool, ai_note: str | None = None) -> dict:
    by = {"high": 0, "medium": 0, "low": 0}
    for f in flags:
        by[f["severity"]] = by.get(f["severity"], 0) + 1
    if not flags:
        statement = ("No meaning problems were found by the automated checks" + (" or the AI comparison" if ai_checked else "") +
                     ". These checks catch common problems but cannot prove the meaning is identical; read the changes before using them.")
    else:
        statement = f"{len(flags)} possible change(s) in meaning need your review."
    return {"checked_blocks": checked, "changed_blocks": changed, "flags_by_severity": by, "ai_checked": ai_checked,
            "ai_note": ai_note, "statement": statement}


def _paras(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]


def validate_texts(original: str, revised: str, key_terms: list[str] | None = None) -> dict:
    """Validate a whole revised text against the original, e.g. after the user edited the output."""
    a, b = _paras(original), _paras(revised)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    flags: list[dict] = []
    checked = changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            checked += i2 - i1
            continue
        oa, rb = a[i1:i2], b[j1:j2]
        checked += len(oa)
        changed += max(len(oa), len(rb))
        if tag == "delete":
            for p in oa:
                if WORD_RE.search(p):
                    flags.append({"block_id": None, "type": "missing_information", "severity": "high", "source": "automated",
                                  "message": "A paragraph from the original is missing.", "original_excerpt": _excerpt(p), "revised_excerpt": ""})
            continue
        if tag == "insert":
            for p in rb:
                flags.append({"block_id": None, "type": "added_information", "severity": "low", "source": "automated",
                              "message": "A new paragraph was added; check it contains nothing unsupported.", "original_excerpt": "",
                              "revised_excerpt": _excerpt(p)})
            continue
        # replace: compare as joined text so merged / split paragraphs are handled.
        flags += compare_block(None, "\n\n".join(oa), "\n\n".join(rb), key_terms=key_terms)
    # Whole-document safety net for exact material.
    for q in QUOTE_RE.findall(original):
        if q not in revised and not any(f["type"] == "quotation" and q[:40] in f["message"] for f in flags):
            flags.append({"block_id": None, "type": "quotation", "severity": "high", "source": "automated",
                          "message": f"A quotation was changed or removed: {_excerpt(q, 80)}", "original_excerpt": _excerpt(q), "revised_excerpt": ""})
    flags.sort(key=lambda f: SEVERITY[f["severity"]])
    return {"flags": flags, "summary": summarize(flags, checked=checked, changed=changed, ai_checked=False)}


# Small models sometimes list "issues" that say there is no issue.
_NO_ISSUE = re.compile(r"^\W*(same|unchanged|identical|no (real )?(issue|problem|change)s?|none|n/?a|preserved|meaning (is )?preserved|ok)\b", re.I)

_NO_ISSUE_ANY = re.compile(r"\b(no (real )?(change|issue|problem)s?( in meaning)?|unchanged|present in both|identical|same meaning|"
                           r"meaning (is )?(preserved|unchanged|the same))\b", re.I)

AI_TYPES = {"missing_information", "changed_claim", "number_or_date", "terminology", "citation", "quotation", "certainty", "argument",
            "added_information", "contradiction"}


def ai_flags(parsed: dict | None, valid_ids: set[int], originals: dict[int, str], revised: dict[int, str]) -> list[dict]:
    out = []
    if not isinstance(parsed, dict):
        return out
    for it in parsed.get("issues") or []:
        if not isinstance(it, dict):
            continue
        try:
            bid = int(it.get("block"))
        except (TypeError, ValueError):
            continue
        if bid not in valid_ids:
            continue
        kind = it.get("type") if it.get("type") in AI_TYPES else "changed_claim"
        sev = it.get("severity") if it.get("severity") in SEVERITY else "medium"
        expl = str(it.get("explanation") or "").strip()
        if not expl or _NO_ISSUE.search(expl) or (len(expl) < 90 and _NO_ISSUE_ANY.search(expl)):
            continue
        # Ground the AI's claim: its quoted excerpts must actually occur in the texts, otherwise downgrade.
        o_ex = str(it.get("original") or "")[:200]
        r_ex = str(it.get("revised") or "")[:200]
        grounded = (not o_ex or o_ex.lower()[:40] in originals[bid].lower()) and (not r_ex or r_ex.lower()[:40] in revised[bid].lower())
        if not grounded and sev == "high":
            sev = "medium"
        out.append({"block_id": bid, "type": kind, "severity": sev, "source": "ai", "message": expl[:300],
                    "original_excerpt": _excerpt(o_ex or originals[bid]), "revised_excerpt": _excerpt(r_ex or revised[bid]),
                    "grounded": grounded})
    return out
