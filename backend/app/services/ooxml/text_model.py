"""Character-level text model over WordprocessingML paragraphs.

Every visible character of a paragraph is an *atom* pointing back at the
XML element that produced it. Extraction reads atoms to produce text, and
the export engine edits atoms in place so that run formatting, hyperlinks,
fields and embedded objects survive.

Atom kinds:
  text    – a character of a <w:t> element (editable)
  tab     – <w:tab/> inside a run (editable: may be removed)
  break   – <w:br/> (non-page) or <w:cr/> (editable: may be removed)
  object  – footnote/endnote reference, drawing, symbol, math … (locked)
  field   – a character of a complex/simple field result (locked)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"

OBJECT_CHAR = "￼"


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


# Containers whose children are walked as if they were inline paragraph content.
TRANSPARENT = {
    w("hyperlink"), w("ins"), w("smartTag"), w("customXml"), w("sdtContent"),
    w("moveTo"), w("dir"), w("bdo"),
}
SKIPPED = {w("del"), w("moveFrom"), w("pPr"), w("rPr"), w("proofErr"), w("bookmarkStart"), w("bookmarkEnd"),
           w("commentRangeStart"), w("commentRangeEnd"), w("permStart"), w("permEnd"), w("sdtPr"), w("sdtEndPr")}
OBJECT_RUN_CHILDREN = {
    w("footnoteReference"): "footnote_ref",
    w("endnoteReference"): "endnote_ref",
    w("drawing"): "image",
    w("pict"): "image",
    w("object"): "embedded_object",
    w("sym"): "symbol",
    w("commentReference"): "comment_ref",
    w("ptab"): "tab",
    w("separator"): "separator",
    w("continuationSeparator"): "separator",
    w("footnoteRef"): "footnote_mark",
    w("endnoteRef"): "endnote_mark",
    w("pgNum"): "page_number",
    w("fldChar"): None,  # handled separately
    w("instrText"): None,
}


@dataclass
class Atom:
    kind: str  # text | tab | break | object | field
    char: str
    element: etree._Element  # w:t / w:tab / w:br / object element
    run: etree._Element | None
    offset: int = 0  # index within w:t text for kind == text
    object_kind: str | None = None

    @property
    def editable(self) -> bool:
        return self.kind in ("text", "tab", "break")


@dataclass
class ParagraphAtoms:
    atoms: list[Atom]
    page_breaks_before_offset: list[int]  # explicit page breaks (w:br type=page) by char offset
    rendered_page_breaks: list[int]  # w:lastRenderedPageBreak by char offset
    has_field: bool
    has_math: bool
    hyperlinks: list[dict]

    @property
    def text(self) -> str:
        return "".join(a.char for a in self.atoms)


def _iter_children(el: etree._Element) -> Iterator[etree._Element]:
    for child in el:
        if not isinstance(child.tag, str):
            continue  # comments / processing instructions
        yield child


def paragraph_atoms(p: etree._Element) -> ParagraphAtoms:
    atoms: list[Atom] = []
    page_breaks: list[int] = []
    rendered: list[int] = []
    hyperlinks: list[dict] = []
    state = {"field_depth": 0, "in_result": [], "has_field": False, "has_math": False}

    def emit_run(r: etree._Element, field_locked: bool) -> None:
        for c in _iter_children(r):
            tag = c.tag
            locked = field_locked or bool(state["in_result"] and state["in_result"][-1])
            if tag == w("t"):
                txt = c.text or ""
                for i, ch in enumerate(txt):
                    atoms.append(Atom("field" if locked else "text", ch, c, r, i))
            elif tag == w("tab"):
                atoms.append(Atom("field" if locked else "tab", "\t", c, r))
            elif tag in (w("br"), w("cr")):
                btype = c.get(w("type"))
                if btype == "page":
                    page_breaks.append(len(atoms))
                elif btype == "column":
                    pass
                else:
                    atoms.append(Atom("field" if locked else "break", "\n", c, r))
            elif tag == w("noBreakHyphen"):
                atoms.append(Atom("object", "‑", c, r, object_kind="hyphen"))
            elif tag == w("softHyphen"):
                continue
            elif tag == w("lastRenderedPageBreak"):
                rendered.append(len(atoms))
            elif tag == w("fldChar"):
                ftype = c.get(w("fldCharType"))
                state["has_field"] = True
                if ftype == "begin":
                    state["in_result"].append(False)
                elif ftype == "separate" and state["in_result"]:
                    state["in_result"][-1] = True
                elif ftype == "end" and state["in_result"]:
                    state["in_result"].pop()
            elif tag in (w("instrText"), w("delText"), w("delInstrText"), w("rPr"), w("annotationRef"), w("lastRenderedPageBreak")):
                continue
            elif tag == f"{{{MC_NS}}}AlternateContent":
                atoms.append(Atom("object", OBJECT_CHAR, c, r, object_kind="image"))
            elif tag in OBJECT_RUN_CHILDREN and OBJECT_RUN_CHILDREN[tag]:
                kind = OBJECT_RUN_CHILDREN[tag]
                if kind == "footnote_mark" or kind == "endnote_mark":
                    # The number shown at the start of a footnote body.
                    atoms.append(Atom("object", OBJECT_CHAR, c, r, object_kind=kind))
                else:
                    atoms.append(Atom("object", OBJECT_CHAR, c, r, object_kind=kind))
            # Unknown run content is ignored for text purposes but left untouched in XML.

    def walk(el: etree._Element, field_locked: bool = False) -> None:
        for c in _iter_children(el):
            tag = c.tag
            if tag in SKIPPED:
                continue
            if tag == w("r"):
                emit_run(c, field_locked)
            elif tag == w("hyperlink"):
                start = len(atoms)
                walk(c, field_locked)
                hyperlinks.append({
                    "start": start,
                    "end": len(atoms),
                    "rid": c.get(f"{{{R_NS}}}id"),
                    "anchor": c.get(w("anchor")),
                })
            elif tag in TRANSPARENT:
                walk(c, field_locked)
            elif tag == w("sdt"):
                content = c.find(w("sdtContent"))
                if content is not None:
                    walk(content, field_locked)
            elif tag == w("fldSimple"):
                state["has_field"] = True
                walk(c, True)
            elif tag in (f"{{{M_NS}}}oMath", f"{{{M_NS}}}oMathPara"):
                state["has_math"] = True
                atoms.append(Atom("object", OBJECT_CHAR, c, None, object_kind="math"))
            elif tag == f"{{{MC_NS}}}AlternateContent":
                atoms.append(Atom("object", OBJECT_CHAR, c, None, object_kind="alternate_content"))
            # Anything else (e.g. w:subDoc) is ignored for text but preserved.

    walk(p)
    return ParagraphAtoms(atoms, page_breaks, rendered, state["has_field"], state["has_math"], hyperlinks)


def run_format(r: etree._Element | None) -> dict:
    """Summarise direct run formatting for display purposes."""
    if r is None:
        return {}
    rpr = r.find(w("rPr"))
    if rpr is None:
        return {}

    def on(tag: str) -> bool:
        el = rpr.find(w(tag))
        if el is None:
            return False
        val = el.get(w("val"))
        return val not in ("0", "false", "none")

    fmt: dict = {}
    if on("b"):
        fmt["b"] = True
    if on("i"):
        fmt["i"] = True
    u = rpr.find(w("u"))
    if u is not None and u.get(w("val")) not in (None, "none"):
        fmt["u"] = True
    if on("strike"):
        fmt["s"] = True
    va = rpr.find(w("vertAlign"))
    if va is not None and va.get(w("val")) in ("superscript", "subscript"):
        fmt["va"] = va.get(w("val"))[:3]
    return fmt
