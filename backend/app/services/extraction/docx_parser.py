"""DOCX → structured document representation.

Reads the OOXML package directly (no conversion, no reconstruction) and
produces a JSON-serialisable structure with stable identifiers. The parser
is a generator of *steps* so the processing pipeline can emit real progress
events while extraction happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from lxml import etree

from app.services.extraction.sentences import count_words, split_sentences, word_spans
from app.services.ooxml.package import DocxPackage, iter_paragraphs, iter_tables, stable_id
from app.services.ooxml.text_model import (
    A_NS, OBJECT_CHAR, R_NS, W_NS, WP_NS, paragraph_atoms, run_format, w,
)

CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCT_NS = "http://purl.org/dc/terms/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

REFERENCE_HEADINGS = re.compile(r"^\s*(references?|bibliography|works cited|literature cited|sources|reference list)\s*$", re.I)
WORDS_PER_PAGE_ESTIMATE = 450


@dataclass
class StyleInfo:
    id: str
    name: str
    type: str
    based_on: str | None
    outline_level: int | None
    has_numbering: bool
    num_id: str | None = None
    num_level: int = 0


@dataclass
class ExtractionResult:
    structure: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _val(el: etree._Element | None, attr: str = "val") -> str | None:
    if el is None:
        return None
    return el.get(w(attr))


def parse_styles(pkg: DocxPackage) -> dict[str, StyleInfo]:
    root = pkg.xml("word/styles.xml")
    styles: dict[str, StyleInfo] = {}
    if root is None:
        return styles
    for s in root.findall(w("style")):
        sid = s.get(w("styleId")) or ""
        name_el = s.find(w("name"))
        ppr = s.find(w("pPr"))
        ol = None
        has_num = False
        num_id = None
        num_level = 0
        if ppr is not None:
            ol_el = ppr.find(w("outlineLvl"))
            if ol_el is not None and _val(ol_el) is not None:
                try:
                    ol = int(_val(ol_el))
                except ValueError:
                    ol = None
            npr = ppr.find(w("numPr"))
            has_num = npr is not None
            if npr is not None:
                num_id = _val(npr.find(w("numId")))
                lv = _val(npr.find(w("ilvl")))
                num_level = int(lv) if lv and lv.isdigit() else 0
        styles[sid] = StyleInfo(
            id=sid,
            name=_val(name_el) or sid,
            type=s.get(w("type")) or "paragraph",
            based_on=_val(s.find(w("basedOn"))),
            outline_level=ol,
            has_numbering=has_num,
            num_id=num_id,
            num_level=num_level,
        )
    return styles


def _resolve_style(styles: dict[str, StyleInfo], sid: str | None) -> tuple[str | None, int | None, tuple[str, int] | None]:
    """Return (style name, heading level, style numbering (numId, ilvl)) walking basedOn."""
    seen = set()
    name = None
    level = None
    has_num = None
    cur = sid
    while cur and cur not in seen and cur in styles:
        seen.add(cur)
        st = styles[cur]
        if name is None:
            name = st.name
        nm = st.name.lower()
        m = re.match(r"heading\s*(\d)", nm)
        if level is None and m:
            level = int(m.group(1))
        if level is None and nm == "title":
            level = 0
        if level is None and st.outline_level is not None and st.outline_level < 9:
            level = st.outline_level + 1
        if has_num is None and st.has_numbering and st.num_id:
            has_num = (st.num_id, st.num_level)
        cur = st.based_on
    return name, level, has_num


def parse_numbering(pkg: DocxPackage) -> dict[tuple[str, int], str]:
    """(numId, ilvl) -> numFmt."""
    root = pkg.xml("word/numbering.xml")
    out: dict[tuple[str, int], str] = {}
    if root is None:
        return out
    abstract: dict[str, dict[int, str]] = {}
    for an in root.findall(w("abstractNum")):
        aid = an.get(w("abstractNumId"))
        levels = {}
        for lvl in an.findall(w("lvl")):
            try:
                il = int(lvl.get(w("ilvl")) or 0)
            except ValueError:
                il = 0
            levels[il] = _val(lvl.find(w("numFmt"))) or "decimal"
        abstract[aid] = levels
    for num in root.findall(w("num")):
        nid = num.get(w("numId"))
        aid = _val(num.find(w("abstractNumId")))
        for il, fmt in abstract.get(aid, {}).items():
            out[(nid, il)] = fmt
    return out


def parse_metadata(pkg: DocxPackage) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    core = pkg.xml("docProps/core.xml")
    if core is not None:
        for key, tag in (("title", f"{{{DC_NS}}}title"), ("subject", f"{{{DC_NS}}}subject"),
                         ("creator", f"{{{DC_NS}}}creator"), ("last_modified_by", f"{{{CP_NS}}}lastModifiedBy"),
                         ("created", f"{{{DCT_NS}}}created"), ("modified", f"{{{DCT_NS}}}modified"),
                         ("revision", f"{{{CP_NS}}}revision")):
            el = core.find(tag)
            if el is not None and el.text:
                meta[key] = el.text.strip()
    app = pkg.xml("docProps/app.xml")
    if app is not None:
        for key, tag in (("pages", "Pages"), ("words", "Words"), ("application", "Application")):
            el = app.find(f"{{{EP_NS}}}{tag}")
            if el is not None and el.text:
                meta[f"app_{key}"] = int(el.text) if el.text.isdigit() else el.text
    return meta


def parse_section(sect: etree._Element, idx: int) -> dict[str, Any]:
    pg = sect.find(w("pgSz"))
    mar = sect.find(w("pgMar"))
    cols = sect.find(w("cols"))

    def tw(el, attr):
        v = el.get(w(attr)) if el is not None else None
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    width, height = tw(pg, "w"), tw(pg, "h")
    orient = (pg.get(w("orient")) if pg is not None else None) or ("landscape" if width and height and width > height else "portrait")
    return {
        "index": idx,
        "id": f"s{idx}",
        "type": _val(sect.find(w("type"))) or "nextPage",
        "page_width_twips": width,
        "page_height_twips": height,
        "orientation": orient,
        "margins_twips": {k: tw(mar, k) for k in ("top", "right", "bottom", "left", "header", "footer")} if mar is not None else None,
        "columns": tw(cols, "num") or 1,
        "header_refs": [r.get(f"{{{R_NS}}}id") for r in sect.findall(w("headerReference"))],
        "footer_refs": [r.get(f"{{{R_NS}}}id") for r in sect.findall(w("footerReference"))],
    }


def extract_document(pkg: DocxPackage, salt: str = "", on_paragraph: Callable[[dict], None] | None = None,
                     on_stage: Callable[[str, dict], None] | None = None) -> ExtractionResult:
    warnings: list[str] = []
    styles = parse_styles(pkg)
    numbering = parse_numbering(pkg)
    metadata = parse_metadata(pkg)
    rels = {r["id"]: r for r in pkg.relationships(pkg.main_part)}

    paragraphs: list[dict] = []
    tables: list[dict] = []
    images: list[dict] = []
    hyperlinks: list[dict] = []
    sections: list[dict] = []
    citations: list[dict] = []
    textbox_count = 0
    parts_summary: list[dict] = []
    rendered_breaks_seen = False

    if on_stage:
        on_stage("structure", {"styles": len(styles), "parts": [p.name for p in pkg.text_parts()]})

    for part in pkg.text_parts():
        root = pkg.xml(part.name)
        if root is None:
            continue
        part_par_count = 0

        for tid, tbl, depth in iter_tables(root, part, salt):
            rows = tbl.findall(w("tr"))
            max_cols = max((len(r.findall(w("tc"))) for r in rows), default=0)
            tables.append({"id": tid, "part": part.kind, "rows": len(rows), "cols": max_cols, "nested_depth": depth,
                           "paragraph_ids": []})
        table_index = {t["id"]: t for t in tables}

        if part.kind == "body":
            sect_idx = 0
            for sp in root.iter(w("sectPr")):
                sections.append(parse_section(sp, sect_idx))
                sect_idx += 1

        for loc in iter_paragraphs(root, part, salt):
            if loc.in_textbox:
                textbox_count += 1
                continue
            p = loc.element
            pa = paragraph_atoms(p)
            text = pa.text
            ppr = p.find(w("pPr"))
            style_id = _val(ppr.find(w("pStyle"))) if ppr is not None else None
            style_name, heading_level, style_num = _resolve_style(styles, style_id)
            if heading_level is None and ppr is not None:
                ol = ppr.find(w("outlineLvl"))
                if ol is not None and (_val(ol) or "").isdigit() and int(_val(ol)) < 9:
                    heading_level = int(_val(ol)) + 1

            num_pr = ppr.find(w("numPr")) if ppr is not None else None
            list_info = None
            if num_pr is not None or style_num:
                nid = _val(num_pr.find(w("numId"))) if num_pr is not None else None
                ilvl_raw = _val(num_pr.find(w("ilvl"))) if num_pr is not None else None
                ilvl = int(ilvl_raw) if ilvl_raw and ilvl_raw.isdigit() else None
                if style_num:
                    nid = nid or style_num[0]
                    ilvl = style_num[1] if ilvl is None else ilvl
                ilvl = ilvl or 0
                if nid != "0":
                    list_info = {"num_id": nid, "level": ilvl, "format": numbering.get((nid, ilvl), "bullet" if nid is None else "decimal")}

            page_break_before = bool(ppr is not None and ppr.find(w("pageBreakBefore")) is not None
                                     and _val(ppr.find(w("pageBreakBefore"))) not in ("0", "false"))
            if pa.rendered_page_breaks:
                rendered_breaks_seen = True
            sect_in_ppr = ppr is not None and ppr.find(w("sectPr")) is not None

            # Formatting spans for display (merged per contiguous run).
            fmt_spans: list[dict] = []
            objects: list[dict] = []
            locked: list[list[int]] = []
            prev_run = None
            for i, a in enumerate(pa.atoms):
                if a.kind == "object":
                    objects.append({"offset": i, "kind": a.object_kind})
                if not a.editable:
                    if locked and locked[-1][1] == i:
                        locked[-1][1] = i + 1
                    else:
                        locked.append([i, i + 1])
                if a.run is not prev_run:
                    f = run_format(a.run)
                    if fmt_spans and fmt_spans[-1]["f"] == f:
                        fmt_spans[-1]["e"] = i + 1
                    else:
                        fmt_spans.append({"s": i, "e": i + 1, "f": f})
                    prev_run = a.run
                else:
                    fmt_spans[-1]["e"] = i + 1
            fmt_spans = [s for s in fmt_spans if s["f"]]

            # Images inside the paragraph.
            for dr in p.iter(w("drawing"), w("pict")):
                blip = None
                for b in dr.iter(f"{{{A_NS}}}blip"):
                    blip = b.get(f"{{{R_NS}}}embed") or b.get(f"{{{R_NS}}}link")
                    break
                ext = next(dr.iter(f"{{{WP_NS}}}extent"), None)
                docpr = next(dr.iter(f"{{{WP_NS}}}docPr"), None)
                rel = rels.get(blip) if blip and part.kind == "body" else None
                images.append({
                    "id": stable_id("i", loc.id, str(len(images))),
                    "paragraph_id": loc.id,
                    "part": part.kind,
                    "rid": blip,
                    "target": rel["target"] if rel else None,
                    "anchored": next(dr.iter(f"{{{WP_NS}}}anchor"), None) is not None,
                    "description": (docpr.get("descr") or docpr.get("name")) if docpr is not None else None,
                    "cx": int(ext.get("cx")) if ext is not None and (ext.get("cx") or "").isdigit() else None,
                    "cy": int(ext.get("cy")) if ext is not None and (ext.get("cy") or "").isdigit() else None,
                })

            for h in pa.hyperlinks:
                rel = rels.get(h["rid"]) if h["rid"] and part.kind == "body" else None
                hyperlinks.append({
                    "paragraph_id": loc.id,
                    "start": h["start"], "end": h["end"],
                    "text": text[h["start"]:h["end"]],
                    "url": rel["target"] if rel and rel["external"] else None,
                    "anchor": h["anchor"],
                })

            # Field-based citations (Word, Zotero, Mendeley, EndNote).
            instr = " ".join((t.text or "") for t in p.iter(w("instrText")))
            instr += " ".join((fs.get(w("instr")) or "") for fs in p.iter(w("fldSimple")))
            has_sdt_citation = any(True for _ in p.iter(w("citation")))
            if re.search(r"\b(CITATION|ADDIN\s+(ZOTERO|CSL_CITATION|EN\.CITE|Mendeley))", instr) or has_sdt_citation:
                citations.append({"paragraph_id": loc.id, "kind": "field", "instruction": instr.strip()[:120]})

            sentences = []
            for si, (s, e) in enumerate(split_sentences(text)):
                sentences.append({"id": f"{loc.id}.s{si}", "start": s, "end": e})

            alignment = _val(ppr.find(w("jc"))) if ppr is not None else None
            stripped = text.replace(OBJECT_CHAR, "").strip()
            kind = "paragraph"
            if not stripped:
                kind = "empty" if not objects else "object"
            elif heading_level is not None:
                kind = "title" if heading_level == 0 else "heading"
            elif list_info:
                kind = "list_item"
            elif style_name and style_name.lower() in ("caption",):
                kind = "caption"
            elif style_name and style_name.lower().startswith("toc"):
                kind = "toc"

            probable_heading = False
            if kind == "paragraph" and part.kind == "body" and not loc.table_path:
                wc = count_words(stripped)
                fmt_all_bold = bool(fmt_spans) and all(s["f"].get("b") for s in fmt_spans) and \
                    sum(s["e"] - s["s"] for s in fmt_spans) >= len(text.rstrip()) * 0.9
                if 0 < wc <= 12 and fmt_all_bold and not stripped.endswith((".", ",", ";", ":")):
                    probable_heading = True

            para = {
                "id": loc.id,
                "part": part.kind,
                "part_name": part.name,
                "note_id": loc.note_id,
                "kind": kind,
                "probable_heading": probable_heading,
                "style_id": style_id,
                "style_name": style_name,
                "heading_level": heading_level,
                "list": list_info,
                "alignment": alignment,
                "table": [{"table_id": t, "row": r, "col": c} for (t, r, c) in loc.table_path] or None,
                "text": text,
                "sentences": sentences,
                "word_count": count_words(stripped),
                "char_count": len(stripped),
                "formatting": fmt_spans,
                "objects": objects,
                "locked_ranges": locked,
                "has_field": pa.has_field,
                "has_math": pa.has_math,
                "page_break_before": page_break_before or bool(pa.page_breaks_before_offset and pa.page_breaks_before_offset[0] == 0),
                "inline_page_breaks": [o for o in pa.page_breaks_before_offset if o > 0],
                "rendered_page_breaks": pa.rendered_page_breaks,
                "section_break_after": sect_in_ppr,
                "order": len(paragraphs),
            }
            for tp in para["table"] or []:
                if tp["table_id"] in table_index:
                    table_index[tp["table_id"]]["paragraph_ids"].append(loc.id)
            paragraphs.append(para)
            part_par_count += 1
            if on_paragraph:
                on_paragraph(para)
        parts_summary.append({"name": part.name, "kind": part.kind, "paragraphs": part_par_count})

    # ---- Page assignment (body only) -------------------------------------
    page_source = "rendered" if rendered_breaks_seen else "estimated"
    page = 1
    words_on_page = 0
    for para in paragraphs:
        if para["part"] != "body":
            para["page"] = None
            continue
        if para["page_break_before"] and words_on_page > 0:
            page += 1
            words_on_page = 0
        if page_source == "rendered":
            # A rendered break at offset 0 means the paragraph starts a new page.
            if para["rendered_page_breaks"] and para["rendered_page_breaks"][0] == 0 and words_on_page > 0 and not para["page_break_before"]:
                page += 1
                words_on_page = 0
            para["page"] = page
            mid = [o for o in para["rendered_page_breaks"] if o > 0]
            page += len(mid)
        else:
            if words_on_page + para["word_count"] > WORDS_PER_PAGE_ESTIMATE and words_on_page > 0:
                page += 1
                words_on_page = 0
            para["page"] = page
            page += len(para["inline_page_breaks"])
            if para["inline_page_breaks"]:
                words_on_page = 0
        words_on_page += para["word_count"]
        if para["section_break_after"]:
            page += 1
            words_on_page = 0
    body_pages = max((p["page"] or 1 for p in paragraphs if p["part"] == "body"), default=1)
    # A trailing section break inflates the count by one.
    if paragraphs and paragraphs[-1].get("section_break_after"):
        body_pages = max(1, body_pages)

    # ---- References section detection -------------------------------------
    references: list[dict] = []
    in_refs_level = None
    for para in paragraphs:
        if para["part"] != "body":
            continue
        is_heading = para["kind"] in ("heading", "title") or para["probable_heading"]
        txt = para["text"].strip()
        if is_heading and REFERENCE_HEADINGS.match(re.sub(r"^[\d.\s]+", "", txt)):
            in_refs_level = para["heading_level"] or 1
            para["is_reference_heading"] = True
            continue
        if in_refs_level is not None:
            if is_heading and (para["heading_level"] or 1) <= in_refs_level:
                in_refs_level = None
                continue
            if para["word_count"] >= 3:
                para["is_reference_entry"] = True
                references.append({"paragraph_id": para["id"], "text": txt})

    if textbox_count:
        warnings.append(f"{textbox_count} text-box paragraph(s) are shown in the preview but are not analysed or edited.")
    if any(p["has_math"] for p in paragraphs):
        warnings.append("Equations are preserved but locked; they are excluded from analysis and edits.")

    body = [p for p in paragraphs if p["part"] == "body"]
    words_total = sum(p["word_count"] for p in body)
    summary = {
        "pages": body_pages,
        "page_source": page_source,  # rendered = Word's last layout markers; estimated = breaks + ~450 words/page
        "word_metadata_pages": metadata.get("app_pages"),
        "paragraphs": sum(1 for p in body if p["kind"] not in ("empty", "object")),
        "paragraphs_total": len(paragraphs),
        "words": words_total,
        "headings": sum(1 for p in body if p["kind"] in ("heading", "title")),
        "probable_headings": sum(1 for p in body if p["probable_heading"]),
        "list_items": sum(1 for p in body if p["kind"] == "list_item"),
        "tables": len(tables),
        "nested_tables": sum(1 for t in tables if t["nested_depth"] > 0),
        "images": len(images),
        "hyperlinks": len(hyperlinks),
        "sections": max(1, len(sections)),
        "headers": sum(1 for pt in parts_summary if pt["kind"] == "header"),
        "footers": sum(1 for pt in parts_summary if pt["kind"] == "footer"),
        "footnotes": len({p["note_id"] for p in paragraphs if p["part"] == "footnotes"}),
        "endnotes": len({p["note_id"] for p in paragraphs if p["part"] == "endnotes"}),
        "references": len(references),
        "field_citations": len(citations),
        "textboxes": textbox_count,
    }
    structure = {
        "metadata": metadata,
        "summary": summary,
        "parts": parts_summary,
        "sections": sections,
        "paragraphs": paragraphs,
        "tables": tables,
        "images": images,
        "hyperlinks": hyperlinks,
        "references": references,
        "citations": citations,
        "styles_used": sorted({p["style_name"] for p in paragraphs if p["style_name"]}),
        "warnings": warnings,
    }
    return ExtractionResult(structure, warnings)


def paragraph_words(para: dict) -> list[list[int]]:
    """Word spans used by the recognition visualisation."""
    return [[s, e] for s, e in word_spans(para["text"])]
