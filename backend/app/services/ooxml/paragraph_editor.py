"""Format-preserving text replacement inside a WordprocessingML paragraph.

Strategy
--------
1. Build the atom list (every visible char → its XML element).
2. Diff old vs new text on word/space/punctuation tokens.
3. Unchanged characters stay in their original ``w:t``; inserted text joins
   the run of the neighbouring character so it inherits that formatting;
   deleted characters are removed from their ``w:t``.
4. Locked atoms (footnote references, images, fields, equations) must fall
   inside unchanged regions. If an edit would delete or rewrite one, the
   edit is refused with :class:`UnsafeEditError` instead of damaging it.
5. Empty ``w:t`` / runs created by deletions are pruned; tabs and line
   breaks typed in new text become ``w:tab`` / ``w:br`` elements.

Nothing outside the paragraph's text-bearing elements is touched, so
paragraph properties, run properties, bookmarks, comments ranges,
hyperlink relationships and section properties are preserved.
"""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass

from lxml import etree

from app.services.ooxml.text_model import XML_NS, Atom, paragraph_atoms, w

TOKEN = re.compile(r"\w+|\s|[^\w\s]", re.UNICODE)


class UnsafeEditError(Exception):
    pass


@dataclass
class EditResult:
    changed: bool
    strategy: str  # unchanged | mapped | new_run
    runs_touched: int
    notes: list[str]


def _tokens(text: str) -> list[str]:
    return TOKEN.findall(text)


