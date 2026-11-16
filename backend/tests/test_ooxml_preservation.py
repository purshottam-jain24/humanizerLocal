"""Formatting-preservation regression tests for the OOXML editor and export."""

from __future__ import annotations

import hashlib
import io
import zipfile

import docx
import pytest
from lxml import etree

from app.services.document.validation import validate_docx_bytes
from app.services.export.document_export_service import build_revised_docx
from app.services.extraction.docx_parser import extract_document
from app.services.ooxml.package import DocxPackage
from app.services.ooxml.paragraph_editor import UnsafeEditError, replace_paragraph_text
from app.services.ooxml.text_model import paragraph_atoms, w

SALT = "test"


def _p(xml_body: str) -> etree._Element:
    return etree.fromstring(
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{xml_body}</w:p>'
    )


def _runs(p):
    out = []
    for r in p.iter(w("r")):
        rpr = r.find(w("rPr"))
        props = sorted(etree.QName(c).localname for c in rpr) if rpr is not None else []
        txt = "".join(t.text or "" for t in r.iter(w("t")))
        out.append((txt, props))
    return out


def test_word_replacement_keeps_run_formatting():
    p = _p('<w:r><w:t xml:space="preserve">The </w:t></w:r>'
           '<w:r><w:rPr><w:b/></w:rPr><w:t>quick</w:t></w:r>'
           '<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve"> brown fox</w:t></w:r>')
    replace_paragraph_text(p, "The quick brown fox", "The fast brown fox")
    assert _runs(p) == [("The ", []), ("fast", ["b"]), (" brown fox", ["i"])]


def test_insertion_joins_adjacent_run():
    p = _p('<w:r><w:rPr><w:b/></w:rPr><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world</w:t></w:r>')
    replace_paragraph_text(p, "Hello world", "Hello there world")
    assert paragraph_atoms(p).text == "Hello there world"
    # The bold run keeps exactly its word; the new word joins the adjacent plain run.
    assert _runs(p) == [("Hello", ["b"]), (" there world", [])]


def test_deletion_prunes_empty_runs_only():
    p = _p('<w:r><w:t xml:space="preserve">the </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>the </w:t></w:r>'
           '<w:r><w:t>end</w:t></w:r>')
    replace_paragraph_text(p, "the the end", "the end")
    assert paragraph_atoms(p).text == "the end"
    assert len(list(p.iter(w("r")))) == 2


def test_footnote_reference_is_protected():
    p = _p('<w:r><w:t>Claim</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>'
           '<w:r><w:t xml:space="preserve"> continues here.</w:t></w:r>')
    text = paragraph_atoms(p).text
    assert text == "Claim￼ continues here."
    # Editing around the marker is fine.
    replace_paragraph_text(p, text, "Strong claim￼ continues there.")
    assert paragraph_atoms(p).text == "Strong claim￼ continues there."
    assert len(list(p.iter(w("footnoteReference")))) == 1
    # Deleting the marker is refused.
    with pytest.raises(UnsafeEditError):
        replace_paragraph_text(p, "Strong claim￼ continues there.", "Strong claim continues there.")


def test_insertion_after_object_keeps_order():
    p = _p('<w:r><w:t>Text</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r>')
    replace_paragraph_text(p, "Text￼", "Text￼ and more")
    assert paragraph_atoms(p).text == "Text￼ and more"


def test_hyperlink_text_edit_stays_inside_hyperlink():
    p = _p('<w:r><w:t xml:space="preserve">Visit </w:t></w:r><w:hyperlink r:id="rId9"><w:r><w:t>our portal</w:t></w:r></w:hyperlink>'
           '<w:r><w:t xml:space="preserve"> today.</w:t></w:r>')
    replace_paragraph_text(p, "Visit our portal today.", "Visit our data portal today.")
    hl = p.find(w("hyperlink"))
    assert "".join(t.text for t in hl.iter(w("t"))) == "our data portal"
    assert hl.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") == "rId9"


def test_complex_field_result_is_locked():
    p = _p('<w:r><w:t xml:space="preserve">See page </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
           '<w:r><w:instrText> PAGEREF _Ref1 </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
           '<w:r><w:t>4</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')
    assert paragraph_atoms(p).text == "See page 4"
    with pytest.raises(UnsafeEditError):
        replace_paragraph_text(p, "See page 4", "See page 5")
    replace_paragraph_text(p, "See page 4", "Refer to page 4")
    assert paragraph_atoms(p).text == "Refer to page 4"


