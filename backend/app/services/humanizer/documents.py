"""Stage A input (file → structured plain text) and export (text → TXT / Markdown / DOCX).

Reuses the app's DOCX validator and parser, pypdf for PDFs and the on-device OCR service
for images. Extracted text uses light Markdown conventions so headings, lists and quotes
survive the round trip. Uploaded files are processed in memory and never written to disk.
"""

from __future__ import annotations

import io
import re

from app.services.extraction import ocr_service

TEXT_EXT = {".txt", ".md", ".markdown", ".text"}
DOCX_EXT = {".docx"}
PDF_EXT = {".pdf"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 300


class ExtractionError(Exception):
    pass


def supported_extensions() -> list[str]:
    exts = sorted(TEXT_EXT | DOCX_EXT | PDF_EXT)
    if ocr_service.available():
        exts += list(ocr_service.OCR_FORMATS)
    return exts


def extract(filename: str, data: bytes) -> dict:
    name = filename or "upload"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if not data:
        raise ExtractionError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ExtractionError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    warnings: list[str] = []
    if ext in TEXT_EXT:
        text = _decode(data)
        source = "text"
    elif ext in DOCX_EXT:
        text, warnings = _docx(data, name)
        source = "docx"
    elif ext in PDF_EXT:
        text, warnings = _pdf(data)
        source = "pdf"
    elif ext in ocr_service.OCR_FORMATS:
        if not ocr_service.available():
            raise ExtractionError("Reading text from images needs on-device OCR, which is only available on macOS.")
        text = _ocr(data)
        source = "ocr"
        warnings.append("Text was recognised from an image (OCR). Check it for recognition errors before rewriting.")
    elif ext == ".doc":
        raise ExtractionError("Old .doc files aren't supported. Open the file in Word and save it as .docx.")
    else:
        raise ExtractionError(f"Unsupported file type “{ext or name}”. Use TXT, Markdown, DOCX or PDF" +
                              (", or an image (PNG, JPG…) for OCR." if ocr_service.available() else "."))
    text = text.replace("\r\n", "\n").replace(" ", " ").strip("\n") + "\n"
    if not re.search(r"\w", text):
        raise ExtractionError("No readable text was found in the file.")
    return {"text": text, "source": source, "filename": name, "warnings": warnings, "characters": len(text),
            "words": len(re.findall(r"\w+", text))}


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _docx(data: bytes, name: str) -> tuple[str, list[str]]:
    from app.services.document.validation import ValidationError, validate_docx_bytes
    from app.services.extraction.docx_parser import extract_document
    from app.services.ooxml.package import DocxPackage

    try:
        rep = validate_docx_bytes(data, name, MAX_FILE_BYTES)
        st = extract_document(DocxPackage(data, rep.main_part)).structure
    except ValidationError as e:
        raise ExtractionError(getattr(e, "message", None) or str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not read the Word document: {str(e)[:200]}") from e
    out: list[str] = []
    warnings: list[str] = []
    tables = 0
    for p in st["paragraphs"]:
        if p["part"] != "body":
            continue
        text = p["text"].replace("￼", "").strip()
        if not text:
            continue
        if p.get("table"):
            tables += 1
            continue
        kind = p.get("kind")
        if kind == "title":
            out.append(f"# {text}")
        elif kind == "heading":
            level = min(6, max(1, int(p.get("heading_level") or 1)))
            out.append(f"{'#' * level} {text}")
        elif kind == "list_item":
            fmt = (p.get("list") or {}).get("format") if isinstance(p.get("list"), dict) else None
            out.append(("1. " if fmt and fmt not in ("bullet", "none") else "- ") + text)
        else:
            out.append(text)
    if tables:
        warnings.append(f"{tables} table cell(s) were left out; tables are not rewritten.")
    # Consecutive list items are single-spaced; everything else is separated by a blank line.
    text = ""
    for i, line in enumerate(out):
        if i:
            both_list = re.match(r"^(- |\d+\. )", line) and re.match(r"^(- |\d+\. )", out[i - 1])
            text += "\n" if both_list else "\n\n"
        text += line
    return text, warnings


def _pdf(data: bytes) -> tuple[str, list[str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:  # noqa: BLE001
                raise ExtractionError("This PDF is password-protected.") from e
        pages = reader.pages
        warnings = []
        if len(pages) > MAX_PDF_PAGES:
            warnings.append(f"Only the first {MAX_PDF_PAGES} pages were read.")
        raw = [(pg.extract_text() or "") for pg in list(pages)[:MAX_PDF_PAGES]]
    except ExtractionError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not read the PDF: {str(e)[:200]}") from e
    text = "\n\n".join(_reflow_pdf_page(p) for p in raw if p.strip())
    if len(re.findall(r"\w+", text)) < 5:
        raise ExtractionError("This PDF has no text layer (it is probably a scan). Export the pages as images and upload them for OCR"
                              + ("." if ocr_service.available() else " on a Mac."))
    warnings.append("PDF layout (columns, footnotes, page headers) can affect extracted text. Check paragraph breaks before rewriting.")
    return text, warnings


def _reflow_pdf_page(page: str) -> str:
    """Join hard-wrapped PDF lines into paragraphs; keep headings, list items and blank-line breaks."""
    page = re.sub(r"(\w)-\n(\w)", r"\1\2", page)
    lines = [l.rstrip() for l in page.split("\n")]
    paras: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            paras.append(" ".join(s.strip() for s in cur))
            cur.clear()

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            flush()
            continue
        if re.match(r"^([-•‣◦*]|\d{1,3}[.)])\s+", s):
            flush()
            cur.append(re.sub(r"^[•‣◦*]\s+", "- ", s))
            continue
        prev = cur[-1].strip() if cur else ""
        # A short line ending a sentence (or a line before a capitalised one) ends a paragraph.
        if prev and re.search(r"[.!?:”\"]$", prev) and len(prev) < 60 and s[:1].isupper():
            flush()
        cur.append(s)
    flush()
    return "\n\n".join(paras)


def _ocr(data: bytes) -> str:
    lines: dict[int, list[str]] = {}
    for w in ocr_service.recognize(data):
        lines.setdefault(w["line"], []).append(w["text"])
    text_lines = [" ".join(ws) for _, ws in sorted(lines.items())]
    # Join lines into paragraphs: a line that ends a sentence and is short ends the paragraph.
    return _reflow_pdf_page("\n".join(text_lines))


# ------------------------------------------------------------------ export
def to_markdown(text: str) -> bytes:
    return text.encode("utf-8")


def to_plain(text: str) -> bytes:
    out = []
    for line in text.split("\n"):
        out.append(re.sub(r"^#{1,6}\s+", "", line))
    return "\n".join(out).encode("utf-8")


def to_docx(text: str, title: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt

    from app.services.humanizer.blocks import parse_blocks

    doc = Document()
    style = doc.styles["Normal"]
    style.font.size = Pt(11)
    if title:
        doc.core_properties.title = title[:200]
    for b in parse_blocks(text):
        content = b.text.strip()
        if b.kind == "blank" or not content:
            continue
        if b.kind == "heading":
            doc.add_heading(_inline(content), level=min(max(b.level or 1, 1), 6) if not b.meta.get("plain_heading") else 2)
        elif b.kind == "list_item":
            numbered = re.match(r"\s*\d", b.prefix) is not None
            doc.add_paragraph(_inline(content), style="List Number" if numbered else "List Bullet")
        elif b.kind == "quote":
            body = re.sub(r"^>\s?", "", content, flags=re.M)
            doc.add_paragraph(body, style="Quote")
        elif b.kind == "code":
            p = doc.add_paragraph()
            run = p.add_run(re.sub(r"^(```|~~~).*\n?|\n?(```|~~~)\s*$", "", content))
            run.font.name = "Courier New"
            run.font.size = Pt(10)
        else:
            doc.add_paragraph(_inline(content.replace("\n", " ")))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _inline(s: str) -> str:
    # Plain-text export: drop Markdown emphasis markers but keep the words.
    return re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
