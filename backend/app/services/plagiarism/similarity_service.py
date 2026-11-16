"""Similarity analysis against the local corpus, within the document, and
(optionally, when the user enables it) against external scholarly metadata.

Similarity is evidence for a human to review — it is never reported as
proof of plagiarism. Categories:

  exact             long verbatim overlap
  near_verbatim     most of the passage matches with small edits
  similar_phrasing  shared phrase runs or high lexical (TF-IDF) similarity
  semantic          similar meaning per local embeddings (not evidence of copying)
  internal          duplicated text inside this document
  quoted            the matching text is inside quotation marks
"""

from __future__ import annotations

import difflib
import math
import re
from collections import Counter, defaultdict

import numpy as np

from app.services.analysis.common import analyzable, make_finding
from app.services.plagiarism import corpus_index_service as corpus
from app.services.plagiarism.fingerprint_service import fingerprints, minhash, minhash_similarity, token_spans, tokens
from app.storage import db

MODULE = "similarity"
MIN_BLOCK = 6  # tokens
QUOTE_RE = re.compile(r"[\"“]([^\"”]{8,})[\"”]")
CITATION_RE = re.compile(r"\([A-Z][A-Za-z'’\-]+(?: et al\.?)?,? \d{4}[a-z]?[^)]*\)|\[\d+(?:[,–\-]\s*\d+)*\]|￼")


def _blocks(a: list[str], b: list[str]) -> list[tuple[int, int, int]]:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(m.a, m.b, m.size) for m in sm.get_matching_blocks() if m.size >= MIN_BLOCK]


def _classify(coverage: float, longest: int, total: int) -> str | None:
    if longest >= 20 or (coverage >= 0.8 and longest >= 12):
        return "exact"
    if coverage >= 0.5:
        return "near_verbatim"
    if longest >= MIN_BLOCK and (longest >= 8 or coverage >= 0.15):
        return "similar_phrasing"
    return None


def _quoted_ranges(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in QUOTE_RE.finditer(text)]


def _citation_status(text: str, start: int, end: int) -> str:
    # Look in the matching sentence and a short window after it.
    window = text[max(0, start - 20): min(len(text), end + 120)]
    return "citation_nearby" if CITATION_RE.search(window) else "no_citation_detected"


def _suggestions(category: str, quoted: bool, cited: str) -> list[str]:
    if quoted and cited == "citation_nearby":
        return ["This passage is quoted and appears to be cited. Verify the quotation is exact and the citation is complete."]
    out = ["Verify the source of this passage."]
    if cited != "citation_nearby":
        out.append("Add a proper citation if this content comes from the source.")
    if category in ("exact", "near_verbatim") and not quoted:
        out.append("If you use the wording directly, put it in quotation marks with a citation.")
    if category in ("near_verbatim", "similar_phrasing", "semantic"):
        out.append("Explain the idea in your own analysis and connect it to your argument, with a citation for the original idea.")
    return out


def compare_to_chunk(para: dict, chunk_text: str) -> dict | None:
    pspans = token_spans(para["text"])
    ptoks = [t for t, _, _ in pspans]
    ctoks = tokens(chunk_text)
    if len(ptoks) < MIN_BLOCK:
        return None
    blocks = _blocks(ptoks, ctoks)
    if not blocks:
        return None
    matched = sum(b[2] for b in blocks)
    longest = max(b[2] for b in blocks)
    coverage = matched / len(ptoks)
    category = _classify(coverage, longest, len(ptoks))
    if not category:
        return None
    a0 = min(b[0] for b in blocks)
    a1 = max(b[0] + b[2] for b in blocks)
    start, end = pspans[a0][1], pspans[a1 - 1][2]
    b0 = min(b[1] for b in blocks)
    b1 = max(b[1] + b[2] for b in blocks)
    # Map chunk tokens back to chunk text for a readable matching passage.
    cspans = token_spans(chunk_text)
    m_start, m_end = cspans[b0][1], cspans[b1 - 1][2]
    return {
        "category": category,
        "coverage": round(coverage, 3),
        "longest_run": longest,
        "matched_tokens": matched,
        "start": start,
        "end": end,
        "matching_passage": chunk_text[max(0, m_start - 40):m_end + 40].strip(),
        "overlap_spans": [[pspans[b[0]][1], pspans[b[0] + b[2] - 1][2]] for b in blocks],
    }


