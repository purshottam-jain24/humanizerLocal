"""Build a revised DOCX from the untouched original plus approved edits.

The original package is copied entry by entry. Only XML parts that contain
an edited paragraph are re-serialised; every other entry (media, styles,
relationships, settings, custom XML …) is written back byte-for-byte with
its original compression settings. The result is validated by re-opening
it, re-parsing every XML part and re-extracting the text to confirm each
paragraph contains exactly the approved text.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

from lxml import etree

from app.services.document.validation import SAFE_PARSER, validate_docx_bytes
from app.services.ooxml.package import DocxPackage, iter_paragraphs
from app.services.ooxml.paragraph_editor import UnsafeEditError, replace_paragraph_text
from app.services.ooxml.text_model import paragraph_atoms, w

BOOKMARK_PREFIX = "hw_"


@dataclass
class ExportOutcome:
    data: bytes
    applied: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)


def _serialize(root: etree._Element, original: bytes) -> bytes:
    standalone = b'standalone="yes"' in original[:200]
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True if standalone else None)


def build_revised_docx(original: bytes, main_part: str, salt: str, edits: dict[str, tuple[str, str]],
                       preview_markers: bool = False) -> ExportOutcome:
    """Apply ``edits`` ({paragraph_id: (expected_original_text, new_text)}).

    ``preview_markers`` adds a zero-width bookmark ``hw_<pid>`` to every
    paragraph so the browser preview can map rendered elements back to
    paragraph ids. Markers are never added to real exports.
    """
    pkg = DocxPackage(original, main_part)
    outcome = ExportOutcome(data=b"")
    modified_parts: dict[str, bytes] = {}
    bm_id = 900000

    for part in pkg.text_parts():
        root = None
        dirty = False
        part_root = pkg.fresh_xml(part.name)
        for loc in iter_paragraphs(part_root, part, salt):
            if loc.id in edits:
                expected, new_text = edits[loc.id]
                try:
                    res = replace_paragraph_text(loc.element, expected, new_text)
                    if res.changed:
                        dirty = True
                        outcome.applied.append({"paragraph_id": loc.id, "strategy": res.strategy,
                                                "runs_touched": res.runs_touched, "notes": res.notes})
                except UnsafeEditError as e:
                    outcome.failed.append({"paragraph_id": loc.id, "reason": str(e)})
            if preview_markers and not loc.in_textbox:
                bm_id += 1
                start = etree.Element(w("bookmarkStart"))
                start.set(w("id"), str(bm_id))
                start.set(w("name"), f"{BOOKMARK_PREFIX}{loc.id}")
                end = etree.Element(w("bookmarkEnd"))
                end.set(w("id"), str(bm_id))
                ppr = loc.element.find(w("pPr"))
                if ppr is not None:
                    ppr.addnext(start)
                else:
                    loc.element.insert(0, start)
                start.addnext(end)
                dirty = True
        root = part_root
        if dirty:
            modified_parts[part.name] = _serialize(root, pkg.read(part.name))

    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            if info.filename in modified_parts:
                ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                ni.compress_type = zipfile.ZIP_DEFLATED
                ni.external_attr = info.external_attr
                dst.writestr(ni, modified_parts[info.filename])
            else:
                dst.writestr(info, src.read(info.filename), compress_type=info.compress_type)
    outcome.data = out.getvalue()
    if not preview_markers:
        outcome.validation = validate_export(original, outcome.data, main_part, salt, edits, outcome)
    return outcome


def validate_export(original: bytes, revised: bytes, main_part: str, salt: str,
                    edits: dict[str, tuple[str, str]], outcome: ExportOutcome) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    try:
        validate_docx_bytes(revised, "export.docx", 1 << 31)
        check("Package structure", True, "ZIP, content types, relationships and XML validated")
    except Exception as e:  # noqa: BLE001 - surface any failure to the user
        check("Package structure", False, str(e))
        return {"ok": False, "checks": checks}

    with zipfile.ZipFile(io.BytesIO(original)) as a, zipfile.ZipFile(io.BytesIO(revised)) as b:
        a_names, b_names = set(a.namelist()), set(b.namelist())
        check("All package parts present", a_names == b_names,
              "" if a_names == b_names else f"missing: {sorted(a_names - b_names)[:5]}")
        changed_parts = {n for n in a_names & b_names if a.read(n) != b.read(n)}
        media_ok = all(a.read(n) == b.read(n) for n in a_names & b_names if n.startswith("word/media/"))
        check("Embedded media unchanged", media_ok)
        rels_ok = all(a.read(n) == b.read(n) for n in a_names & b_names if n.endswith(".rels"))
        check("Relationships unchanged", rels_ok)
        styles_ok = all(a.read(n) == b.read(n) for n in ("word/styles.xml", "word/numbering.xml", "word/settings.xml") if n in a_names)
        check("Styles, numbering and settings unchanged", styles_ok)
        allowed = {main_part} | {n for n in a_names if n.startswith("word/header") or n.startswith("word/footer")
                                 or n in ("word/footnotes.xml", "word/endnotes.xml")}
        check("Only text parts modified", changed_parts <= allowed,
              ", ".join(sorted(changed_parts)) or "no parts modified")

    # Re-extract and compare text paragraph by paragraph.
    orig_pkg = DocxPackage(original, main_part)
    new_pkg = DocxPackage(revised, main_part)
    applied_ids = {a["paragraph_id"] for a in outcome.applied}
    mismatches: list[str] = []
    total = 0
    for part in orig_pkg.text_parts():
        o_root = orig_pkg.xml(part.name)
        n_root = new_pkg.xml(part.name)
        o_pars = list(iter_paragraphs(o_root, part, salt))
        n_pars = list(iter_paragraphs(n_root, part, salt))
        if [p.id for p in o_pars] != [p.id for p in n_pars]:
            mismatches.append(f"paragraph structure changed in {part.name}")
            continue
        for op, np_ in zip(o_pars, n_pars):
            total += 1
            o_text = paragraph_atoms(op.element).text
            n_text = paragraph_atoms(np_.element).text
            expected = edits[op.id][1] if op.id in applied_ids else o_text
            if n_text != expected:
                mismatches.append(op.id)
            # Paragraph properties must be untouched.
            o_ppr, n_ppr = op.element.find(w("pPr")), np_.element.find(w("pPr"))
            if (etree.tostring(o_ppr) if o_ppr is not None else b"") != (etree.tostring(n_ppr) if n_ppr is not None else b""):
                mismatches.append(f"{op.id}: paragraph properties changed")
    check("Paragraph text matches approved revisions", not mismatches,
          f"{total} paragraphs verified" if not mismatches else f"{len(mismatches)} mismatch(es): {mismatches[:3]}")

    try:
        import docx  # python-docx as an independent reader
        docx.Document(io.BytesIO(revised))
        check("Opens with python-docx", True)
    except Exception as e:  # noqa: BLE001
        check("Opens with python-docx", False, str(e))

    for pname in [main_part]:
        try:
            etree.fromstring(zipfile.ZipFile(io.BytesIO(revised)).read(pname), SAFE_PARSER)
        except etree.XMLSyntaxError as e:
            check("Main document XML", False, str(e))

    return {"ok": all(c["ok"] for c in checks), "checks": checks}
