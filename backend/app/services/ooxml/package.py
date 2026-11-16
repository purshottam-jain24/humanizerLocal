"""Read-only access to an OPC package plus deterministic paragraph enumeration.

Paragraph identifiers are derived from (part name, structural path) of the
*original* package. Because the original file is never modified, the same
paragraph always resolves to the same identifier and the same XML element,
which is what lets export map revisions back onto the source XML.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from dataclasses import dataclass
from typing import Iterator

from lxml import etree

from app.services.document.validation import SAFE_PARSER
from app.services.ooxml.text_model import MC_NS, W_NS, w

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
RT_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


@dataclass
class PartInfo:
    name: str  # zip path, e.g. word/header1.xml
    kind: str  # body | header | footer | footnotes | endnotes


def rels_path_for(part: str) -> str:
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


class DocxPackage:
    def __init__(self, data: bytes, main_part: str = "word/document.xml"):
        self.data = data
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        self.names = set(self.zip.namelist())
        self.main_part = main_part
        self._xml_cache: dict[str, etree._Element] = {}

    def read(self, name: str) -> bytes:
        return self.zip.read(name)

    def xml(self, name: str) -> etree._Element | None:
        if name not in self.names:
            return None
        if name not in self._xml_cache:
            self._xml_cache[name] = etree.fromstring(self.zip.read(name), SAFE_PARSER)
        return self._xml_cache[name]

    def fresh_xml(self, name: str) -> etree._Element:
        """Independent parse (for mutation during export)."""
        return etree.fromstring(self.zip.read(name), SAFE_PARSER)

    def relationships(self, part: str) -> list[dict]:
        rels_name = rels_path_for(part)
        root = self.xml(rels_name)
        if root is None:
            return []
        base = posixpath.dirname(part)
        out = []
        for rel in root.findall(f"{{{REL_NS}}}Relationship"):
            target = rel.get("Target", "")
            external = rel.get("TargetMode") == "External"
            resolved = target if external else (target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target)))
            out.append({
                "id": rel.get("Id"),
                "type": rel.get("Type", "").rsplit("/", 1)[-1],
                "target": resolved,
                "external": external,
            })
        return out

    def text_parts(self) -> list[PartInfo]:
        parts = [PartInfo(self.main_part, "body")]
        for rel in self.relationships(self.main_part):
            if rel["external"] or rel["target"] not in self.names:
                continue
            if rel["type"] in ("header", "footer", "footnotes", "endnotes"):
                parts.append(PartInfo(rel["target"], rel["type"]))
        order = {"body": 0, "header": 1, "footer": 2, "footnotes": 3, "endnotes": 4}
        parts.sort(key=lambda p: (order[p.kind], p.name))
        return parts


def stable_id(prefix: str, *components: str) -> str:
    h = hashlib.sha1("\x1f".join(components).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}{h}"


@dataclass
class ParagraphLocation:
    id: str
    element: etree._Element
    part: PartInfo
    path: str  # structural path inside the part
    table_path: list[tuple[str, int, int]]  # (table_id, row, col) outermost first
    note_id: str | None  # footnote / endnote id
    in_textbox: bool


def _structural_path(el: etree._Element, root: etree._Element) -> str:
    segs = []
    cur = el
    while cur is not None and cur is not root:
        parent = cur.getparent()
        if parent is None:
            break
        idx = 0
        for sib in parent:
            if sib is cur:
                break
            if isinstance(sib.tag, str) and sib.tag == cur.tag:
                idx += 1
        local = etree.QName(cur).localname if isinstance(cur.tag, str) else "x"
        segs.append(f"{local}[{idx}]")
        cur = parent
    return "/".join(reversed(segs))


def iter_paragraphs(root: etree._Element, part: PartInfo, salt: str = "") -> Iterator[ParagraphLocation]:
    """Yield every paragraph of a part in reading order with a stable id.

    Paragraphs inside ``mc:Fallback`` are skipped (they duplicate
    ``mc:Choice`` content). Text-box paragraphs are yielded with
    ``in_textbox=True`` so callers can decide how to treat them.
    """
    note_tag = {"footnotes": w("footnote"), "endnotes": w("endnote")}.get(part.kind)
    for p in root.iter(w("p")):
        anc = p.getparent()
        skip = False
        in_textbox = False
        note_id = None
        table_path: list[tuple[str, int, int]] = []
        tc_chain: list[etree._Element] = []
        while anc is not None:
            tag = anc.tag
            if tag == f"{{{MC_NS}}}Fallback":
                skip = True
                break
            if tag == w("txbxContent"):
                in_textbox = True
            if note_tag and tag == note_tag:
                ntype = anc.get(w("type"))
                if ntype in ("separator", "continuationSeparator", "continuationNotice"):
                    skip = True
                    break
                note_id = anc.get(w("id"))
            if tag == w("tc"):
                tc_chain.append(anc)
            anc = anc.getparent()
        if skip:
            continue
        for tc in reversed(tc_chain):
            tr = tc.getparent()
            tbl = tr.getparent() if tr is not None else None
            while tbl is not None and tbl.tag != w("tbl"):
                tbl = tbl.getparent()
            if tbl is None:
                continue
            rows = [c for c in tbl if c.tag == w("tr")]
            row_idx = rows.index(tr) if tr in rows else 0
            cells = [c for c in tr if c.tag == w("tc")]
            col_idx = cells.index(tc) if tc in cells else 0
            table_path.append((stable_id("t", salt, part.name, _structural_path(tbl, root)), row_idx, col_idx))
        path = _structural_path(p, root)
        yield ParagraphLocation(
            id=stable_id("p", salt, part.name, path),
            element=p,
            part=part,
            path=path,
            table_path=table_path,
            note_id=note_id,
            in_textbox=in_textbox,
        )


def iter_tables(root: etree._Element, part: PartInfo, salt: str = "") -> Iterator[tuple[str, etree._Element, int]]:
    for tbl in root.iter(w("tbl")):
        depth = sum(1 for a in tbl.iterancestors(w("tbl")))
        yield stable_id("t", salt, part.name, _structural_path(tbl, root)), tbl, depth


__all__ = ["DocxPackage", "PartInfo", "ParagraphLocation", "iter_paragraphs", "iter_tables", "stable_id", "W_NS"]
