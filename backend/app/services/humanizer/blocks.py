"""Structure-preserving segmentation of plain text into blocks, and protection of exact spans.

The humanizer works on plain text that uses light Markdown conventions (``# Heading``,
``- item``, ``1. item``, ``> quote``, fenced code). Every block keeps its exact
prefix and trailing separator, so ``join_blocks(parse_blocks(t)) == t`` and only
block *content* is ever rewritten.

Citations, quotations, URLs, e-mail addresses and inline code are replaced by
tokens such as ``⟦Q1⟧`` before text is sent to the model and restored afterwards,
so they cannot be paraphrased.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.services.extraction.sentences import WORD_RE, split_sentences

# Paragraphs longer than this are split at sentence boundaries into parts that are
# rejoined with a single space, so a single model call never has to carry a huge block.
MAX_BLOCK_WORDS = 450

HEADING_MD = re.compile(r"^(#{1,6})[ \t]+")
LIST_MARK = re.compile(r"^([ \t]*)([-*•‣◦]|\d{1,3}[.)]|[a-zA-Z][.)])[ \t]+")
QUOTE_MARK = re.compile(r"^>[ \t]?")
FENCE = re.compile(r"^(```|~~~)")


@dataclass
class Block:
    id: int
    kind: str  # heading | paragraph | list_item | quote | code | blank
    prefix: str  # exact marker text (e.g. "## ", "- ", "> ")
    text: str  # content that may be rewritten
    sep: str  # exact whitespace after the block
    level: int | None = None
    part_of: int | None = None  # for long paragraphs split into parts: id of the first part
    rewritable: bool = True
    words: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _words(t: str) -> int:
    return len(WORD_RE.findall(t))


def _looks_like_plain_heading(line: str, next_is_blank: bool, prev_is_blank: bool) -> bool:
    s = line.strip()
    if not s or not next_is_blank or not prev_is_blank:
        return False
    w = _words(s)
    if w == 0 or w > 12 or len(s) > 90:
        return False
    if s[-1] in ".,;!?\"”’)":
        return False
    if s[-1] == ":":
        return True
    # Title-ish: starts upper-case or numbered ("2.1 Methods").
    return s[0].isupper() or re.match(r"^\d+(\.\d+)*\s", s) is not None


def parse_blocks(text: str) -> list[Block]:
    """Split text into blocks. Exact round-trip: ``join_blocks(parse_blocks(t)) == t``."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    # Each entry: (kind, prefix, content, level, raw_lines_consumed_end_index)
    raw: list[dict] = []
    i = 0
    n = len(lines)
    leading = ""
    # Leading blank lines are carried as the first block's "lead" via a blank block.
    while i < n and not lines[i].strip():
        leading += lines[i] + ("\n" if i < n - 1 else "")
        i += 1
    if leading:
        raw.append({"kind": "blank", "prefix": "", "content": "", "sep": leading})

    def blank_at(j: int) -> bool:
        return j < 0 or j >= n or not lines[j].strip()

    while i < n:
        line = lines[i]
        if FENCE.match(line.strip()):
            fence = FENCE.match(line.strip()).group(1)
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence):
                j += 1
            j = min(j, n - 1)
            content = "\n".join(lines[i:j + 1])
            raw.append({"kind": "code", "prefix": "", "content": content, "end": j})
            i = j + 1
        elif HEADING_MD.match(line):
            m = HEADING_MD.match(line)
            raw.append({"kind": "heading", "prefix": m.group(0), "content": line[m.end():], "level": len(m.group(1)), "end": i})
            i += 1
        elif LIST_MARK.match(line):
            m = LIST_MARK.match(line)
            # Continuation lines (indented, non-blank, not a new marker) belong to the item.
            j = i
            while j + 1 < n and lines[j + 1].strip() and not LIST_MARK.match(lines[j + 1]) and not HEADING_MD.match(lines[j + 1]) \
                    and lines[j + 1].startswith((" ", "\t")):
                j += 1
            content = "\n".join([line[m.end():], *lines[i + 1:j + 1]])
            raw.append({"kind": "list_item", "prefix": m.group(0), "content": content, "end": j})
            i = j + 1
        elif QUOTE_MARK.match(line):
            j = i
            while j + 1 < n and QUOTE_MARK.match(lines[j + 1]):
                j += 1
            m = QUOTE_MARK.match(line)
            rest = [line[m.end():]] + [QUOTE_MARK.sub("", l, count=1) for l in lines[i + 1:j + 1]]
            if j > i:
                # Multi-line block quote: keep the raw lines exactly; never rewritten.
                raw.append({"kind": "quote", "prefix": "", "content": "\n".join(lines[i:j + 1]), "end": j, "raw": True})
            else:
                raw.append({"kind": "quote", "prefix": m.group(0), "content": rest[0], "end": j})
            i = j + 1
        elif line.strip() and _looks_like_plain_heading(line, blank_at(i + 1), blank_at(i - 1)) and (i + 1 < n):
            raw.append({"kind": "heading", "prefix": "", "content": line, "level": 2, "end": i, "plain": True})
        elif line.strip():
            j = i
            while j + 1 < n and lines[j + 1].strip() and not HEADING_MD.match(lines[j + 1]) and not LIST_MARK.match(lines[j + 1]) \
                    and not QUOTE_MARK.match(lines[j + 1]) and not FENCE.match(lines[j + 1].strip()):
                j += 1
            raw.append({"kind": "paragraph", "prefix": "", "content": "\n".join(lines[i:j + 1]), "end": j})
            i = j + 1
        else:
            i += 1
            continue
        # Separator: the newline that ends the block plus any blank lines after it.
        end = raw[-1]["end"]
        sep = "\n" if end < n - 1 else ""
        k = end + 1
        while k < n and not lines[k].strip():
            sep += lines[k] + ("\n" if k < n - 1 else "")
            k += 1
        raw[-1]["sep"] = sep
        i = k

    blocks: list[Block] = []
    for r in raw:
        kind = r["kind"]
        content = r["content"]
        rewritable = kind in ("paragraph", "list_item", "quote") and not r.get("raw") and _words(content) > 0
        if kind == "quote":
            rewritable = False  # quoted material is preserved unless the user edits it explicitly
        if kind == "paragraph" and _words(content) > MAX_BLOCK_WORDS and "\n" not in content.strip():
            parts = _split_long(content)
            first_id = len(blocks)
            for pi, (ptxt, psep) in enumerate(parts):
                last = pi == len(parts) - 1
                blocks.append(Block(id=len(blocks), kind="paragraph", prefix="", text=ptxt, sep=r["sep"] if last else psep,
                                    part_of=first_id, rewritable=True, words=_words(ptxt)))
            continue
        blocks.append(Block(id=len(blocks), kind=kind, prefix=r["prefix"], text=content, sep=r.get("sep", ""),
                            level=r.get("level"), rewritable=rewritable, words=_words(content),
                            meta={"plain_heading": True} if r.get("plain") else {}))
    return blocks


