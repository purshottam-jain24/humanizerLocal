"""Stage A — semantic understanding.

Two layers, both feeding the rewrite prompts *and* the validation stage:

1. A deterministic **meaning ledger** per block: the exact qualifiers (hedges, certainty and scope
   words), negations, logical links between sentences (cause, contrast, concession, condition,
   purpose) and the numbers/dates a rewrite must keep. Always computed; costs nothing.
2. An AI **semantic map** per batch of blocks (Medium/Extensive on texts long enough to need it):
   the points each block makes and how it relates to the previous block. Points are kept only when
   they are grounded in the block (enough of their content words occur in it), so a map entry can't
   smuggle invented facts into later stages.
"""

from __future__ import annotations

import re

from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer.blocks import TOKEN_RE

# Words whose presence or absence changes how strong a claim is.
QUALIFIERS = [
    "may", "might", "could", "can", "would", "should", "must", "will", "possibly", "probably", "likely", "unlikely", "perhaps",
    "approximately", "roughly", "about", "around", "nearly", "almost", "up to", "at least", "at most", "more than", "less than",
    "fewer than", "on average", "typically", "usually", "often", "sometimes", "rarely", "always", "never", "generally",
    "mostly", "most", "some", "many", "few", "all", "every", "only", "largely", "partly", "slightly", "significantly",
    "not necessarily", "suggests", "suggest", "indicates", "appears", "seems", "tends to", "tend to", "promising",
]
NEGATIONS = ["not", "no", "never", "none", "neither", "nor", "without", "cannot", "can't", "don't", "doesn't", "isn't", "aren't",
             "won't", "wasn't", "weren't", "hasn't", "haven't", "unless"]
RELATIONS = {
    "cause": ["because", "since", "due to", "as a result of", "owing to", "caused by"],
    "consequence": ["therefore", "so that", "which means", "as a result", "consequently", "thus", "hence", "so"],
    "contrast": ["but", "however", "whereas", "while", "yet", "on the other hand", "in contrast"],
    "concession": ["although", "though", "even though", "despite", "even if", "nevertheless", "nonetheless"],
    "condition": ["if", "unless", "provided that", "only when", "as long as", "in that case"],
    "purpose": ["in order to", "so as to", "to limit", "to ensure", "to avoid"],
}
# A sentence that asks the reader to do (or not do) something must stay a request, not become a statement of fact.
REQUEST_RE = re.compile(r"\b(please|kindly|(?:i|we) (?:would like to )?(?:kindly )?(?:ask|request)|would you|could you|can you|"
                        r"(?:i|we)'?d like you|make sure|be sure to|remember to)\b", re.I)
NUMBER_RE = re.compile(r"(?<![\w⟦])\d[\d,.:]*(?:\s?%|\s?(?:ms|s|kb|mb|gb|km|kg|cm|mm|m|h|min|minutes|hours|seconds|days|years|points))?\b", re.I)

MAP_BATCH_WORDS = 900
MAX_POINTS = 4


def _sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in split_sentences(text)]


def _window(sentence: str, start: int, end: int, before: int = 2, after: int = 3) -> str:
    """The matched phrase with a few words either side, so the model sees what the qualifier qualifies."""
    left = WORD_RE.findall(sentence[:start])[-before:]
    right = WORD_RE.findall(sentence[end:])[:after]
    return " ".join(left + [sentence[start:end]] + right)


def ledger(text: str) -> dict:
    """Deterministic meaning ledger of one block (protected tokens are ignored)."""
    clean = TOKEN_RE.sub(" ", text)
    qualifiers: list[str] = []
    negations: list[str] = []
    links: list[dict] = []
    for s in _sentences(clean):
        low = s.lower()
        for q in QUALIFIERS:
            for m in re.finditer(r"\b" + re.escape(q) + r"\b", low):
                # "can" and "will" are too common to be worth listing unless they carry a claim's strength in short text.
                if q in ("can", "will", "would", "some", "all", "most", "about") and len(qualifiers) >= 6:
                    continue
                qualifiers.append(_window(s, m.start(), m.end()))
        for n in NEGATIONS:
            for m in re.finditer(r"\b" + re.escape(n) + r"\b", low):
                if re.match(r"\s+(only|least)\b", low[m.end():]) or (n == "not" and re.match(r"\s+hesitate", low[m.end():])):
                    continue
                negations.append(_window(s, m.start(), m.end(), 2, 3))
        for kind, words in RELATIONS.items():
            for w in words:
                m = re.search(r"\b" + re.escape(w) + r"\b", low)
                if m and not (w == "so" and not re.search(r"\bso\b(?! that)(?! much| many| far| on)", low)):
                    links.append({"kind": kind, "text": _window(s, m.start(), m.end(), 1, 5)})
                    break
    numbers = sorted({m.group(0).strip() for m in NUMBER_RE.finditer(clean)})
    requests = [" ".join(s.split()[:16]) for s in _sentences(clean)
                if REQUEST_RE.search(s) and not re.search(r"\b(?:do not|don't) hesitate\b", s, re.I)]
    return {"qualifiers": _dedupe(qualifiers)[:10], "negations": _dedupe(negations)[:6], "links": _dedupe_links(links)[:6],
            "numbers": numbers[:20], "requests": requests[:3]}


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        k = it.lower()
        if not any(k in s.lower() or s.lower() in k for s in seen):
            seen.append(it)
    return seen