def test_tabs_and_breaks_roundtrip():
    p = _p('<w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r>')
    assert paragraph_atoms(p).text == "A\tB\nC"
    replace_paragraph_text(p, "A\tB\nC", "A\tBee\nC and\tD")
    assert paragraph_atoms(p).text == "A\tBee\nC and\tD"


def test_mismatched_expected_text_refused():
    p = _p('<w:r><w:t>Actual</w:t></w:r>')
    with pytest.raises(UnsafeEditError):
        replace_paragraph_text(p, "Different", "New")


@pytest.mark.parametrize("name", ["rich", "mixed", "long", "repetitive"])
def test_export_without_edits_is_package_identical(fixtures_dir, name):
    data = fixtures_dir[name].read_bytes()
    out = build_revised_docx(data, "word/document.xml", SALT, {})
    a, b = zipfile.ZipFile(io.BytesIO(data)), zipfile.ZipFile(io.BytesIO(out.data))
    assert a.namelist() == b.namelist()
    for n in a.namelist():
        assert a.read(n) == b.read(n), n
    assert out.validation["ok"], out.validation


def test_export_applies_edits_and_preserves_everything_else(fixtures_dir):
    path = fixtures_dir["rich"]
    data = path.read_bytes()
    before_hash = hashlib.sha256(data).hexdigest()
    rep = validate_docx_bytes(data, "rich.docx", 50 << 20)
    struct = extract_document(DocxPackage(data, rep.main_part), SALT).structure
    paras = {p["text"][:20]: p for p in struct["paragraphs"]}

    intro = next(p for p in struct["paragraphs"] if p["text"].startswith("Urban green spaces"))
    footnoted = next(p for p in struct["paragraphs"] if p["text"].startswith("Green space access"))
    header = next(p for p in struct["paragraphs"] if p["part"] == "header")
    cell = next(p for p in struct["paragraphs"] if p["text"] == "Nested A")
    edits = {
        intro["id"]: (intro["text"], intro["text"].replace("the the", "the").replace("In order to", "To").replace("recieve", "receive")),
        footnoted["id"]: (footnoted["text"], footnoted["text"].replace("unevenly distributed", "distributed unevenly")),
        header["id"]: (header["text"], "HumanWrite revised header"),
        cell["id"]: (cell["text"], "Nested cell A"),
    }
    out = build_revised_docx(data, rep.main_part, SALT, edits)
    assert not out.failed, out.failed
    assert out.validation["ok"], out.validation
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash  # original untouched

    rev = extract_document(DocxPackage(out.data, rep.main_part), SALT).structure
    rev_by_id = {p["id"]: p for p in rev["paragraphs"]}
    for pid, (_, new) in edits.items():
        assert rev_by_id[pid]["text"] == new
    # Same identifiers, same structure counts
    assert [p["id"] for p in rev["paragraphs"]] == [p["id"] for p in struct["paragraphs"]]
    for key in ("tables", "images", "hyperlinks", "sections", "footnotes", "headers", "footers", "list_items", "headings"):
        assert rev["summary"][key] == struct["summary"][key], key

    # Formatting of the intro paragraph: bold/italic runs survive.
    d = docx.Document(io.BytesIO(out.data))
    intro_p = next(p for p in d.paragraphs if p.text.startswith("Urban green spaces"))
    bold = [r.text for r in intro_p.runs if r.bold]
    italic = [r.text for r in intro_p.runs if r.italic]
    assert bold == ["play a crucial role"]
    assert italic == ["public health"]
    assert any(r.font.color and r.font.color.rgb and str(r.font.color.rgb) == "C00000" for r in intro_p.runs)
    assert paras  # sanity


def test_export_refuses_unsafe_edit_without_damaging(fixtures_dir):
    data = fixtures_dir["rich"].read_bytes()
    struct = extract_document(DocxPackage(data), SALT).structure
    fn = next(p for p in struct["paragraphs"] if p["text"].startswith("Green space access"))
    out = build_revised_docx(data, "word/document.xml", SALT, {fn["id"]: (fn["text"], "Footnote marker removed.")})
    assert out.failed and out.failed[0]["paragraph_id"] == fn["id"]
    assert out.validation["ok"]
    rev = extract_document(DocxPackage(out.data), SALT).structure
    assert next(p for p in rev["paragraphs"] if p["id"] == fn["id"])["text"] == fn["text"]


def test_preview_markers_are_bookmarks(fixtures_dir):
    data = fixtures_dir["mixed"].read_bytes()
    out = build_revised_docx(data, "word/document.xml", SALT, {}, preview_markers=True)
    xml = zipfile.ZipFile(io.BytesIO(out.data)).read("word/document.xml")
    assert xml.count(b'w:name="hw_p') == 2