def _source_info(chunk_id: str) -> dict:
    r = db.query_one("SELECT s.id, s.title, s.kind, s.origin, s.url, s.meta_json, c.text FROM corpus_chunks c "
                     "JOIN corpus_sources s ON s.id=c.source_id WHERE c.id=?", (chunk_id,))
    if not r:
        return {}
    return {"source_id": r["id"], "title": r["title"], "kind": r["kind"], "origin": r["origin"], "url": r["url"],
            "meta": db.loads(r["meta_json"], {}), "text": r["text"]}


def _finding_for_match(para: dict, cmp: dict, src: dict, method: str, score: float | None) -> dict:
    quoted = any(qs <= cmp["start"] and cmp["end"] <= qe + 1 for qs, qe in _quoted_ranges(para["text"]))
    cited = _citation_status(para["text"], cmp["start"], cmp["end"])
    category = "quoted" if quoted else cmp["category"]
    labels = {
        "exact": "Exact match",
        "near_verbatim": "Near-verbatim match",
        "similar_phrasing": "Similar phrasing",
        "semantic": "Semantically similar",
        "quoted": "Quoted passage",
    }
    explanation = {
        "exact": f"{cmp['longest_run']} consecutive words match the source exactly.",
        "near_verbatim": f"{round(cmp['coverage'] * 100)}% of this passage's words appear in the same order in the source.",
        "similar_phrasing": f"Shares phrasing with the source (longest shared run: {cmp.get('longest_run', 0)} words).",
        "semantic": "Expresses a similar idea to the source according to local embeddings. Similar meaning is not evidence of copying.",
        "quoted": "Matching text is inside quotation marks.",
    }[category]
    prio = {"exact": "high", "near_verbatim": "high", "similar_phrasing": "medium", "semantic": "low", "quoted": "low"}[category]
    if category in ("exact", "near_verbatim") and cited == "citation_nearby":
        prio = "medium"
    return make_finding(MODULE, category, para, start=cmp["start"], end=cmp["end"], priority=prio, confidence=score,
                        source="corpus" if src.get("origin") != "external" else "external",
                        explanation=f"{labels[category]}: {explanation}",
                        data={
                            "label": labels[category],
                            "method": method,
                            "coverage": cmp.get("coverage"),
                            "longest_run": cmp.get("longest_run"),
                            "overlap_spans": cmp.get("overlap_spans", []),
                            "matching_passage": cmp.get("matching_passage"),
                            "source": {k: v for k, v in src.items() if k != "text"},
                            "citation_status": cited,
                            "quoted": quoted,
                            "recommendations": _suggestions(category, quoted, cited),
                        })


