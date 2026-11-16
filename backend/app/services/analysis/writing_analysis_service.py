"""Deterministic writing-quality checks.

These rules run instantly, offline, without a model. They are intentionally
conservative: formal or academic phrasing is not flagged just for being
formal. The optional LLM reviewer (``llm_review_service``) adds
context-dependent findings on top.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from app.services.analysis.common import analyzable, make_finding
from app.services.extraction.sentences import WORD_RE

MODULE = "writing"

WORDY = {
    "in order to": "to",
    "due to the fact that": "because",
    "owing to the fact that": "because",
    "in spite of the fact that": "although",
    "despite the fact that": "although",
    "at this point in time": "now",
    "at the present time": "currently",
    "in the event that": "if",
    "for the purpose of": "to",
    "with regard to": "regarding",
    "with respect to": "regarding",
    "in the near future": "soon",
    "a large number of": "many",
    "a majority of": "most",
    "has the ability to": "can",
    "have the ability to": "can",
    "is able to": "can",
    "are able to": "can",
    "in close proximity to": "near",
    "on a daily basis": "daily",
    "on a regular basis": "regularly",
    "it is important to note that ": "",
    "it should be noted that ": "",
    "the reason why is because": "because",
    "each and every": "each",
    "first and foremost": "first",
    "until such time as": "until",
    "in light of the fact that": "because",
    "prior to": "before",
    "subsequent to": "after",
    "in the process of": "",
}

REDUNDANT = {
    "end result": "result",
    "past history": "history",
    "future plans": "plans",
    "final outcome": "outcome",
    "basic fundamentals": "fundamentals",
    "completely eliminate": "eliminate",
    "absolutely essential": "essential",
    "advance planning": "planning",
    "added bonus": "bonus",
    "close proximity": "proximity",
    "collaborate together": "collaborate",
    "combine together": "combine",
    "join together": "join",
    "repeat again": "repeat",
    "revert back": "revert",
    "return back": "return",
    "true fact": "fact",
    "unexpected surprise": "surprise",
    "free gift": "gift",
    "new innovation": "innovation",
    "each individual": "each",
    "exact same": "same",
    "past experience": "experience",
    "personal opinion": "opinion",
    "general consensus": "consensus",
    "reason why": "reason",
    "brief summary": "summary",
}

FILLERS = ["basically", "actually", "literally", "really", "very", "quite", "just", "totally", "simply", "obviously", "clearly"]

MISSPELLINGS = {
    "recieve": "receive", "recieved": "received", "teh": "the", "definately": "definitely", "seperate": "separate",
    "occured": "occurred", "occurence": "occurrence", "accomodate": "accommodate", "untill": "until", "wich": "which",
    "alot": "a lot", "acheive": "achieve", "acheived": "achieved", "beleive": "believe", "begining": "beginning",
    "goverment": "government", "enviroment": "environment", "existance": "existence", "independant": "independent",
    "neccessary": "necessary", "necesary": "necessary", "noticable": "noticeable", "persue": "pursue",
    "posession": "possession", "publically": "publicly", "reccomend": "recommend", "recomend": "recommend",
    "refered": "referred", "relevent": "relevant", "succesful": "successful", "successfull": "successful",
    "tommorow": "tomorrow", "truely": "truly", "wierd": "weird", "arguement": "argument", "calender": "calendar",
    "commited": "committed", "concious": "conscious", "dependant": "dependent", "embarass": "embarrass",
    "foward": "forward", "grammer": "grammar", "harrass": "harass", "occassion": "occasion", "priviledge": "privilege",
    "resistence": "resistance", "rythm": "rhythm", "supercede": "supersede", "threshhold": "threshold",
    "accross": "across", "agressive": "aggressive", "apparantly": "apparently", "basicly": "basically",
    "comparision": "comparison", "completly": "completely", "differnt": "different", "enviromental": "environmental",
    "experiance": "experience", "familar": "familiar", "finaly": "finally", "futher": "further", "happend": "happened",
    "immediatly": "immediately", "intresting": "interesting", "knowlege": "knowledge", "liason": "liaison",
    "maintainance": "maintenance", "mispell": "misspell", "paralel": "parallel", "particulary": "particularly",
    "peice": "piece", "preform": "perform", "proffesional": "professional", "realy": "really", "remeber": "remember",
    "responsability": "responsibility", "sucess": "success", "suprise": "surprise", "therefor": "therefore",
    "thier": "their", "tounge": "tongue", "wether": "whether", "writting": "writing", "anaylsis": "analysis",
    "analyse": None, "reserach": "research", "resarch": "research", "studys": "studies", "hte": "the",
}

VARIANT_PAIRS = [
    ("analyse", "analyze"), ("analysed", "analyzed"), ("organisation", "organization"), ("behaviour", "behavior"),
    ("colour", "color"), ("centre", "center"), ("modelling", "modeling"), ("utilise", "utilize"),
    ("realise", "realize"), ("recognise", "recognize"), ("emphasise", "emphasize"), ("programme", "program"),
    ("e-mail", "email"), ("data set", "dataset"), ("well-being", "wellbeing"), ("online", "on-line"),
]

AN_EXCEPTIONS_VOWEL_SOUND_CONSONANT = re.compile(r"^(hour|honest|honou?r|heir|herb\b|mba|mri|fbi|faq|nda|sql|html|x-?ray|llm|ngo|rna|mp)", re.I)
A_EXCEPTIONS_CONSONANT_SOUND_VOWEL = re.compile(r"^(uni[^nmd]|unanim|use|usu|usa|uk\b|eu\b|euro|one\b|once|ubiq|ufo|ure|uter|util|u\b)", re.I)

_spell = None


def _spellchecker():
    global _spell
    if _spell is None:
        try:
            from spellchecker import SpellChecker

            _spell = SpellChecker(language="en", distance=1)
        except Exception:  # noqa: BLE001
            _spell = False
    return _spell or None


_VARIANT_RULES = [("our", "or"), ("ise", "ize"), ("isa", "iza"), ("yse", "yze"), ("tre", "ter"), ("ogue", "og"),
                  ("ence", "ense"), ("lled", "led"), ("lling", "ling"), ("ae", "e"), ("oe", "e"), ("ys", "yz")]


def _is_regional_variant(sc, word: str) -> bool:
    """British/Commonwealth spellings are correct; don't flag them."""
    for a, b in _VARIANT_RULES:
        if a in word and not sc.unknown([word.replace(a, b)]):
            return True
    return False