def _dedupe_links(items: list[dict]) -> list[dict]:
    out, seen = [], set()
    for it in items:
        k = it["text"].lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def describe_ledger(bid: int, led: dict, points: list[str] | None, relation: str | None) -> str:
    """Compact per-block 'meaning to keep' notes for the rewrite prompt."""
    parts = []
    if points:
        parts.append("points: " + " | ".join(points[:MAX_POINTS]))
    if relation:
        parts.append(f"role: {relation}")
    if led.get("qualifiers"):
        parts.append("keep qualifier strength: " + "; ".join(f"“{q}”" for q in led["qualifiers"][:6]))
    if led.get("negations"):
        parts.append("keep negation: " + "; ".join(f"“{n}”" for n in led["negations"][:4]))
    if led.get("links"):
        parts.append("keep logic: " + "; ".join(f"{l['kind']} (“{l['text']}”)" for l in led["links"][:4]))
    if led.get("requests"):
        parts.append("keep as a request to the reader, not a statement of fact: " + "; ".join(f"“{r}…”" for r in led["requests"][:2]))
    if not parts:
        return ""
    return f"Block {bid} — " + " · ".join(parts)


# ------------------------------------------------------------------ AI semantic map
def map_batches(block_ids: list[int], words: dict[int, int]) -> list[list[int]]:
    batches: list[list[int]] = []
    total = 0
    for bid in block_ids:
        if batches and total + words[bid] <= MAP_BATCH_WORDS:
            batches[-1].append(bid)
            total += words[bid]
        else:
            batches.append([bid])
            total = words[bid]
    return batches


def map_messages(blocks: list[tuple[int, str]], doc_context: str) -> list[dict]:
    """Blocks are labelled P1…Pn (models renumber arbitrary ids); ``parse_map`` maps the labels back."""
    system = ("You help an editor understand a document before it is revised. For each block, list the distinct points it makes "
              "(claims, findings, instructions, conditions, caveats), each as a short paraphrase of at most 15 words that reuses the "
              "block's own key words and keeps its exact strength (\"may\" stays \"may\", \"roughly\" stays \"roughly\", "
              "\"not necessarily\" stays). Also say in a few words what role the block plays relative to the one before it "
              "(e.g. 'gives evidence for the previous claim', 'adds a caveat', 'new topic'). "
              "Do not add anything that is not in the text. Respond with one JSON object only.")
    body = "\n\n".join(f"[P{i + 1}]\n{txt}" for i, (_bid, txt) in enumerate(blocks))
    schema = '{"blocks": [{"block": "P1", "points": ["..."], "role": "..."}]}'
    user = ((f"About the document:\n{doc_context}\n\n" if doc_context else "") + f"{body}\n\n"
            f"Return JSON in this shape, one entry per block (P1 to P{len(blocks)}), at most {MAX_POINTS} points each:\n{schema}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_STOP = set("a an the and or but of to in on at by for with from as is are was were be been it its this that these those which who "
            "their they them we our you your has have had not no can may might could will would should than then so if".split())


def _content(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(TOKEN_RE.sub(" ", text)) if w.lower() not in _STOP and len(w) > 2}


_SUFFIXES = ("ingly", "edly", "ations", "ation", "ments", "ment", "ness", "ings", "ing", "ies", "ied", "ers", "est", "ed", "es", "er", "ly", "s")


def stem(w: str) -> str:
    """A crude, dependency-free stem for overlap checks ("reads"/"read", "writes"/"written"/"write" → "writ")."""
    w = w.lower()
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:4] if len(w) > 4 else w


def overlap(words: set[str], reference: set[str]) -> float:
    """Share of ``words`` that occur in ``reference`` (exactly or by stem)."""
    if not words:
        return 0.0
    stems = {stem(w) for w in reference}
    return sum(1 for w in words if w in reference or stem(w) in stems) / len(words)


def grounded(point: str, block_text: str, min_share: float = 0.5) -> bool:
    """A point is grounded when at least half of its content words (or their stems) occur in the block."""
    pw = _content(point)
    return bool(pw) and overlap(pw, _content(block_text)) >= min_share


def _label_to_bid(label: object, order: list[int]) -> int | None:
    """'P2', 2 (1-based, as labelled) or a real block id → the real block id."""
    m = re.match(r"^\s*\[?p?\s*(\d+)\]?\s*$", str(label), re.I)
    if not m:
        return None
    n = int(m.group(1))
    if str(label).strip().lower().lstrip("[").startswith("p") or n not in order:
        return order[n - 1] if 1 <= n <= len(order) else (order[0] if n == 0 else None)
    return n


def parse_map(parsed: object, texts: dict[int, str]) -> dict[int, dict]:
    """Keep only well-formed, grounded entries for known blocks.

    ``texts`` must be in prompt order. A point that is not grounded in the block it was attributed to but is
    grounded in exactly one other block of the batch is moved there (small models misnumber blocks)."""
    order = list(texts)
    out: dict[int, dict] = {bid: {"points": [], "role": None, "dropped": 0} for bid in order}
    if not isinstance(parsed, dict):
        return {}
    for it in parsed.get("blocks") or []:
        if not isinstance(it, dict):
            continue
        bid = _label_to_bid(it.get("block"), order)
        if bid is None:
            continue
        role = str(it.get("role") or "").strip()[:120] or None
        if role and not out[bid]["role"]:
            out[bid]["role"] = role
        for p in (it.get("points") or []):
            p = str(p).strip()[:160]
            if not p:
                continue
            if grounded(p, texts[bid]):
                target = bid
            else:
                homes = [b for b in order if b != bid and grounded(p, texts[b])]
                target = homes[0] if len(homes) == 1 else None
            if target is None:
                out[bid]["dropped"] += 1
            elif len(out[target]["points"]) < MAX_POINTS:
                out[target]["points"].append(p)
    return {bid: v for bid, v in out.items() if v["points"] or v["role"] or v["dropped"]}
