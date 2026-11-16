"""Academic completeness: citations, references, unsupported claims, structure.

Findings that would need new facts or sources are always marked
``needs_verification`` — HumanWrite never invents references or evidence.
"""

from __future__ import annotations

import re
from collections import Counter

from app.services.analysis.common import analyzable, make_finding

MODULE_ACADEMIC = "academic"
MODULE_FORMAT = "formatting"

AUTHOR_YEAR = re.compile(r"\(([A-Z][A-Za-z'’\-]+(?: et al\.?| and [A-Z][A-Za-z'’\-]+| & [A-Z][A-Za-z'’\-]+)?),? (\d{4}[a-z]?)(?:, (?:p|pp)\. ?[\d\-–]+)?\)")
NARRATIVE = re.compile(r"\b([A-Z][A-Za-z'’\-]+(?: et al\.?)?) \((\d{4}[a-z]?)\)")
NUMERIC = re.compile(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")
CLAIM_PATTERNS = [
    (r"\bstudies (have )?show(n|s)?\b", "Refers to studies without citing them."),
    (r"\bresearch (has )?(shown|shows|suggests|indicates|demonstrates)\b", "Refers to research without a citation."),
    (r"\b(it is|it's) (well|widely|generally|commonly) (known|established|accepted|recognized|recognised)\b", "Presents a claim as common knowledge; consider citing a source."),
    (r"\b(experts|scientists|scholars|researchers) (agree|believe|argue|say)\b", "Attributes a view to unnamed experts."),
    (r"\b(\d{1,3}(\.\d+)?\s?%|\d+ percent)\b", "Statistic without an adjacent citation."),
    (r"\b(always|never|everyone|no one|all people|proves?)\b", "Absolute claim; consider qualifying it or supporting it with evidence."),
]
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b")


def _has_citation(text: str) -> bool:
    return bool(AUTHOR_YEAR.search(text) or NARRATIVE.search(text) or NUMERIC.search(text) or "￼" in text)


def analyze(structure: dict) -> tuple[dict, list[dict], list[dict]]:
    paragraphs = structure["paragraphs"]
    body = [p for p in paragraphs if p["part"] == "body"]
    academic: list[dict] = []
    formatting: list[dict] = []

    # --- Citation inventory ---------------------------------------------------
    styles = Counter()
    in_text: list[dict] = []
    numeric_refs: set[int] = set()
    for p in body:
        if p.get("is_reference_entry"):
            continue
        for m in AUTHOR_YEAR.finditer(p["text"]):
            styles["author-date"] += 1
            in_text.append({"paragraph_id": p["id"], "author": m.group(1), "year": m.group(2), "style": "author-date"})
        for m in NARRATIVE.finditer(p["text"]):
            styles["author-date"] += 1
            in_text.append({"paragraph_id": p["id"], "author": m.group(1), "year": m.group(2), "style": "narrative"})
        for m in NUMERIC.finditer(p["text"]):
            styles["numeric"] += 1
            for part in re.split(r"\s*,\s*", m.group(1)):
                rng = re.split(r"\s*[–\-]\s*", part)
                try:
                    if len(rng) == 2:
                        numeric_refs.update(range(int(rng[0]), int(rng[1]) + 1))
                    else:
                        numeric_refs.add(int(rng[0]))
                except ValueError:
                    pass
    footnote_refs = sum(1 for p in body for o in p["objects"] if o["kind"] == "footnote_ref")
    if footnote_refs:
        styles["footnotes"] += footnote_refs
    field_cites = len(structure.get("citations", []))
    if field_cites:
        styles["reference-manager fields"] += field_cites

    references = structure.get("references", [])
    ref_texts = [r["text"] for r in references]

    if len([s for s in styles if s in ("author-date", "numeric")]) > 1:
        academic.append(make_finding(MODULE_ACADEMIC, "citation_style", None, priority="medium",
                                     explanation=f"Mixed citation styles: {dict(styles)}. Use one style consistently.",
                                     data={"styles": dict(styles)}))

    # Author-year citations without a matching reference entry.
    if references:
        ref_blob = "\n".join(ref_texts).lower()
        missing = []
        for c in in_text:
            author = c["author"].split()[0].lower().rstrip(",")
            if author not in ref_blob or c["year"][:4] not in ref_blob:
                missing.append(c)
        seen = set()
        for c in missing:
            key = (c["author"], c["year"])
            if key in seen:
                continue
            seen.add(key)
            p = next(x for x in body if x["id"] == c["paragraph_id"])
            idx = p["text"].find(c["author"])
            academic.append(make_finding(MODULE_ACADEMIC, "missing_reference", p, start=max(0, idx), end=max(0, idx) + len(c["author"]),
                                         priority="medium",
                                         explanation=f"In-text citation {c['author']} ({c['year']}) has no matching entry in the reference list.",
                                         data={"needs_verification": True}))
        # Reference entries never cited.
        cited_authors = {c["author"].split()[0].lower().rstrip(",") for c in in_text}
        if in_text:
            for r in references:
                first = re.match(r"\s*([A-Z][A-Za-z'’\-]+)", r["text"])
                if first and first.group(1).lower() not in cited_authors and not numeric_refs:
                    p = next(x for x in body if x["id"] == r["paragraph_id"])
                    academic.append(make_finding(MODULE_ACADEMIC, "uncited_reference", p, start=0, end=len(first.group(1)),
                                                 priority="low",
                                                 explanation=f"Reference “{r['text'][:60]}…” does not appear to be cited in the text."))
        for r in references:
            if not YEAR_RE.search(r["text"]) and "n.d." not in r["text"]:
                p = next(x for x in body if x["id"] == r["paragraph_id"])
                academic.append(make_finding(MODULE_ACADEMIC, "reference_format", p, start=0, end=len(p["text"]), passage=p["text"][:120],
                                             priority="low", explanation="Reference entry has no publication year (or “n.d.”).",
                                             data={"needs_verification": True}))
        if numeric_refs and max(numeric_refs) > len(references):
            academic.append(make_finding(MODULE_ACADEMIC, "missing_reference", None, priority="medium",
                                         explanation=f"Numeric citations go up to [{max(numeric_refs)}] but the reference list has {len(references)} entries.",
                                         data={"needs_verification": True}))
    elif in_text or numeric_refs:
        academic.append(make_finding(MODULE_ACADEMIC, "missing_reference_list", None, priority="medium",
                                     explanation="The text contains citations but no reference list (References / Bibliography heading) was found."))

    # --- Unsupported claims --------------------------------------------------
    for p in body:
        if not analyzable(p):
            continue
        for s in p["sentences"]:
            sent = p["text"][s["start"]:s["end"]]
            if _has_citation(sent):
                continue
            for pat, why in CLAIM_PATTERNS:
                m = re.search(pat, sent, re.I)
                if m:
                    if why.startswith("Absolute") and len(sent.split()) < 8:
                        continue
                    academic.append(make_finding(MODULE_ACADEMIC, "unsupported_claim", p, start=s["start"] + m.start(), end=s["start"] + m.end(),
                                                 priority="medium" if "cit" in why else "low",
                                                 explanation=why + " Any added source must be verified — HumanWrite does not invent references.",
                                                 data={"needs_verification": True}))
                    break

    # --- Undefined acronyms ---------------------------------------------------
    defined = set()
    flagged = set()
    for p in body:
        if not analyzable(p, include_headings=False):
            continue
        for m in re.finditer(r"\(([A-Z]{2,6}s?)\)", p["text"]):
            defined.add(m.group(1).rstrip("s"))
        for m in re.finditer(r"\b([A-Z]{2,6})s?\b", p["text"]):
            ac = m.group(1)
            if ac in defined or ac in flagged or ac in COMMON_ACRONYMS:
                continue
            flagged.add(ac)
            academic.append(make_finding(MODULE_ACADEMIC, "undefined_term", p, start=m.start(1), end=m.end(1), priority="low",
                                         explanation=f"The abbreviation “{ac}” is used before (or without) being defined. Spell it out on first use."))

    # --- Formatting & structure -------------------------------------------------
    prev_level = 0
    for p in body:
        if p["kind"] in ("heading", "title") and p["heading_level"]:
            lvl = p["heading_level"]
            if prev_level and lvl > prev_level + 1:
                formatting.append(make_finding(MODULE_FORMAT, "heading_hierarchy", p, start=0, end=len(p["text"]), priority="medium",
                                               explanation=f"Heading level jumps from {prev_level} to {lvl}. Skipped levels break the document outline and navigation."))
            prev_level = lvl
            if p["text"].rstrip().endswith("."):
                formatting.append(make_finding(MODULE_FORMAT, "heading_punctuation", p, start=len(p["text"].rstrip()) - 1,
                                               end=len(p["text"].rstrip()), priority="low",
                                               explanation="Headings usually do not end with a full stop.", correction=""))
        if p["probable_heading"]:
            formatting.append(make_finding(MODULE_FORMAT, "manual_heading", p, start=0, end=len(p["text"]), priority="low",
                                           explanation="This looks like a heading formatted manually (bold body text). Applying a Heading style makes it appear in the outline and table of contents."))
        if p["kind"] == "paragraph" and re.match(r"^\s*(\d+[.)]|[•\-–*])\s+\w", p["text"]) and not p["table"]:
            formatting.append(make_finding(MODULE_FORMAT, "manual_list", p, start=0, end=min(len(p["text"]), 4), priority="low",
                                           explanation="Manually typed numbering or bullet. Word's list formatting keeps numbering consistent."))
    run = []
    for p in body:
        if p["kind"] == "empty" and not p["table"]:
            run.append(p)
        else:
            if len(run) >= 3:
                formatting.append(make_finding(MODULE_FORMAT, "empty_paragraphs", run[0], start=0, end=0, passage="",
                                               priority="low",
                                               explanation=f"{len(run)} consecutive empty paragraphs are used for spacing. Paragraph spacing or page breaks are more reliable."))
            run = []
    if not any(p["kind"] in ("heading", "title") for p in body) and sum(p["word_count"] for p in body) > 800:
        formatting.append(make_finding(MODULE_FORMAT, "no_headings", None, priority="low",
                                       explanation="A long document without heading styles. Headings make the structure navigable."))

    summary = {
        "citation_styles": dict(styles),
        "in_text_citations": len(in_text) + sum(1 for _ in numeric_refs),
        "reference_entries": len(references),
        "footnote_citations": footnote_refs,
        "field_citations": field_cites,
        "unsupported_claims": sum(1 for f in academic if f["category"] == "unsupported_claim"),
    }
    return summary, academic, formatting


COMMON_ACRONYMS = {
    "USA", "UK", "EU", "UN", "US", "PDF", "DOCX", "HTML", "URL", "AI", "PhD", "CEO", "GDP", "COVID", "DNA", "RNA", "NASA",
    "WHO", "OK", "TV", "PC", "IT", "ID", "API", "UI", "CPU", "GPU", "RAM", "FAQ", "NB", "AM", "PM", "BC", "AD", "II", "III",
    "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "ISBN", "DOI", "ISO", "MS", "MA", "BA", "BSC", "MSC",
}