def _match_case(src: str, repl: str) -> str:
    if not repl:
        return repl
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def analyze_paragraph(para: dict, *, spellcheck: bool, doc_vocab: Counter) -> list[dict]:
    text = para["text"]
    out: list[dict] = []

    # Repeated word ("the the").
    for m in re.finditer(r"\b(\w+)(\s+)(\1)\b", text, re.I):
        if m.group(1).lower() in ("had", "that", "is", "bye", "very", "so", "no", "really"):
            continue
        out.append(make_finding(MODULE, "grammar", para, start=m.start(), end=m.end(), priority="high", confidence=0.95,
                                explanation=f"The word “{m.group(1)}” is repeated.", correction=m.group(1)))

    # Spacing and punctuation.
    for m in re.finditer(r"(?<=\S)  +(?=\S)", text):
        out.append(make_finding(MODULE, "punctuation", para, start=m.start(), end=m.end(), priority="low", confidence=0.9,
                                explanation="Multiple spaces between words.", correction=" "))
    for m in re.finditer(r"(\w) +([,.;:!?])(?!\w)", text):
        s = m.start() + 1
        out.append(make_finding(MODULE, "punctuation", para, start=s, end=m.end(), priority="low", confidence=0.85,
                                explanation="Space before punctuation mark.", correction=m.group(2)))
    for m in re.finditer(r"([a-z]{2,})([,;])([A-Za-z]{2,})", text):
        s = m.start(2)
        out.append(make_finding(MODULE, "punctuation", para, start=s, end=s + 1, priority="low", confidence=0.8,
                                explanation="Missing space after punctuation.", correction=m.group(2) + " "))
    for m in re.finditer(r"([a-z]{2,})\.([A-Z][a-z]+)", text):
        s = m.start() + len(m.group(1))
        out.append(make_finding(MODULE, "punctuation", para, start=s, end=s + 1, priority="low", confidence=0.7,
                                explanation="Missing space after the full stop.", correction=". "))
    for m in re.finditer(r"([!?.,])\1+", text):
        if m.group() == "...":
            continue
        out.append(make_finding(MODULE, "punctuation", para, start=m.start(), end=m.end(), priority="low", confidence=0.8,
                                explanation="Repeated punctuation.", correction=m.group(1)))

    # Sentence-initial lowercase.
    for s in para["sentences"]:
        seg = text[s["start"]:s["end"]]
        mm = re.match(r"[\"'“‘(]*([a-z])", seg)
        if mm and not re.match(r"[a-z]+\.[a-z]", seg) and not re.match(r"(e\.g|i\.e|etc|vs|et al|iPhone|eBay|pH)\b", seg):
            pos = s["start"] + mm.start(1)
            out.append(make_finding(MODULE, "grammar", para, start=pos, end=pos + 1, priority="medium", confidence=0.75,
                                    explanation="Sentence starts with a lowercase letter.", correction=mm.group(1).upper()))

    # a/an.
    for m in re.finditer(r"\b(a|an|A|An)\s+([A-Za-z][\w-]*)", text):
        art, word = m.group(1), m.group(2)
        vowel = word[0].lower() in "aeiou"
        if art.lower() == "a" and vowel and not A_EXCEPTIONS_CONSONANT_SOUND_VOWEL.match(word):
            fix = "an" if art == "a" else "An"
        elif art.lower() == "an" and not vowel and not AN_EXCEPTIONS_VOWEL_SOUND_CONSONANT.match(word) and not (word.isupper() and word[0] in "FHLMNRSX"):
            fix = "a" if art == "an" else "A"
        else:
            continue
        out.append(make_finding(MODULE, "grammar", para, start=m.start(1), end=m.end(1), priority="medium", confidence=0.7,
                                explanation=f"Use “{fix.lower()}” before “{word}”.", correction=fix))

    # Known misspellings + dictionary check.
    flagged_spans = set()
    for m in WORD_RE.finditer(text):
        word = m.group()
        lw = word.lower()
        if lw in MISSPELLINGS and MISSPELLINGS[lw]:
            out.append(make_finding(MODULE, "spelling", para, start=m.start(), end=m.end(), priority="high", confidence=0.95,
                                    explanation=f"“{word}” appears to be misspelled.", correction=_match_case(word, MISSPELLINGS[lw])))
            flagged_spans.add(m.start())
    if spellcheck:
        sc = _spellchecker()
        if sc is not None:
            cands = []
            for m in WORD_RE.finditer(text):
                word = m.group().strip("'’-")
                if (m.start() in flagged_spans or len(word) < 4 or not word.isalpha() or not word.islower()):
                    continue
                if doc_vocab[word.lower()] >= 2:
                    continue  # repeated in the document → likely a deliberate term
                cands.append((m, word))
            unknown = sc.unknown([w for _, w in cands]) if cands else set()
            for m, word in cands:
                if word.lower() in unknown and not _is_regional_variant(sc, word.lower()):
                    corr = sc.correction(word.lower())
                    if corr and corr != word.lower():
                        out.append(make_finding(MODULE, "spelling", para, start=m.start(), end=m.start() + len(word),
                                                priority="medium", confidence=0.6,
                                                explanation=f"“{word}” is not in the dictionary; did you mean “{corr}”? Ignore if it is a technical term.",
                                                correction=corr))

    # Wordy phrases and redundancy.
    for table, cat, why in ((WORDY, "wordiness", "can be expressed more concisely"),
                            (REDUNDANT, "redundancy", "is redundant")):
        for phrase, repl in table.items():
            for m in re.finditer(r"\b" + re.escape(phrase.strip()) + (r"\s*" if phrase.endswith(" ") else r"\b"), text, re.I):
                orig = m.group()
                corr = _match_case(orig, repl) if repl else ""
                if not repl and m.end() < len(text):
                    # Removing a leading phrase: capitalise the following word.
                    nxt = text[m.end():m.end() + 1]
                    if orig[:1].isupper() and nxt.isalpha():
                        end = m.end() + 1
                        out.append(make_finding(MODULE, cat, para, start=m.start(), end=end, priority="low", confidence=0.8,
                                                explanation=f"“{orig.strip()}” {why}.", correction=nxt.upper()))
                        continue
                out.append(make_finding(MODULE, cat, para, start=m.start(), end=m.end(), priority="low", confidence=0.8,
                                        explanation=f"“{orig.strip()}” {why}." + (f" Consider “{repl}”." if repl else " Consider removing it."),
                                        correction=corr))

    # Filler words (only when used 2+ times in the paragraph — single uses are normal).
    lower = text.lower()
    for f in FILLERS:
        hits = list(re.finditer(rf"\b{f}\b", lower))
        if len(hits) >= 2:
            h = hits[1]
            out.append(make_finding(MODULE, "filler", para, start=h.start(), end=h.end(), priority="low", confidence=0.5,
                                    explanation=f"“{f}” is used {len(hits)} times in this paragraph; it may weaken the writing.",
                                    data={"occurrences": len(hits)}))

    # Long and complicated sentences.
    for s in para["sentences"]:
        seg = text[s["start"]:s["end"]]
        wc = len(WORD_RE.findall(seg))
        clauses = seg.count(",") + seg.count(";")
        if wc > 40 or (wc > 30 and clauses >= 4):
            out.append(make_finding(MODULE, "complexity", para, start=s["start"], end=s["end"],
                                    priority="medium" if wc > 50 else "low", confidence=None,
                                    explanation=f"This sentence has {wc} words and {clauses} commas/semicolons. Consider splitting it.",
                                    data={"words": wc}))

    # Repeated phrase within paragraph (3+ word sequence repeated).
    toks = [(m.group().lower(), m.start(), m.end()) for m in WORD_RE.finditer(text)]
    seen: dict[tuple, int] = {}
    reported = set()
    for i in range(len(toks) - 3):
        key = tuple(t[0] for t in toks[i:i + 4])
        if key in seen and key not in reported and not all(k in STOP for k in key):
            j = seen[key]
            if i - j >= 4:
                reported.add(key)
                out.append(make_finding(MODULE, "repetition", para, start=toks[i][1], end=toks[i + 3][2], priority="low",
                                        confidence=None,
                                        explanation=f"The phrase “{' '.join(key)}” is repeated within this paragraph.",
                                        data={"first_at": toks[j][1]}))
        else:
            seen.setdefault(key, i)

    # Repeated sentence openers within the paragraph.
    openers = defaultdict(list)
    for s in para["sentences"]:
        ws = WORD_RE.findall(text[s["start"]:s["end"]])
        if len(ws) >= 3:
            openers[" ".join(ws[:2]).lower()].append(s)
    for op, ss in openers.items():
        if len(ss) >= 3:
            out.append(make_finding(MODULE, "repetition", para, start=ss[0]["start"], end=ss[-1]["end"], priority="low",
                                    explanation=f"{len(ss)} sentences begin with “{op}”. Vary sentence openings for better flow.",
                                    data={"opener": op}))
    return out


