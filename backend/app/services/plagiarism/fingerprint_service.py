"""Text fingerprinting: normalised tokens, k-gram hashing, winnowing, MinHash.

Winnowing (Schleimer, Wilkerson & Aiken, 2003) selects a robust subset of
k-gram hashes: any shared passage of at least ``k + w - 1`` tokens is
guaranteed to share at least one fingerprint.
"""

from __future__ import annotations

import hashlib
import re
import struct
import unicodedata

K = 5  # word k-grams
WINDOW = 4
TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
MINHASH_PERMS = 64
_MASK64 = (1 << 64) - 1


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    return t.replace("’", "'").replace("‘", "'")


def tokens(text: str) -> list[str]:
    return TOKEN.findall(normalize(text))


def token_spans(text: str) -> list[tuple[str, int, int]]:
    norm = normalize(text)
    # NFKC can change length for some characters; fall back to lower() when it does.
    if len(norm) != len(text):
        norm = text.lower().replace("’", "'").replace("‘", "'")
    return [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(norm)]


def _h(s: str) -> int:
    v = struct.unpack(">q", hashlib.blake2b(s.encode(), digest_size=8).digest())[0]
    return v


def kgram_hashes(toks: list[str], k: int = K) -> list[int]:
    return [_h(" ".join(toks[i:i + k])) for i in range(len(toks) - k + 1)]


def winnow(hashes: list[int], window: int = WINDOW) -> list[tuple[int, int]]:
    """Return (hash, position) fingerprints."""
    if not hashes:
        return []
    if len(hashes) <= window:
        m = min(range(len(hashes)), key=lambda i: hashes[i])
        return [(hashes[m], m)]
    out: list[tuple[int, int]] = []
    last = -1
    for start in range(len(hashes) - window + 1):
        win = hashes[start:start + window]
        # rightmost minimum, per the winnowing paper
        mi = min(range(window), key=lambda i: (win[i], -i))
        pos = start + mi
        if pos != last:
            out.append((hashes[pos], pos))
            last = pos
    return out


def fingerprints(text: str) -> list[tuple[int, int]]:
    return winnow(kgram_hashes(tokens(text)))


def minhash(toks: list[str], k: int = 3, perms: int = MINHASH_PERMS) -> list[int]:
    shingles = {" ".join(toks[i:i + k]) for i in range(max(1, len(toks) - k + 1))}
    sig = []
    for seed in range(perms):
        sig.append(min((_h(f"{seed}:{s}") & _MASK64) for s in shingles) if shingles else 0)
    return sig


def minhash_similarity(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)