def analyze_against_corpus(paragraphs: list[dict], *, embed_model: str | None = None,
                           paragraph_embeddings: dict[str, np.ndarray] | None = None, on_progress=None) -> tuple[dict, list[dict]]:
    stats = corpus.corpus_stats()
    findings: list[dict] = []
    targets = [p for p in paragraphs if analyzable(p) and p["word_count"] >= 8]
    if stats["sources"] == 0:
        return {"checked": False, "reason": "The local reference corpus is empty. Import PDF, DOCX or TXT sources to compare against.",
                "corpus": stats}, []
    tfidf = corpus.load_tfidf_index()
    emb_ids, emb_mat = corpus.load_chunk_matrix(embed_model) if embed_model else ([], None)
    methods = ["fingerprint (winnowing)", "sequence alignment", "TF-IDF cosine"]
    if emb_mat is not None and paragraph_embeddings:
        methods.append(f"semantic embeddings ({embed_model})")

    for i, p in enumerate(targets):
        matched_chunks: set[str] = set()
        fps = fingerprints(p["text"])
        if fps:
            hashes = [h for h, _ in fps]
            counts: Counter = Counter()
            for j in range(0, len(hashes), 500):
                part = hashes[j:j + 500]
                q = f"SELECT chunk_id, COUNT(*) AS n FROM fingerprints WHERE hash IN ({','.join('?' * len(part))}) GROUP BY chunk_id"
                for r in db.query(q, part):
                    counts[r["chunk_id"]] += r["n"]
            for chunk_id, n in counts.most_common(5):
                if n < 1:
                    continue
                src = _source_info(chunk_id)
                cmp = compare_to_chunk(p, src["text"])
                if cmp:
                    matched_chunks.add(chunk_id)
                    findings.append(_finding_for_match(p, cmp, src, "fingerprint", round(cmp["coverage"], 2)))

        # Lexical similarity (TF-IDF) for paraphrase-level overlap without shared 5-grams.
        ptf = Counter(t for t in tokens(p["text"]) if t not in corpus.STOPWORDS and len(t) > 2)
        if ptf and tfidf["n"]:
            vec = {t: (1 + math.log(c)) * tfidf["idf"].get(t, 0) for t, c in ptf.items() if t in tfidf["idf"]}
            pnorm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            scores: dict[str, float] = defaultdict(float)
            for t, v in vec.items():
                for cid, cv in tfidf["inverted"].get(t, ()):
                    scores[cid] += v * cv
            best = sorted(((s / (pnorm * tfidf["norms"][cid]), cid) for cid, s in scores.items()), reverse=True)[:2]
            for score, cid in best:
                if score >= 0.55 and cid not in matched_chunks and len(ptf) >= 6:
                    src = _source_info(cid)
                    matched_chunks.add(cid)
                    cmp = {"category": "similar_phrasing", "coverage": None, "longest_run": 0, "start": 0, "end": len(p["text"]),
                           "matching_passage": src["text"][:300]}
                    f = _finding_for_match(p, cmp, src, "tfidf", round(score, 2))
                    f["explanation"] = (f"Similar vocabulary to the source (TF-IDF cosine {score:.2f}). "
                                        "Lexical similarity alone does not show copying; compare the passages.")
                    findings.append(f)

        # Semantic similarity (local embeddings).
        if emb_mat is not None and paragraph_embeddings and p["id"] in paragraph_embeddings:
            sims = emb_mat @ paragraph_embeddings[p["id"]]
            idx = int(np.argmax(sims))
            score = float(sims[idx])
            cid = emb_ids[idx]
            if score >= 0.86 and cid not in matched_chunks:
                src = _source_info(cid)
                cmp = {"category": "semantic", "start": 0, "end": len(p["text"]), "matching_passage": src["text"][:300]}
                findings.append(_finding_for_match(p, cmp, src, "embedding", round(score, 2)))
        if on_progress:
            on_progress(i + 1, len(targets))
    return {"checked": True, "corpus": stats, "methods": methods}, findings


def internal_duplication(paragraphs: list[dict]) -> list[dict]:
    targets = [p for p in paragraphs if analyzable(p) and p["word_count"] >= 12]
    sigs = {p["id"]: minhash(tokens(p["text"])) for p in targets}
    findings = []
    by_hash: dict[int, set[str]] = defaultdict(set)
    for p in targets:
        for h, _ in fingerprints(p["text"]):
            by_hash[h].add(p["id"])
    pairs: Counter = Counter()
    for ids in by_hash.values():
        ids = sorted(ids)
        if 1 < len(ids) <= 20:
            for a in range(len(ids)):
                for b in range(a + 1, len(ids)):
                    pairs[(ids[a], ids[b])] += 1
    pmap = {p["id"]: p for p in targets}
    for (a, b), n in pairs.items():
        if n < 2:
            continue
        pa, pb = pmap[a], pmap[b]
        cmp = compare_to_chunk(pb, pa["text"])
        if not cmp:
            continue
        mh = minhash_similarity(sigs[a], sigs[b])
        findings.append(make_finding(MODULE, "internal", pb, start=cmp["start"], end=cmp["end"], priority="medium", source="statistical",
                                     confidence=round(mh, 2),
                                     explanation=(f"This passage repeats text from an earlier paragraph ({cmp['longest_run']} consecutive "
                                                  f"words, {round(cmp['coverage'] * 100)}% of the paragraph). Remove the duplication or "
                                                  "refer back to the earlier point."),
                                     data={"label": "Internal duplication", "method": "fingerprint", "other_paragraph_id": a,
                                           "matching_passage": cmp["matching_passage"], "overlap_spans": cmp["overlap_spans"],
                                           "coverage": cmp["coverage"], "minhash_jaccard": round(mh, 2),
                                           "recommendations": ["Remove or consolidate the repeated passage."]}))
    return findings


async def embed_paragraphs(ollama, model: str, paragraphs: list[dict]) -> dict[str, np.ndarray]:
    targets = [p for p in paragraphs if analyzable(p) and p["word_count"] >= 8]
    out: dict[str, np.ndarray] = {}
    for i in range(0, len(targets), 16):
        batch = targets[i:i + 16]
        vecs = await ollama.embed(model, [p["text"].replace("￼", "") for p in batch])
        for p, v in zip(batch, vecs):
            arr = np.asarray(v, dtype=np.float32)
            out[p["id"]] = arr / (np.linalg.norm(arr) or 1.0)
    return out
