"""Generate DOCX fixtures covering the structures HumanWrite must preserve.

python-docx creates most content; features it lacks (footnotes, hyperlinks,
fields) are injected as raw OOXML so the fixtures resemble real Word output.
"""

from __future__ import annotations

import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_BREAK
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor, Inches


def _png(width: int = 40, height: int = 20) -> bytes:
    raw = b"".join(b"\x00" + bytes([30, 140, 220]) * width for _ in range(height))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def add_hyperlink(paragraph, url: str, text: str):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    xml = (f'<w:hyperlink {nsdecls("w", "r")} r:id="{r_id}"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/>'
           f'<w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:hyperlink>')
    paragraph._p.append(parse_xml(xml))


def build_rich(path: Path) -> None:
    doc = Document()
    doc.core_properties.title = "Fixture: Rich Document"
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = "HumanWrite fixture header"
    sec.footer.paragraphs[0].text = "Confidential draft footer"

    doc.add_heading("The Effects of Urban Green Spaces", level=0)
    doc.add_heading("1. Introduction", level=1)

    p = doc.add_paragraph()
    p.add_run("Urban green spaces ")
    b = p.add_run("play a crucial role")
    b.bold = True
    p.add_run(" in improving ")
    it = p.add_run("public health")
    it.italic = True
    it.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    p.add_run(" and the the wellbeing of residents. In order to understand this effect, we recieve data from several cities.")

    p2 = doc.add_paragraph("Furthermore, it is important to note that green spaces reduce heat. Moreover, they improve air quality. "
                           "Additionally, they support biodiversity. Studies show that residents are happier.")
    p2.paragraph_format.space_after = Pt(18)
    p2.paragraph_format.first_line_indent = Inches(0.5)

    p3 = doc.add_paragraph("See the dataset at ")
    add_hyperlink(p3, "https://example.org/data", "the open data portal")
    r = p3.add_run(" for details.")
    r.font.size = Pt(13)

    doc.add_heading("1.1 Methods", level=2)
    doc.add_paragraph("First survey item with a bullet.", style="List Bullet")
    doc.add_paragraph("Nested survey item at level two.", style="List Bullet 2")
    doc.add_paragraph("Numbered step one of the method.", style="List Number")
    doc.add_paragraph("Numbered step two of the method.", style="List Number")

    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    for ri, row in enumerate(table.rows):
        for ci, cell in enumerate(row.cells):
            cell.text = f"Row {ri} col {ci} value"
    shade = parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="D9E2F3"/>')
    table.rows[0].cells[0]._tc.get_or_add_tcPr().append(shade)
    inner = table.rows[2].cells[2].add_table(rows=1, cols=2)
    inner.rows[0].cells[0].text = "Nested A"
    inner.rows[0].cells[1].text = "Nested B"

    img_par = doc.add_paragraph()
    img_par.add_run().add_picture(io.BytesIO(_png()), width=Inches(1.5))
    doc.add_paragraph("Figure 1. A placeholder chart of park coverage.", style="Caption")

    pb = doc.add_paragraph("This paragraph ends with a manual page break.")
    pb.add_run().add_break(WD_BREAK.PAGE)

    doc.add_heading("2. Discussion", level=1)
    fn_par = doc.add_paragraph("Green space access is unevenly distributed across neighbourhoods")
    fn_par._p.append(parse_xml(
        f'<w:r {nsdecls("w")}><w:rPr><w:rStyle w:val="FootnoteReference"/><w:vertAlign w:val="superscript"/></w:rPr>'
        f'<w:footnoteReference w:id="1"/></w:r>'))
    fn_par.add_run(", which raises questions of equity (Smith, 2020).")

    new_sec = doc.add_section()
    new_sec.orientation = WD_ORIENT.LANDSCAPE
    new_sec.page_width, new_sec.page_height = new_sec.page_height, new_sec.page_width
    doc.add_paragraph("This paragraph lives in a landscape section.")

    doc.add_heading("References", level=1)
    doc.add_paragraph("Smith, J. (2020). Parks and equity. Journal of Urban Studies, 12(3), 45-67.")
    doc.add_paragraph("Lee, K. (2019). Heat islands in dense cities. Climate Review, 8, 100-120.")

    buf = io.BytesIO()
    doc.save(buf)
    _inject_footnotes(buf.getvalue(), path)


FOOTNOTES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
<w:footnote w:id="1"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> Based on census tract data from 2018.</w:t></w:r></w:p></w:footnote>
</w:footnotes>"""


def _inject_footnotes(data: bytes, path: Path) -> None:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            content = src.read(item.filename)
            if item.filename == "[Content_Types].xml":
                content = content.replace(b"</Types>",
                    b'<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>')
            if item.filename == "word/_rels/document.xml.rels":
                content = content.replace(b"</Relationships>",
                    b'<Relationship Id="rIdFn1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/></Relationships>')
            dst.writestr(item, content)
        dst.writestr("word/footnotes.xml", FOOTNOTES_XML)
    path.write_bytes(out.getvalue())


def build_mixed_runs(path: Path) -> None:
    """Many runs per word, typical of documents edited repeatedly in Word."""
    doc = Document()
    p = doc.add_paragraph()
    for i, chunk in enumerate(["The ", "quick", " br", "own ", "fox ", "jumps ", "over", " the ", "lazy", " dog", "."]):
        r = p.add_run(chunk)
        r.bold = i % 3 == 0
        r.italic = i % 4 == 1
        if i == 4:
            r.font.highlight_color = 7  # yellow
    p2 = doc.add_paragraph()
    p2.add_run("Tabs\tand line")
    p2.add_run().add_break()
    p2.add_run("breaks are preserved.")
    doc.save(path)


def build_long(path: Path, paragraphs: int = 400) -> None:
    doc = Document()
    doc.add_heading("A Long Academic Report", 0)
    for i in range(paragraphs):
        if i % 25 == 0:
            doc.add_heading(f"Section {i // 25 + 1}", 1)
        doc.add_paragraph(
            f"Paragraph {i} discusses the topic in detail. The results indicate a measurable effect in sample {i}. "
            "However, further research is needed to confirm whether the observed pattern holds across contexts. "
            "This analysis considers several variables and their interactions over time."
        )
    doc.save(path)


def build_repetitive(path: Path) -> None:
    doc = Document()
    doc.add_paragraph("In today's fast-paced world, technology plays a crucial role in education. "
                      "Furthermore, technology plays a crucial role in communication. Moreover, technology plays a crucial "
                      "role in healthcare. Additionally, technology plays a crucial role in business.")
    doc.add_paragraph("In today's fast-paced world, technology plays a crucial role in education. "
                      "Furthermore, technology plays a crucial role in communication.")
    doc.save(path)


def build_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "rich": out_dir / "rich.docx",
        "mixed": out_dir / "mixed_runs.docx",
        "long": out_dir / "long.docx",
        "repetitive": out_dir / "repetitive.docx",
    }
    build_rich(files["rich"])
    build_mixed_runs(files["mixed"])
    build_long(files["long"])
    build_repetitive(files["repetitive"])
    return files


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "generated"
    for k, v in build_all(out).items():
        print(k, v)