def _split_long(text: str) -> list[tuple[str, str]]:
    """Split a long paragraph at sentence boundaries into ~MAX_BLOCK_WORDS/1.5 parts, keeping exact whitespace."""
    spans = split_sentences(text)
    if len(spans) < 2:
        return [(text, "")]
    target = int(MAX_BLOCK_WORDS / 1.5)
    parts: list[tuple[int, int]] = []
    start = spans[0][0]
    cur_words = 0
    last_end = spans[0][0]
    for s, e in spans:
        w = _words(text[s:e])
        if cur_words and cur_words + w > target:
            parts.append((start, last_end))
            start = s
            cur_words = 0
        cur_words += w
        last_end = e
    parts.append((start, len(text)))
    out = []
    for idx, (s, e) in enumerate(parts):
        nxt = parts[idx + 1][0] if idx + 1 < len(parts) else e
        out.append((text[s:e], text[e:nxt]))
    # Preserve any leading whitespace of the paragraph on the first part.
    if spans[0][0] > 0:
        out[0] = (text[:spans[0][0]] + out[0][0], out[0][1])
    return out


def join_blocks(blocks: list[Block] | list[dict]) -> str:
    out = []
    for b in blocks:
        d = b if isinstance(b, dict) else b.to_dict()
        out.append(d["prefix"] + d["text"] + d["sep"])
    return "".join(out)


# ------------------------------------------------------------------ protection
CITATION_RE = re.compile(
    r"\((?:[^()]*?(?:\b(?:1[5-9]|20)\d{2}[a-z]?\b|\bn\.d\.)[^()]*?)\)"  # (Smith, 2020), (Smith & Lee 2019, p. 4)
    r"|\[\d+(?:\s*[,–\-]\s*\d+)*\]"  # [1], [2, 3], [4–6]
    r"|\[[A-Z][A-Za-z]+(?:\s+et al\.)?,?\s+\d{4}[a-z]?\]"  # [Smith, 2020]
)
QUOTE_RE = re.compile(r"“[^”\n]{2,600}”|\"[^\"\n]{2,600}\"|‘[^’\n]{3,400}\s[^’\n]{1,400}’")
URL_RE = re.compile(r"\bhttps?://[^\s<>()]+[^\s<>().,;:!?'\"”’]|\bwww\.[^\s<>()]+[^\s<>().,;:!?'\"”’]")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
CODE_RE = re.compile(r"`[^`\n]+`")
TOKEN_RE = re.compile(r"⟦([CQULK])(\d+)⟧")

KIND_LABEL = {"C": "citation", "Q": "quotation", "U": "link", "L": "e-mail address", "K": "inline code"}


def protect(text: str, start_index: dict[str, int] | None = None) -> tuple[str, dict[str, str]]:
    """Replace exact spans with tokens. Returns (protected_text, {token: original})."""
    counters = start_index if start_index is not None else {}
    mapping: dict[str, str] = {}
    out = text
    # Order matters: code and URLs first so quotes/citations inside them are not double-protected.
    for kind, rx in (("K", CODE_RE), ("U", URL_RE), ("L", EMAIL_RE), ("Q", QUOTE_RE), ("C", CITATION_RE)):
        def repl(m: re.Match, kind: str = kind) -> str:
            if TOKEN_RE.fullmatch(m.group(0)):
                return m.group(0)
            counters[kind] = counters.get(kind, 0) + 1
            tok = f"⟦{kind}{counters[kind]}⟧"
            mapping[tok] = m.group(0)
            return tok
        out = rx.sub(repl, out)
    return out, mapping


def restore(text: str, mapping: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Restore tokens. Returns (text, missing_tokens, unexpected_or_duplicated_tokens)."""
    found = [m.group(0) for m in TOKEN_RE.finditer(text)]
    extra = [t for t in found if t not in mapping]
    dup = sorted({t for t in found if found.count(t) > 1})
    restored = TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
    # A model sometimes writes the protected text out instead of its token. That is fine as long as
    # it is reproduced exactly (character for character); anything else counts as missing.
    missing = [t for t in mapping if t not in found and mapping[t] not in TOKEN_RE.sub("", text)]
    return restored, missing, extra + dup


def legend(mapping: dict[str, str]) -> str:
    if not mapping:
        return ""
    lines = [f"{tok} = {KIND_LABEL[tok[1]]}: {val[:160]}" for tok, val in mapping.items()]
    return "\n".join(lines)
