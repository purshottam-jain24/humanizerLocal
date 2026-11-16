"""Optional external source discovery (OpenAlex, Crossref).

OFF by default. Only runs when the user enables it in Settings → Privacy.
What leaves the device: a small number of short sentences from the document,
sent as search queries to the public OpenAlex and Crossref APIs. Results are
compared against titles and abstracts those APIs return. Full texts are not
fetched, paywalls are not bypassed, and this does not cover the whole web or
commercial plagiarism databases (e.g. Turnitin / iThenticate).
"""

from __future__ import annotations

import asyncio
import re

import httpx

from app.services.analysis.common import analyzable
from app.services.plagiarism.similarity_service import _finding_for_match, compare_to_chunk

PROVIDERS = {
    "openalex": "OpenAlex (api.openalex.org) — titles and abstracts of scholarly works",
    "crossref": "Crossref (api.crossref.org) — bibliographic metadata and some abstracts",
}
MAX_QUERIES = 12
USER_AGENT = "HumanWrite/1.0 (local document analysis; similarity research)"


def _abstract_from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    positions = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    return " ".join(w for _, w in sorted(positions))


def select_query_sentences(paragraphs: list[dict], limit: int = MAX_QUERIES) -> list[tuple[dict, str]]:
    cands = []
    for p in paragraphs:
        if not analyzable(p) or p["word_count"] < 15:
            continue
        for s in p["sentences"]:
            sent = p["text"][s["start"]:s["end"]].replace("￼", "").strip()
            n = len(sent.split())
            if 14 <= n <= 45:
                # Prefer distinctive sentences: many long words, few stock words.
                score = sum(1 for w in sent.split() if len(w) > 6)
                cands.append((score, p, sent))
    cands.sort(key=lambda x: -x[0])
    seen_par = set()
    out = []
    for _, p, s in cands:
        if p["id"] in seen_par:
            continue
        seen_par.add(p["id"])
        out.append((p, s))
        if len(out) >= limit:
            break
    return out


async def search(paragraphs: list[dict], providers: list[str], on_query=None) -> tuple[dict, list[dict]]:
    queries = select_query_sentences(paragraphs)
    findings: list[dict] = []
    log: list[dict] = []
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        for p, sent in queries:
            q = re.sub(r"[^\w\s'-]", " ", sent)[:300]
            for prov in providers:
                if prov not in PROVIDERS:
                    continue
                works = []
                try:
                    if prov == "openalex":
                        r = await client.get("https://api.openalex.org/works", params={"search": q, "per_page": 5,
                                             "select": "id,doi,title,display_name,publication_year,abstract_inverted_index,primary_location"})
                        r.raise_for_status()
                        for wk in r.json().get("results", []):
                            loc = wk.get("primary_location") or {}
                            works.append({"title": wk.get("display_name") or wk.get("title") or "Untitled",
                                          "url": wk.get("doi") or (loc.get("landing_page_url")) or wk.get("id"),
                                          "year": wk.get("publication_year"),
                                          "text": _abstract_from_inverted(wk.get("abstract_inverted_index"))})
                    else:
                        r = await client.get("https://api.crossref.org/works", params={"query.bibliographic": q, "rows": 5,
                                             "select": "DOI,title,abstract,issued,URL"})
                        r.raise_for_status()
                        for it in r.json().get("message", {}).get("items", []):
                            abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")
                            year = ((it.get("issued") or {}).get("date-parts") or [[None]])[0][0]
                            works.append({"title": (it.get("title") or ["Untitled"])[0], "url": it.get("URL"),
                                          "year": year, "text": abstract})
                    log.append({"provider": prov, "query_chars": len(q), "results": len(works), "ok": True})
                except Exception as e:  # noqa: BLE001
                    log.append({"provider": prov, "ok": False, "error": str(e)[:120]})
                    continue
                for wk in works:
                    text = f"{wk['title']}. {wk['text']}".strip()
                    if len(text.split()) < 8:
                        continue
                    cmp = compare_to_chunk(p, text)
                    if cmp and cmp["category"] in ("exact", "near_verbatim", "similar_phrasing") and cmp["longest_run"] >= 8:
                        src = {"title": wk["title"], "origin": "external", "kind": prov, "url": wk["url"],
                               "meta": {"year": wk["year"], "provider": PROVIDERS[prov]}}
                        findings.append(_finding_for_match(p, cmp, src, f"external:{prov}", round(cmp["coverage"], 2)))
                await asyncio.sleep(0.25)  # stay well within polite rate limits
            if on_query:
                on_query(len(log))
    return {
        "checked": True,
        "providers": [PROVIDERS[p] for p in providers if p in PROVIDERS],
        "queries_sent": len(queries),
        "log": log,
        "coverage_note": ("Compared against titles and abstracts returned by the listed public APIs only. This is not a "
                          "check of the entire internet and does not include commercial databases such as Turnitin or iThenticate."),
    }, findings
