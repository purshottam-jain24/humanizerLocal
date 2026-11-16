"""DOCX package validation.

A DOCX is an OPC (ZIP) package. We check the container, the mandatory parts,
XML well-formedness of every XML part, and relationship targets before
anything else touches the file.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass, field

from lxml import etree

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # encrypted OOXML or legacy .doc
MAX_UNCOMPRESSED = 600 * 1024 * 1024
MAX_ENTRIES = 20000
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
STRICT_DOC_REL_TYPE = "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"

SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=False)


class ValidationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidationReport:
    ok: bool
    main_part: str = "word/document.xml"
    warnings: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})


def _rels_path_for(part: str) -> str:
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


def validate_docx_bytes(data: bytes, filename: str, max_bytes: int) -> ValidationReport:
    report = ValidationReport(ok=False)

    if not filename.lower().endswith(".docx"):
        if filename.lower().endswith(".doc"):
            raise ValidationError("legacy_format", "Legacy .doc files are not supported. Save the file as .docx in Word and upload it again.")
        raise ValidationError("bad_extension", "Only .docx files are supported.")
    report.add("File extension", True, ".docx")

    if len(data) == 0:
        raise ValidationError("empty", "The file is empty.")
    if len(data) > max_bytes:
        raise ValidationError("too_large", f"The file is {len(data) / 1e6:.1f} MB, above the {max_bytes / 1e6:.0f} MB limit.")
    report.add("File size", True, f"{len(data) / 1024:.1f} KB")

    if data[:8] == OLE_SIGNATURE:
        raise ValidationError(
            "encrypted",
            "This document is password-protected (encrypted) or is a legacy binary file. Remove the password in Word and upload it again.",
        )
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValidationError("not_zip", "The file is not a valid DOCX package (ZIP container is missing or damaged).")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValidationError("bad_zip", f"The DOCX container is damaged: {e}") from e

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise ValidationError("too_many_parts", "The package contains an unusually large number of parts.")
        total = sum(i.file_size for i in infos)
        if total > MAX_UNCOMPRESSED:
            raise ValidationError("too_large_uncompressed", "The package expands to an unsafe size.")
        for i in infos:
            if i.flag_bits & 0x1:
                raise ValidationError("encrypted_entry", "The package contains encrypted entries.")
            if i.filename.startswith("/") or ".." in i.filename.split("/"):
                raise ValidationError("unsafe_path", f"Unsafe path in package: {i.filename}")
        bad = zf.testzip()
        if bad is not None:
            raise ValidationError("crc", f"Corrupted package entry: {bad}")
        report.add("ZIP integrity", True, f"{len(infos)} parts, CRC verified")

        names = set(zf.namelist())
        if "[Content_Types].xml" not in names:
            raise ValidationError("no_content_types", "Missing [Content_Types].xml — not a valid Office package.")
        if "_rels/.rels" not in names:
            raise ValidationError("no_root_rels", "Missing package relationships (_rels/.rels).")

        # Locate main document part via the root relationship.
        root_rels = etree.fromstring(zf.read("_rels/.rels"), SAFE_PARSER)
        main = None
        for rel in root_rels.findall(f"{{{REL_NS}}}Relationship"):
            if rel.get("Type") in (DOC_REL_TYPE, STRICT_DOC_REL_TYPE):
                main = rel.get("Target", "").lstrip("/")
                if rel.get("Type") == STRICT_DOC_REL_TYPE:
                    raise ValidationError("strict_ooxml", "Strict Open XML documents are not supported yet. Save as a standard Word Document (.docx).")
        if not main or main not in names:
            raise ValidationError("no_main_part", "The package has no main Word document part.")
        report.main_part = main

        ct = zf.read("[Content_Types].xml")
        if b"macroEnabled" in ct:
            report.warnings.append("The document declares macro-enabled content; macros are not executed and are preserved as-is.")

        # Well-formedness of all XML parts.
        xml_count = 0
        for name in names:
            if name.endswith(".xml") or name.endswith(".rels"):
                try:
                    etree.fromstring(zf.read(name), SAFE_PARSER)
                    xml_count += 1
                except etree.XMLSyntaxError as e:
                    raise ValidationError("bad_xml", f"Invalid XML in {name}: {e}") from e
        report.add("XML validity", True, f"{xml_count} XML parts well-formed")

        # Relationship targets for the main part.
        rels_name = _rels_path_for(main)
        missing: list[str] = []
        if rels_name in names:
            rels = etree.fromstring(zf.read(rels_name), SAFE_PARSER)
            base = posixpath.dirname(main)
            for rel in rels.findall(f"{{{REL_NS}}}Relationship"):
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                if resolved not in names:
                    missing.append(target)
        if missing:
            report.warnings.append(f"{len(missing)} relationship target(s) are missing from the package: {', '.join(missing[:5])}")
        report.add("Relationships", not missing, "all internal targets present" if not missing else f"{len(missing)} missing")

    report.ok = True
    return report
