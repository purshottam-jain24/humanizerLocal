"""Suggestions: creation, streamed AI generation, review actions."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any, AsyncIterator

from app.services.document import document_service as docs
from app.services.extraction.sentences import WORD_RE
from app.services.ollama.ollama_service import GenerationCancelled, OllamaUnavailable, ollama
from app.services.writing import revision_service as revisions
from app.services.writing.assessment import assess
from app.services.writing.prompts import ACTION_LABELS, build_messages, parse_tagged, protect, restore
from app.storage import db
from app.storage.settings_store import get_settings

_cancel_events: dict[str, asyncio.Event] = {}


def _row(r: dict) -> dict:
    r["meaning"] = db.loads(r.pop("meaning_json"), None)
    r["verification"] = db.loads(r.pop("verification_json"), None)
    return r


def get(sid: str) -> dict | None:
    r = db.query_one("SELECT * FROM suggestions WHERE id=?", (sid,))
    return _row(r) if r else None


def list_for(doc_id: str, paragraph_id: str | None = None, status: str | None = None) -> list[dict]:
    q = "SELECT * FROM suggestions WHERE document_id=?"
    params: list[Any] = [doc_id]
    if paragraph_id:
        q += " AND paragraph_id=?"
        params.append(paragraph_id)
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at"
    return [_row(r) for r in db.query(q, params)]


def insert(doc_id: str, **f: Any) -> dict:
    sid = db.new_id("sug")
    t = db.now()
    db.execute(
        "INSERT INTO suggestions(id, document_id, paragraph_id, sentence_id, finding_id, group_id, scope, action, mode, base_text, "
        "original_text, proposed_text, span_start, span_end, category, explanation, reason, meaning_json, verification_json, status, "
        "reviewed, source, model, priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
        (sid, doc_id, f["paragraph_id"], f.get("sentence_id"), f.get("finding_id"), f.get("group_id") or sid, f["scope"], f["action"],
         f.get("mode"), f["base_text"], f["original_text"], f["proposed_text"], f.get("span_start"), f.get("span_end"),
         f["category"], f.get("explanation"), f.get("reason"), db.dumps(f.get("meaning")), db.dumps(f.get("verification")),
         f.get("status", "pending"), f.get("source", "rule"), f.get("model"), f.get("priority", "medium"), t, t),
    )
    return get(sid)


def update(sid: str, **f: Any) -> None:
    sets, vals = [], []
    for k, v in f.items():
        if k in ("meaning", "verification"):
            k = f"{k}_json"
            v = db.dumps(v)
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(db.now())
    db.execute(f"UPDATE suggestions SET {', '.join(sets)} WHERE id=?", (*vals, sid))


# --------------------------------------------------------------------- rules
def from_findings(doc_id: str, findings: list[dict], paragraphs: dict[str, dict]) -> list[dict]:
    out = []
    for f in findings:
        if f.get("correction") is None or f.get("start") is None or not f.get("paragraph_id"):
            continue
        p = paragraphs.get(f["paragraph_id"])
        if not p:
            continue
        original = p["text"][f["start"]:f["end"]]
        if original != f.get("passage"):
            continue
        context = p["text"][max(0, f["start"] - 60):f["end"] + 60]
        meaning, verification = assess(original, f["correction"], context)
        if f["module"] in ("academic",) or (f.get("data") or {}).get("needs_verification"):
            verification = {"required": True, "items": ["This change relates to sources or facts; verify before accepting."]}
        out.append(insert(
            doc_id, paragraph_id=p["id"], sentence_id=f.get("sentence_id"), finding_id=f.get("id"), scope="span",
            action="fix_finding", mode="light", base_text=p["text"], original_text=original, proposed_text=f["correction"],
            span_start=f["start"], span_end=f["end"], category=f["category"], explanation=f["explanation"],
            reason=f"{f['module'].replace('_', ' ').title()} check ({f['source']})", meaning=meaning, verification=verification,
            source=f["source"], priority=f["priority"],
        ))
    return out


# ------------------------------------------------------------------ applying
def proposed_paragraph(s: dict, current: str) -> tuple[str | None, str]:
    """Return (new paragraph text, how) or (None, reason) when the suggestion is stale."""
    if s["scope"] == "paragraph":
        if current == s["base_text"]:
            return s["proposed_text"], "paragraph"
        return None, "The paragraph has changed since this suggestion was generated. Regenerate it."
    if current == s["base_text"] and s["span_start"] is not None:
        st, en = s["span_start"], s["span_end"]
        if current[st:en] == s["original_text"]:
            return current[:st] + s["proposed_text"] + current[en:], "span"
    n = current.count(s["original_text"])
    if n == 1:
        return current.replace(s["original_text"], s["proposed_text"], 1), "relocated"
    if n == 0:
        return None, "The original passage no longer exists in the paragraph."
    return None, "The original passage appears more than once; apply it manually."


def accept(doc_id: str, sid: str, edited_text: str | None = None) -> dict:
    s = get(sid)
    if not s or s["document_id"] != doc_id:
        raise revisions.RevisionError("Suggestion not found.")
    if s["status"] in ("generating",):
        raise revisions.RevisionError("Generation is still running.")
    if s["status"] == "incomplete" and edited_text is None:
        raise revisions.RevisionError("This draft is incomplete (generation was interrupted). Edit it or regenerate before accepting.")
    current = revisions.current_text(doc_id, s["paragraph_id"])
    if edited_text is not None:
        update(sid, proposed_text=edited_text, source=s["source"] + "+edited" if "edited" not in s["source"] else s["source"])
        s = get(sid)
    new_text, how = proposed_paragraph(s, current)
    if new_text is None:
        update(sid, status="stale")
        raise revisions.RevisionError(how)
    op = revisions.record(doc_id, "accept", paragraph_id=s["paragraph_id"], after_text=new_text, suggestion_id=sid,
                          new_status="accepted", note=ACTION_LABELS.get(s["action"], s["action"]))
    # Sibling alternatives are superseded by the accepted one.
    db.execute("UPDATE suggestions SET status='superseded', updated_at=? WHERE group_id=? AND id<>? AND status='pending'",
               (db.now(), s["group_id"], sid))
    return {"op": op, "paragraph_id": s["paragraph_id"], "text": new_text, "applied_as": how}


def reject(doc_id: str, sid: str) -> dict:
    s = get(sid)
    if not s or s["document_id"] != doc_id:
        raise revisions.RevisionError("Suggestion not found.")
    op = revisions.record(doc_id, "reject", paragraph_id=None, suggestion_id=sid, new_status="rejected",
                          note=ACTION_LABELS.get(s["action"], s["action"]))
    return {"op": op}


def mark_reviewed(sid: str, reviewed: bool = True) -> None:
    update(sid, reviewed=1 if reviewed else 0)


# ---------------------------------------------------------------- generation
def _terminology(structure: dict) -> list[str]:
    counts = Counter()
    for p in structure["paragraphs"]:
        if p["part"] != "body":
            continue
        for w in WORD_RE.findall(p["text"]):
            if len(w) > 6 and w.lower() == w:
                counts[w] += 1
            elif w.isupper() and 2 <= len(w) <= 6:
                counts[w] += 2
    return [w for w, c in counts.most_common(15) if c >= 3]


def _context(doc_id: str, structure: dict, pid: str) -> dict:
    paras = structure["paragraphs"]
    idx = next(i for i, p in enumerate(paras) if p["id"] == pid)
    target = paras[idx]

    def neighbour(step: int) -> str | None:
        j = idx + step
        while 0 <= j < len(paras):
            q = paras[j]
            if q["part"] == target["part"] and q["kind"] in ("paragraph", "list_item", "heading", "title") and q["word_count"]:
                return revisions.current_text(doc_id, q["id"], structure).replace("￼", "")
            j += step
        return None

    heading = None
    for j in range(idx - 1, -1, -1):
        if paras[j]["kind"] in ("heading", "title") and paras[j]["part"] == "body":
            heading = paras[j]["text"]
            break
    title = structure["metadata"].get("title") or next((p["text"] for p in paras if p["kind"] == "title"), None) or \
        next((p["text"] for p in paras if p["kind"] == "heading"), None)
    return {"prev": neighbour(-1), "next": neighbour(1), "heading": heading, "title": title, "terms": _terminology(structure)}


def cancel(gen_id: str) -> bool:
    ev = _cancel_events.get(gen_id)
    if ev:
        ev.set()
        return True
    return False


async def generate(doc_id: str, *, paragraph_id: str, scope: str = "paragraph", sentence_id: str | None = None,
                   action: str = "improve_clarity", mode: str | None = None, custom_instruction: str | None = None,
                   finding_id: str | None = None, alt_style: str | None = None, group_id: str | None = None,
                   gen_id: str | None = None) -> AsyncIterator[dict]:
    """Stream a suggestion. Yields events: started, token, completed | cancelled | error."""
    settings = get_settings()
    model = settings["ollama"]["model"]
    mode = mode or settings["writing"]["intensity"]
    structure = docs.load_structure(doc_id)
    pmap = docs.paragraph_map(structure)
    if paragraph_id not in pmap:
        yield {"type": "error", "message": "Unknown paragraph."}
        return
    try:
        await ollama.ensure_model(model)
    except OllamaUnavailable as e:
        yield {"type": "error", "message": str(e), "code": "ollama_unavailable"}
        return

    current = revisions.current_text(doc_id, paragraph_id, structure)
    span_start = span_end = None
    if scope == "sentence":
        from app.services.extraction.sentences import split_sentences

        spans = split_sentences(current)
        target_span = None
        if sentence_id and sentence_id.rsplit(".", 1)[-1][1:].isdigit():
            k = int(sentence_id.rsplit(".", 1)[-1][1:])
            if k < len(spans):
                target_span = spans[k]
        if target_span is None:
            yield {"type": "error", "message": "Sentence not found in the current paragraph text."}
            return
        span_start, span_end = target_span
        original = current[span_start:span_end]
    else:
        original = current
    finding = db.query_one("SELECT * FROM findings WHERE id=?", (finding_id,)) if finding_id else None

    protected, n_markers = protect(original)
    ctx = _context(doc_id, structure, paragraph_id)
    avoid = []
    if group_id:
        avoid = [r["proposed_text"] for r in db.query("SELECT proposed_text FROM suggestions WHERE group_id=? AND proposed_text<>''", (group_id,))]
    messages = build_messages(
        action=action, mode=mode, tone=settings["writing"]["tone"], target_text=protected, scope=scope,
        paragraph_text=protect(current)[0], prev_text=ctx["prev"], next_text=ctx["next"], doc_title=ctx["title"],
        section_heading=ctx["heading"], terminology=ctx["terms"], custom_instruction=custom_instruction,
        global_instructions=settings["writing"]["custom_instructions"], finding=finding, alt_style=alt_style, avoid=avoid,
    )
    category = {"fix_grammar": "grammar", "fix_spelling": "spelling", "improve_punctuation": "punctuation",
                "remove_repetition": "repetition", "concise": "wordiness", "simplify": "clarity", "improve_transitions": "transition",
                "academic_tone": "tone", "terminology": "terminology", "readability": "readability"}.get(action, finding["category"] if finding else "clarity")
    sug = insert(doc_id, paragraph_id=paragraph_id, sentence_id=sentence_id, finding_id=finding_id, group_id=group_id,
                 scope="sentence" if scope == "sentence" else "paragraph", action=action, mode=mode, base_text=current,
                 original_text=original, proposed_text="", span_start=span_start, span_end=span_end, category=category,
                 explanation=None, reason=ACTION_LABELS.get(action, action), status="generating", source="llm", model=model,
                 priority=finding["priority"] if finding else "medium")
    if scope != "sentence":
        update(sug["id"], span_start=None, span_end=None)
    gen_id = gen_id or sug["id"]
    cancel_ev = asyncio.Event()
    _cancel_events[gen_id] = cancel_ev
    yield {"type": "started", "suggestion_id": sug["id"], "gen_id": gen_id, "model": model, "group_id": sug["group_id"]}

    buf: list[str] = []
    stats = None
    thinking_announced = False
    try:
        async for ch in ollama.chat_stream(model, messages, options={"temperature": settings["ollama"]["temperature"] + (0.25 if alt_style else 0),
                                                                     "num_ctx": settings["ollama"]["num_ctx"]},
                                           keep_alive=settings["ollama"]["keep_alive"], cancel=cancel_ev):
            if ch.thinking and not thinking_announced:
                thinking_announced = True
                yield {"type": "thinking"}  # the model is reasoning before it writes
            if ch.content:
                buf.append(ch.content)
                yield {"type": "token", "text": ch.content}
            if ch.done:
                stats = ch.stats
    except GenerationCancelled:
        raw = "".join(buf)
        rev, exp, _ = parse_tagged(raw)
        partial, _ = restore(rev or "", n_markers)
        update(sug["id"], proposed_text=partial, status="incomplete", explanation=exp)
        yield {"type": "cancelled", "suggestion": get(sug["id"])}
        _cancel_events.pop(gen_id, None)
        return
    except (OllamaUnavailable, Exception) as e:  # noqa: BLE001
        raw = "".join(buf)
        rev, exp, _ = parse_tagged(raw)
        partial, _ = restore(rev or "", n_markers)
        update(sug["id"], proposed_text=partial, status="incomplete" if partial else "failed", explanation=exp)
        yield {"type": "error", "message": str(e)[:300], "suggestion": get(sug["id"])}
        _cancel_events.pop(gen_id, None)
        return
    _cancel_events.pop(gen_id, None)

    raw = "".join(buf)
    rev, exp, complete = parse_tagged(raw)
    proposed, markers_ok = restore(rev or "", n_markers)
    proposed = proposed.strip()
    # Preserve the original's surrounding whitespace for sentence-level edits.
    if scope == "sentence":
        proposed = re.sub(r"\s+$", "", proposed)
    if not proposed:
        update(sug["id"], status="failed", explanation="The model returned no usable text.")
        yield {"type": "error", "message": "The model returned no usable text. Try again or choose another model.",
               "suggestion": get(sug["id"])}
        return
    full_context = "\n".join(filter(None, [ctx["prev"], current, ctx["next"]]))
    meaning, verification = assess(original, proposed, full_context)
    if not markers_ok:
        meaning["warnings"].insert(0, "The model did not keep the footnote/field markers in place; this version cannot be exported until fixed manually.")
        meaning["level"] = "possible_change"
        meaning["label"] = "Warning: protected elements were changed"
    status = "pending" if complete else "incomplete"
    if proposed == original:
        exp = (exp or "") + " (No changes were needed.)"
    update(sug["id"], proposed_text=proposed, explanation=exp, status=status, meaning=meaning, verification=verification)
    yield {"type": "completed", "suggestion": get(sug["id"]), "stats": stats}