def char_opcodes(old: str, new: str) -> list[tuple[str, int, int, int, int]]:
    """Token-level diff expanded to character offsets."""
    a, b = _tokens(old), _tokens(new)
    assert "".join(a) == old and "".join(b) == new
    a_off = [0]
    for t in a:
        a_off.append(a_off[-1] + len(t))
    b_off = [0]
    for t in b:
        b_off.append(b_off[-1] + len(t))
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(tag, a_off[i1], a_off[i2], b_off[j1], b_off[j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes()]


def _set_text(t: etree._Element, text: str) -> None:
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(f"{{{XML_NS}}}space", "preserve")


def _split_special(run: etree._Element, t: etree._Element, text: str) -> None:
    """Write ``text`` into ``t``; tabs/newlines become sibling w:tab / w:br."""
    if "\t" not in text and "\n" not in text:
        _set_text(t, text)
        return
    pieces = re.split(r"([\t\n])", text)
    anchor = t
    first = True
    for piece in pieces:
        if piece == "":
            continue
        if piece == "\t":
            el = etree.Element(w("tab"))
        elif piece == "\n":
            el = etree.Element(w("br"))
        else:
            if first:
                _set_text(t, piece)
                first = False
                continue
            el = etree.Element(w("t"))
            _set_text(el, piece)
        if first:
            # Text started with a special char: keep t but empty it, insert before.
            t.addprevious(el)
            continue
        anchor.addnext(el)
        anchor = el
    if first:
        _set_text(t, "")


def _run_is_empty(run: etree._Element) -> bool:
    for c in run:
        if not isinstance(c.tag, str):
            continue
        if c.tag == w("rPr"):
            continue
        if c.tag == w("t") and not (c.text or ""):
            continue
        return False
    return True


def replace_paragraph_text(p: etree._Element, expected_old: str, new_text: str) -> EditResult:
    pa = paragraph_atoms(p)
    atoms = pa.atoms
    old = pa.text
    if old != expected_old:
        raise UnsafeEditError("The paragraph in the source document does not match the extracted text; refusing to edit.")
    if old == new_text:
        return EditResult(False, "unchanged", 0, [])

    # out[i] = text that replaces atom i (for editable atoms); ins_before = text inserted before atom 0
    out: list[str | None] = [a.char if a.editable else None for a in atoms]
    positional: list[tuple[int, str]] = []  # (insert before atom index, text)
    deleted: set[int] = set()
    notes: list[str] = []

    def editable_anchor(i: int, direction: int) -> int | None:
        j = i
        while 0 <= j < len(atoms):
            if atoms[j].kind == "text":
                return j
            j += direction
        return None

    for tag, i1, i2, j1, j2 in char_opcodes(old, new_text):
        if tag == "equal":
            continue
        for k in range(i1, i2):
            if not atoms[k].editable:
                raise UnsafeEditError(
                    f"The change would modify a protected element ({atoms[k].object_kind or atoms[k].kind}). "
                    "Keep footnote markers, images, fields and equations in place, or edit this paragraph in Word."
                )
        ins = new_text[j1:j2]
        for k in range(i1, i2):
            out[k] = ""
            deleted.add(k)
        if not ins:
            continue
        if i2 > i1 and atoms[i1].kind == "text":
            target = i1  # replacement keeps the formatting of the first replaced char
            out[target] = ins
            continue
        # Pure insertion (or replacement of a tab/break): like Word, inserted text
        # inherits the formatting of the preceding character; at the start of a
        # paragraph it inherits from the following character.
        if i1 > 0 and atoms[i1 - 1].kind == "text":
            out[i1 - 1] = (out[i1 - 1] or "") + ins
            continue
        if i2 < len(atoms) and atoms[i2].kind == "text":
            out[i2] = ins + (out[i2] or "")
            continue
        # Neighbours are objects/tabs: place a new run directly after the
        # preceding element (or before the following one) so order is kept.
        positional.append((i1, ins))

    # Group results per w:t element, preserving order.
    touched_runs: set[int] = set()
    by_t: dict[int, tuple[Atom, list[str]]] = {}
    for idx, a in enumerate(atoms):
        if a.kind == "text":
            key = id(a.element)
            if key not in by_t:
                by_t[key] = (a, [])
            by_t[key][1].append(out[idx] or "")
    for key, (a, parts) in by_t.items():
        new_val = "".join(parts)
        if new_val != (a.element.text or ""):
            touched_runs.add(id(a.run))
            _split_special(a.run, a.element, new_val)

    # Remove deleted tabs/breaks.
    for idx in deleted:
        a = atoms[idx]
        if a.kind in ("tab", "break") and a.element.getparent() is not None:
            touched_runs.add(id(a.run))
            a.element.getparent().remove(a.element)

    strategy = "mapped"
    for before_idx, text in positional:
        strategy = "new_run"
        src_idx = editable_anchor(before_idx - 1, -1) if before_idx > 0 else None
        if src_idx is None:
            src_idx = editable_anchor(before_idx, 1)
        src_run = atoms[src_idx].run if src_idx is not None else next(iter(p.iter(w("r"))), None)
        new_r = etree.Element(w("r"))
        if src_run is not None and src_run.find(w("rPr")) is not None:
            new_r.append(copy.deepcopy(src_run.find(w("rPr"))))
        t = etree.SubElement(new_r, w("t"))
        _split_special(new_r, t, text)
        if before_idx > 0:
            ref = atoms[before_idx - 1]
            anchor = ref.run if ref.run is not None and ref.run.getparent() is not None else ref.element
            anchor.addnext(new_r)
        elif atoms:
            ref = atoms[0]
            anchor = ref.run if ref.run is not None and ref.run.getparent() is not None else ref.element
            anchor.addprevious(new_r)
        else:
            p.append(new_r)
        notes.append("Text inserted next to a protected element was placed in a new run with matching formatting.")

    # Prune empty w:t and empty runs (only runs whose sole content was emptied text).
    for t in list(p.iter(w("t"))):
        if not (t.text or "") and t.getparent() is not None:
            t.getparent().remove(t)
    for r in list(p.iter(w("r"))):
        if id(r) in touched_runs and _run_is_empty(r) and r.getparent() is not None:
            r.getparent().remove(r)

    result_text = paragraph_atoms(p).text
    if result_text != new_text:
        raise UnsafeEditError("Internal check failed: the edited paragraph does not match the requested text.")
    return EditResult(True, strategy, len(touched_runs), notes)


def check_edit_safety(p: etree._Element, expected_old: str, new_text: str) -> str | None:
    """Return an error message if the edit cannot be applied safely, else None."""
    trial = copy.deepcopy(p)
    try:
        replace_paragraph_text(trial, expected_old, new_text)
    except UnsafeEditError as e:
        return str(e)
    return None