STOP = set("the a an of to in and or for on with as by at is are was were be been this that these those it its from".split())


def terminology_consistency(paragraphs: list[dict]) -> list[dict]:
    """Inconsistent spelling variants across the document (e.g. analyse vs analyze)."""
    counts: dict[str, list[tuple[dict, re.Match]]] = defaultdict(list)
    for p in paragraphs:
        if not analyzable(p, include_headings=True):
            continue
        for a, b in VARIANT_PAIRS:
            for v in (a, b):
                for m in re.finditer(rf"\b{re.escape(v)}\b", p["text"], re.I):
                    counts[v].append((p, m))
    out = []
    for a, b in VARIANT_PAIRS:
        if counts[a] and counts[b]:
            major, minor = (a, b) if len(counts[a]) >= len(counts[b]) else (b, a)
            for p, m in counts[minor][:5]:
                out.append(make_finding(MODULE, "terminology", p, start=m.start(), end=m.end(), priority="low", confidence=0.7,
                                        explanation=f"The document uses both “{major}” ({len(counts[major])}×) and “{minor}” ({len(counts[minor])}×). Use one form consistently.",
                                        correction=_match_case(m.group(), major)))
    return out


def analyze_document(paragraphs: list[dict], *, spellcheck: bool = True, on_progress=None) -> list[dict]:
    vocab = Counter(w.lower() for p in paragraphs for w in WORD_RE.findall(p["text"]))
    findings: list[dict] = []
    targets = [p for p in paragraphs if analyzable(p)]
    for i, p in enumerate(targets):
        findings.extend(analyze_paragraph(p, spellcheck=spellcheck, doc_vocab=vocab))
        if on_progress:
            on_progress(i + 1, len(targets), p)
    findings.extend(terminology_consistency(paragraphs))
    return _dedupe(findings)


def _dedupe(findings: list[dict]) -> list[dict]:
    """Drop overlapping findings of lower priority on the same span."""
    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (f["paragraph_id"] or "", f["start"] or 0, rank[f["priority"]]))
    out: list[dict] = []
    for f in findings:
        if f["correction"] is not None and out:
            prev = out[-1]
            if (prev["paragraph_id"] == f["paragraph_id"] and prev["correction"] is not None
                    and prev["start"] is not None and f["start"] is not None and f["start"] < prev["end"]):
                continue
        out.append(f)
    return out
