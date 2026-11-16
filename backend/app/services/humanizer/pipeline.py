"""The humanizer pipeline: a multi-stage rewriting engine.

    read → analyse → understand (context + semantic map)
         → for each requested variation:
             structure → vocabulary → style → grammar & flow → validate & repair → assemble

Each stage does real, separately observable work:

- **Understand** (stage A) builds a deterministic meaning ledger per block (qualifiers, negations,
  logical links, numbers) and, at Medium/Extensive, an AI semantic map of the points each block makes.
  Only points grounded in the text are kept. Both feed every later stage.
- **Structure** (B) is the main rewrite: sentence construction, paragraph organisation and flow, one
  model call per section with the previous section's revised tail and the next section's opening.
- **Vocabulary / Style / Grammar** (C–E) are *targeted* passes: a deterministic check finds concrete
  problems in each block, and only flagged blocks go to the model, with the exact edits to make. Every
  model edit must pass ``refine.guard`` or the block keeps its previous version.
- **Validate & repair** (F) checks the result against the original deterministically (and optionally
  with an AI comparison that sees the semantic map), sends blocks with meaning problems back for one
  targeted repair, re-checks them, and reverts any block whose objective problems remain.

Every stage event corresponds to work that actually ran; stages with nothing to do say so. The AI
detector is never consulted. Nothing is persisted.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.services.analysis.ai_writing_analysis_service import STOCK_TRANSITIONS
from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer import analysis, llm, prompts, refine, semantics, style_profiles, validation
from app.services.humanizer.blocks import Block, join_blocks, legend, parse_blocks, protect, restore
from app.services.ollama.ollama_service import GenerationCancelled, OllamaUnavailable, ollama

CHUNK_WORDS = 380  # words of rewritable prose per structural rewrite call
# Sections shorter than this are merged with a neighbour (across headings): every model call has a fixed
# overhead, so a five-word section costs almost as much time as a full one.
MIN_SECTION_WORDS = 120
# Small models tend to stop early when a section has many short blocks (e.g. a long bulleted list).
MAX_SECTION_BLOCKS = 12
# The meaning check compares original and revised text and replies with a short JSON list, so it can
# take several sections per call.
VALIDATION_WORDS = 700
VALIDATION_MAX_TOKENS = 1000
REFINE_BATCH_WORDS = 600  # words per targeted-refinement or repair call
REFINE_BATCH_BLOCKS = 8
PREV_CONTEXT_WORDS = 120
NEXT_CONTEXT_WORDS = 80
SHORT_TEXT_WORDS = 150  # below this the text itself is the context; no separate document-context call
MAX_FLOW_ITEMS = 4
MAX_INPUT_CHARS = 120_000
MAX_VARIATIONS = 3
BASE_TEMPERATURE = {"light": 0.3, "medium": 0.5, "extensive": 0.6}

STAGES = [
    ("reading", "Reading document"),
    ("analyzing", "Analyzing writing patterns"),
    ("context", "Understanding meaning & context"),
    ("rewriting", "Restructuring sentences & paragraphs"),
    ("vocabulary", "Refining vocabulary"),
    ("style", "Applying writing style"),
    ("grammar", "Grammar, readability & flow"),
    ("validating", "Validating & repairing meaning"),
    ("finalizing", "Preparing final output"),
]
VARIANT_STAGES = ("rewriting", "vocabulary", "style", "grammar", "validating", "finalizing")

# Flag types that a repair pass can act on, and those that are objective enough to revert a block for.
REPAIRABLE = {"number_or_date", "citation", "quotation", "link", "certainty", "missing_information", "terminology", "changed_claim",
              "argument", "added_information", "contradiction"}
OBJECTIVE = {"number_or_date", "citation", "quotation", "link"}


@dataclass
class Params:
    text: str
    mode: str = "natural"
    intensity: str = "medium"
    profile_id: str | None = None
    custom_instructions: str | None = None
    document_type: str | None = None
    audience: str | None = None
    ai_validation: bool = True
    speed: str = "fast"
    approaches: list[str] = field(default_factory=lambda: ["balanced"])


@dataclass
class Section:
    index: int
    block_ids: list[int]
    heading: str | None
    words: int


@dataclass
class State:
    blocks: list[Block]
    protected: dict[int, str] = field(default_factory=dict)
    mappings: dict[int, dict[str, str]] = field(default_factory=dict)
    revised: dict[int, str] = field(default_factory=dict)  # final (restored) text per block
    notes: list[dict] = field(default_factory=list)
    trail: dict[int, list[dict]] = field(default_factory=dict)  # per block: what each stage did
    report: dict[str, dict] = field(default_factory=dict)  # per stage: counts for the UI

    def cur(self, bid: int) -> str:
        return self.revised.get(bid, self.blocks[bid].text)

    def log(self, bid: int, stage: str, status: str, items: list[str] | None = None, reason: str | None = None) -> None:
        self.trail.setdefault(bid, []).append({"stage": stage, "status": status, "items": (items or [])[:8],
                                               **({"reason": reason} if reason else {})})


@dataclass
class Shared:
    """Everything computed once and shared by all variations."""
    blocks: list[Block]
    protected: dict[int, str]
    mappings: dict[int, dict[str, str]]
    rewritable: list[Block]
    sections: list[Section]
    profile: dict
    ctx: dict
    ledgers: dict[int, dict]
    smap: dict[int, dict]
    style_block: str | None
    profile_stats: dict | None
    context_text: str
    first_variant: dict[int, str] = field(default_factory=dict)  # variation 1's structural rewrite, shown to later variations for contrast


Emit = Callable[[dict], Any]


# ------------------------------------------------------------------ sections and helpers
def build_sections(blocks: list[Block]) -> list[Section]:
    sections: list[Section] = []
    cur: list[int] = []
    words = 0
    heading: str | None = None
    cur_heading: str | None = None

    def flush() -> None:
        nonlocal cur, words
        if cur:
            sections.append(Section(len(sections), cur, cur_heading, words))
        cur, words = [], 0

    for b in blocks:
        if b.kind == "heading":
            flush()
            heading = b.text.strip()
            cur_heading = heading
            continue
        if not b.rewritable:
            continue
        # Parts of one long paragraph stay together in order; a section boundary may fall between them.
        if cur and (words + b.words > CHUNK_WORDS or len(cur) >= MAX_SECTION_BLOCKS):
            flush()
            cur_heading = heading
        cur.append(b.id)
        words += b.words
    flush()
    return _merge_small(sections)


def _batches(ids: list[int], words: dict[int, int], max_words: int, max_blocks: int = 99) -> list[list[int]]:
    out: list[list[int]] = []
    total = 0
    for bid in ids:
        w = words[bid]
        if out and total + w <= max_words and len(out[-1]) < max_blocks:
            out[-1].append(bid)
            total += w
        else:
            out.append([bid])
            total = w
    return out


def _validation_batches(changed_ids: list[int], by_id: dict[int, Block]) -> list[list[int]]:
    """Group changed blocks for the AI meaning check: one call per ~VALIDATION_WORDS of original text."""
    return _batches(changed_ids, {b: by_id[b].words for b in changed_ids}, VALIDATION_WORDS)


def _merge_small(sections: list[Section]) -> list[Section]:
    out: list[Section] = []
    for sec in sections:
        prev = out[-1] if out else None
        if prev and (prev.words < MIN_SECTION_WORDS or sec.words < MIN_SECTION_WORDS) and prev.words + sec.words <= CHUNK_WORDS \
                and len(prev.block_ids) + len(sec.block_ids) <= MAX_SECTION_BLOCKS:
            heads = [h for h in (prev.heading, sec.heading) if h]
            prev.heading = " / ".join(dict.fromkeys(heads)) or None
            prev.block_ids += sec.block_ids
            prev.words += sec.words
        else:
            out.append(Section(len(out), list(sec.block_ids), sec.heading, sec.words))
    return out


def _tail_words(text: str, n: int) -> str:
    ws = text.split()
    return ("… " if len(ws) > n else "") + " ".join(ws[-n:])


def _head_words(text: str, n: int) -> str:
    ws = text.split()
    return " ".join(ws[:n]) + (" …" if len(ws) > n else "")


def _digest(blocks: list[Block], limit_words: int = 700) -> str:
    out, used = [], 0
    for b in blocks:
        if used >= limit_words:
            break
        if b.kind == "heading":
            out.append(f"# {b.text.strip()}")
            continue
        if not b.text.strip() or b.kind == "code":
            continue
        sents = [b.text[s:e] for s, e in split_sentences(b.text)][:2]
        piece = " ".join(sents)
        out.append(("- " if b.kind == "list_item" else "") + piece)
        used += len(WORD_RE.findall(piece))
    return "\n".join(out)


def _candidate_terms(blocks: list[Block]) -> list[str]:
    text = "\n".join(b.text for b in blocks if b.kind != "code")
    c: Counter = Counter()
    for m in re.finditer(r"\b[A-Z]{2,8}s?\b", text):
        c[m.group(0)] += 2
    for m in re.finditer(r"\b(?:[A-Z][a-z]+(?:[- ][A-Z][a-z]+)+)\b", text):
        c[m.group(0)] += 1
    for m in re.finditer(r"\b[a-z]+(?:-[a-z]+)+\b|\b\w*\d\w*[A-Za-z]\w*\b", text):
        c[m.group(0)] += 1
    return [t for t, k in c.most_common(30) if k >= 2 or t.isupper()][:20]


def _is_term(t: str) -> bool:
    """A key term worth keeping exactly: a name, acronym, number-bearing, hyphenated or multi-word term, or a long word.
    Everyday words ("time", "data") are not treated as terms that must survive verbatim."""
    t = t.strip()
    return bool(t) and (any(c.isupper() for c in t[1:]) or t[:1].isupper() and " " in t or any(c.isdigit() for c in t)
                        or "-" in t or "_" in t or " " in t or len(t) >= 9 or t.isupper())


def _parse_items(raw: str, ids: list[int]) -> dict[int, str]:
    """Block-tagged reply; for a single-block batch, an untagged reply (minus echoed labels) is accepted too."""
    got = llm.parse_blocks(raw)
    if len(ids) == 1 and len(got) == 1 and ids[0] not in got:
        return {ids[0]: next(iter(got.values()))}  # one block, wrong id: small models renumber
    if not got and len(ids) == 1:
        t = llm.strip_reasoning(raw).strip()
        t = re.sub(r"^\s*\[(?:Block|P)\s*\d+\]\s*", "", t, flags=re.I)
        t = re.sub(r"^\s*(?:(?:CORRECTED|FIXED)\s+)?(?:REVISION|REVISED|OUTPUT)\s*:\s*", "", t, flags=re.I)
        if "ORIGINAL:" in t:  # the model echoed the whole item; keep only the revision part
            m = re.search(r"REVISION\s*:\s*(.*?)(?:\n\s*Problems to fix:|$)", t, re.S | re.I)
            t = m.group(1).strip() if m else ""
        if t and not t.startswith(("{", "[", "<")):
            got = {ids[0]: t}
    return got


def _strip_list_marker(text: str) -> str:
    return re.sub(r"^\s*(?:[-*•‣◦]|\d{1,3}[.)])\s+", "", text)


def _with_ws(original: str, new: str) -> str:
    """Keep the block's original leading/trailing whitespace pattern."""
    lead = re.match(r"^\s*", original).group(0)
    trail = re.search(r"\s*$", original).group(0)
    return lead + new.strip() + trail


def _restore_candidate(st: State, bid: int, raw: str) -> tuple[str | None, str | None]:
    """Restore tokens in a model reply for one block. Returns (text, None) or (None, reason)."""
    b = st.blocks[bid]
    txt = raw.strip()
    if b.kind == "list_item":
        txt = _strip_list_marker(txt)
    if not txt:
        return None, "The model returned an empty block."
    restored, missing, extra = restore(txt, st.mappings.get(bid, {}))
    if missing or extra:
        what = ", ".join(sorted({prompts_kind(t) for t in missing + extra}))
        return None, f"The rewrite would have altered protected {what}; the previous version was kept."
    if "<b" in restored or "</b>" in restored:
        return None, "The model output was malformed; the previous version was kept."
    return _with_ws(b.text, restored), None


def _accept_rewrite(st: State, bid: int, raw: str, *, source: str) -> tuple[bool, str | None]:
    text, why = _restore_candidate(st, bid, raw)
    if text is None:
        return False, (why or "").replace("the previous version", "the original")
    st.revised[bid] = text
    return True, None


def _protected_ok(st: State, bid: int, raw: str) -> bool:
    _, missing, extra = restore(_strip_list_marker(raw.strip()), st.mappings.get(bid, {}))
    return not missing and not extra


def prompts_kind(tok: str) -> str:
    from app.services.humanizer.blocks import KIND_LABEL

    return KIND_LABEL.get(tok[1:2], "text") + "s"


# ------------------------------------------------------------------ runner
async def run(params: Params, cancel: asyncio.Event) -> AsyncIterator[dict]:
    """Run the pipeline, yielding events. Model activity is interleaved via a queue."""
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    async def emit(ev: dict) -> None:
        await queue.put(ev)

    async def worker() -> None:
        try:
            await _pipeline(params, emit, cancel)
        except GenerationCancelled:
            await queue.put({"type": "cancelled"})
        except (OllamaUnavailable, llm.LLMError) as e:
            await queue.put({"type": "error", "message": str(e)[:400], "code": "model_unavailable" if isinstance(e, OllamaUnavailable) else "model_failed"})
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("humanizer pipeline failed")
            await queue.put({"type": "error", "message": f"Unexpected error: {str(e)[:300]}", "code": "internal"})
        finally:
            await queue.put(done)

    task = asyncio.create_task(worker())
    try:
        while True:
            ev = await queue.get()
            if ev is done:
                break
            yield ev
    finally:
        if not task.done():
            cancel.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def _stage(emit: Emit, key: str, state: str, detail: str | None = None, **extra: Any) -> None:
    await emit({"type": "stage", "stage": key, "state": state, "detail": detail, "t": time.time(), **extra})


def _check(cancel: asyncio.Event) -> None:
    if cancel.is_set():
        raise GenerationCancelled()


def _approaches(p: Params) -> list[str]:
    out = [a for a in dict.fromkeys(p.approaches or ["balanced"]) if a in prompts.APPROACHES][:MAX_VARIATIONS]
    return out or ["balanced"]


async def _pipeline(p: Params, emit: Emit, cancel: asyncio.Event) -> None:
    t0 = time.time()
    llm.use_speed(p.speed)
    usage = llm.start_usage()
    ms = await llm.resolve_model()
    approaches = _approaches(p)
    await emit({"type": "started", "speed": ms["speed"], "fast_model": ms["fast_model"], "stages": [{"key": k, "label": l} for k, l in STAGES],
                "model": ms["model"], "provider": {"name": "Ollama", "url": ollama.base_url},
                "variants": [{"approach": a, "label": prompts.APPROACHES[a]["label"]} for a in approaches]})

    # ---------------------------------------------------------------- reading
    await _stage(emit, "reading", "active")
    blocks = parse_blocks(p.text)
    _mark_unrewritable(blocks)
    protected: dict[int, str] = {}
    mappings: dict[int, dict[str, str]] = {}
    counters: dict[str, int] = {}
    for b in blocks:
        if b.rewritable:
            protected[b.id], mappings[b.id] = protect(b.text, counters)
    rewritable = [b for b in blocks if b.rewritable]
    if not rewritable:
        await _stage(emit, "reading", "failed", "There is no prose to rewrite (only headings, quotations, code or very short lines).")
        await emit({"type": "error", "message": "There is no prose to rewrite in this text.", "code": "empty"})
        return
    kinds = Counter(b.kind for b in blocks)
    prot = Counter(tok[1] for m in mappings.values() for tok in m)
    detail = (f"{sum(b.words for b in rewritable)} words of prose in {len(rewritable)} block(s)"
              + (f", {kinds['heading']} heading(s)" if kinds["heading"] else "")
              + (f", {kinds['list_item']} list item(s)" if kinds["list_item"] else "")
              + (f". Protected exactly: {prot['C']} citation(s)" if prot["C"] else "")
              + (f", {prot['Q']} quotation(s)" if prot["Q"] else "")
              + (f", {prot['U'] + prot['L']} link(s)" if prot["U"] + prot["L"] else ""))
    await _stage(emit, "reading", "completed", detail,
                 blocks=[{"id": b.id, "kind": b.kind, "rewritable": b.rewritable, "words": b.words} for b in blocks])
    sections = build_sections(blocks)
    total_words = sum(b.words for b in rewritable)
    semantic_ai = p.intensity != "light"
    # The plan lets the UI show every section up front and estimate time from real per-call timings.
    await emit({"type": "plan", "sections": [{"index": s.index, "words": s.words, "heading": s.heading,
                                              "preview": _preview(by_text(blocks, s.block_ids[0]))} for s in sections],
                "words": total_words, "context_call": total_words >= SHORT_TEXT_WORDS,
                "semantic_calls": len(semantics.map_batches([b.id for b in rewritable], {b.id: b.words for b in rewritable})) if semantic_ai else 0,
                "flow": p.intensity != "light", "ai_validation": p.ai_validation, "variants": len(approaches)})
    # Fail fast (before spending time on analysis) if the model is missing.
    await ollama.ensure_model(ms["model"])
    _check(cancel)

    # ---------------------------------------------------------------- analysis
    await _stage(emit, "analyzing", "active")
    profile = await asyncio.to_thread(analysis.analyze, blocks)
    await emit({"type": "profile", "profile": profile})
    await _stage(emit, "analyzing", "completed",
                 f"{len(profile['issues'])} writing pattern(s) found" if profile["issues"] else "No notable problems found; light polishing only")
    _check(cancel)

    # ---------------------------------------------------------------- A: understanding
    await _stage(emit, "context", "active")
    terms = _candidate_terms(blocks)
    ctx: dict[str, Any] = {"document_type": p.document_type if p.document_type and p.document_type != "auto" else None,
                           "audience": p.audience or None, "key_terms": terms, "source": "automatic"}
    ctx_bits = []
    if total_words < SHORT_TEXT_WORDS:
        ctx_bits.append("short text: no separate document-context call")
    else:
        try:
            raw = await llm.complete(prompts.context_messages(_digest(blocks), doc_type=p.document_type, audience=p.audience, stats_terms=terms),
                                     temperature=0.1, cancel=cancel, on_activity=_activity(emit, "context"), label="context", stage="context",
                                     num_predict=_cap(ms, 700))
            parsed = llm.parse_json(raw)
            if isinstance(parsed, dict):
                for k in ("document_type", "audience", "purpose", "tone"):
                    if isinstance(parsed.get(k), str) and parsed[k].strip() and not ctx.get(k):
                        ctx[k] = parsed[k].strip()[:200]
                for k in ("main_points", "key_terms", "claims_to_preserve", "context_notes"):
                    if isinstance(parsed.get(k), list):
                        vals = [str(x).strip()[:200] for x in parsed[k] if str(x).strip()]
                        ctx[k] = list(dict.fromkeys((ctx.get(k) or []) + vals))[:20] if k == "key_terms" else vals[:6]
                ctx["source"] = "ai"
                # Only keep AI-suggested key terms that actually occur in the text.
                body = p.text.lower()
                ctx["key_terms"] = [t for t in ctx.get("key_terms", []) if t.lower() in body and _is_term(t)][:20]
                ctx_bits.append(_ctx_detail(ctx))
            else:
                ctx_bits.append("the context summary could not be read; automatic term detection used")
        except GenerationCancelled:
            raise
        except llm.LLMError as e:
            ctx_bits.append(f"context call failed ({e}); automatic term detection used")
    _check(cancel)
    ledgers = {b.id: semantics.ledger(protected[b.id]) for b in rewritable}
    smap: dict[int, dict] = {}
    context_text = prompts.describe_context(ctx)
    if semantic_ai:
        batches = semantics.map_batches([b.id for b in rewritable], {b.id: b.words for b in rewritable})
        dropped = 0
        try:
            for i, g in enumerate(batches):
                _check(cancel)
                await _stage(emit, "context", "active", f"Mapping the points each paragraph makes ({i + 1} of {len(batches)})",
                             progress={"done": i, "total": len(batches)})
                raw = await llm.complete(semantics.map_messages([(bid, protected[bid].strip()) for bid in g], context_text), temperature=0.1,
                                         cancel=cancel, on_activity=_activity(emit, "context", f"map {i + 1}/{len(batches)}"),
                                         label="semantic map", stage="semantic_map", num_predict=_cap(ms, 220 * len(g) + 200))
                got = semantics.parse_map(llm.parse_json(raw), {bid: protected[bid] for bid in g})
                smap.update(got)
                dropped += sum(v["dropped"] for v in got.values())
            n_points = sum(len(v["points"]) for v in smap.values())
            ctx_bits.append(f"{n_points} point(s) mapped across {len(smap)} block(s)" + (f"; {dropped} ungrounded point(s) discarded" if dropped else ""))
        except GenerationCancelled:
            raise
        except llm.LLMError as e:
            ctx_bits.append(f"semantic map unavailable ({e}); using the deterministic meaning ledger only")
    n_q = sum(len(l["qualifiers"]) + len(l["negations"]) for l in ledgers.values())
    n_links = sum(len(l["links"]) for l in ledgers.values())
    ctx_bits.append(f"{n_q} qualifier(s)/negation(s) and {n_links} logical link(s) recorded to keep")
    ctx["semantic_map"] = {str(k): v for k, v in smap.items()}
    ctx["ledger"] = {str(k): v for k, v in ledgers.items()}
    await _stage(emit, "context", "completed", _sentence_case("; ".join(b for b in ctx_bits if b)), context=ctx)

    style_block, profile_stats = None, None
    if p.profile_id:
        prof = style_profiles.get_profile(p.profile_id)
        if prof:
            style_block = style_profiles.prompt_block(prof)
            profile_stats = prof.get("stats") or None
    sh = Shared(blocks=blocks, protected=protected, mappings=mappings, rewritable=rewritable, sections=sections, profile=profile, ctx=ctx,
                ledgers=ledgers, smap=smap, style_block=style_block, profile_stats=profile_stats, context_text=context_text)

    # ---------------------------------------------------------------- B–F per variation
    variants = []
    for vi, approach in enumerate(approaches):
        _check(cancel)
        variants.append(await _variant(p, sh, vi, approach, len(approaches), emit, cancel, ms))

    first = variants[0]
    result = {
        "original": p.text,
        **{k: first[k] for k in ("output", "blocks", "metrics_after", "issues_after", "flags", "notes", "validation", "stage_report")},
        "profile_before": profile,
        "context": ctx,
        "variants": variants,
        "settings": {"mode": p.mode, "intensity": p.intensity, "profile_id": p.profile_id, "document_type": p.document_type,
                     "audience": p.audience, "custom_instructions": p.custom_instructions, "ai_validation": p.ai_validation,
                     "speed": p.speed, "approaches": approaches},
        "model": ms["model"],
        "duration_s": round(time.time() - t0, 1),
        "sections": len(sections),
        "usage": usage,
        "llm_calls": usage["calls"],
    }
    await emit({"type": "completed", "result": result})


def _cap(ms: dict, tokens: int) -> int | None:
    """An output cap for calls without a reasoning phase (reasoning tokens count towards num_predict)."""
    return int(tokens) if ms.get("think") is False or ms.get("fast_model") else None


async def _variant(p: Params, sh: Shared, vi: int, approach: str, n: int, emit: Emit, cancel: asyncio.Event, ms: dict) -> dict:
    blocks = sh.blocks
    by_id = {b.id: b for b in blocks}
    st = State(blocks=blocks, protected=sh.protected, mappings=sh.mappings)
    label = prompts.APPROACHES[approach]["label"]
    vtag = {"variant": vi, "variants": n, "approach": approach}
    pre = f"Variation {vi + 1} of {n} ({label}): " if n > 1 else ""

    async def stage(key: str, state: str, detail: str | None = None, **extra: Any) -> None:
        await _stage(emit, key, state, (pre + detail) if detail else (pre.rstrip(": ") or None), **vtag, **extra)

    if vi > 0:
        for k in VARIANT_STAGES:
            await _stage(emit, k, "pending", None, **vtag)

    # ---------------------------------------------------------------- B: structural rewrite
    sections = sh.sections
    await stage("rewriting", "active", f"0 of {len(sections)} section(s)", progress={"done": 0, "total": len(sections)})
    temperature = max(0.1, BASE_TEMPERATURE.get(p.intensity, 0.5) + prompts.APPROACHES[approach]["temperature"])
    for sec in sections:
        _check(cancel)
        prev_text = None
        first = sec.block_ids[0]
        prev_ids = [b.id for b in blocks[:first] if b.rewritable]
        if prev_ids:
            last = prev_ids[-1]
            prev_text = _tail_words(st.cur(last), PREV_CONTEXT_WORDS)
        nxt = next((b for b in blocks[sec.block_ids[-1] + 1:] if b.rewritable), None)
        next_text = _head_words(nxt.text, NEXT_CONTEXT_WORDS) if nxt else None
        guidance = analysis.guidance_for(sh.profile, sec.block_ids)

        def msgs_for(ids: list[int]) -> list[dict]:
            return prompts.rewrite_messages(
                mode=p.mode, intensity=p.intensity, custom_instructions=p.custom_instructions, style_block=sh.style_block,
                audience=p.audience, context_text=sh.context_text, guidance=guidance, section_heading=sec.heading,
                prev_text=prev_text, next_text=next_text, approach=approach, meaning_notes=_meaning_notes(sh, ids),
                other_version="\n".join(_reprotect(sh.first_variant[b], st.mappings[b])[0].strip() for b in ids if b in sh.first_variant) or None
                if vi > 0 else None,
                blocks=[(bid, by_id[bid].kind, st.protected[bid].strip()) for bid in ids],
                legend_text=legend({k: v for bid in ids for k, v in st.mappings[bid].items()}))

        msgs = msgs_for(sec.block_ids)
        lab = f"section {sec.index + 1}/{len(sections)}" + (f" · {label}" if n > 1 else "")
        cap = _cap(ms, sec.words * 3.2 + 400)
        raw = await llm.complete(msgs, temperature=temperature, cancel=cancel, on_activity=_activity(emit, "rewriting", lab, vtag), label=lab,
                                 stage="structure", num_predict=cap)
        got = llm.parse_blocks(raw)
        if not got and len(sec.block_ids) == 1 and raw.strip() and "<" not in raw.strip()[:3]:
            got = {sec.block_ids[0]: raw.strip()}
        if not got:
            # One retry with a reminder of the format; then keep originals.
            await emit({"type": "activity", "stage": "rewriting", "activity": "retrying", "label": lab, "reason": "unreadable output", **vtag})
            raw = await llm.complete(msgs + [{"role": "assistant", "content": raw[:2000]},
                                             {"role": "user", "content": "Your reply did not use the required format. Reply again with only <b id=\"N\">…</b> tags, one per block."}],
                                     temperature=0.3, cancel=cancel, on_activity=_activity(emit, "rewriting", lab, vtag), label=lab, stage="structure",
                                     num_predict=cap)
            got = llm.parse_blocks(raw)
        # Blocks the model skipped, or whose rewrite lost protected material, get one more attempt on their own.
        skipped = [bid for bid in sec.block_ids if bid not in got] if got else []
        damaged = [bid for bid in sec.block_ids if bid in got and not _protected_ok(st, bid, got[bid])]
        retry_ids = [bid for bid in sec.block_ids if bid in skipped or bid in damaged]
        if retry_ids:
            await emit({"type": "activity", "stage": "rewriting", "activity": "retrying", "label": lab, **vtag,
                        "reason": "a citation or quotation was altered" if damaged and not skipped else
                        f"{len(retry_ids)} block(s) were skipped or need another attempt"})
            toks = ", ".join(t for bid in damaged for t in st.mappings[bid])
            retry_msgs = msgs_for(retry_ids)
            if toks:
                retry_msgs[-1]["content"] += (f"\n\nImportant: your previous attempt changed protected text. Copy each of these tokens "
                                              f"exactly once, unchanged: {toks}.")
            if skipped:
                retry_msgs[-1]["content"] += "\n\nReturn every block listed above, each in its own <b id=\"N\">…</b> tag."
            again = llm.parse_blocks(await llm.complete(retry_msgs, temperature=0.3, cancel=cancel, stage="structure",
                                                        on_activity=_activity(emit, "rewriting", lab, vtag), label=lab, num_predict=cap))
            for bid in retry_ids:
                if bid in again and _protected_ok(st, bid, again[bid]):
                    got[bid] = again[bid]
        for bid in sec.block_ids:
            if bid not in got:
                st.notes.append({"block_id": bid, "type": "kept_original", "severity": "low", "source": "pipeline",
                                 "message": "The model did not return this block, so the original text was kept.",
                                 "original_excerpt": validation._excerpt(by_id[bid].text), "revised_excerpt": ""})
                st.log(bid, "structure", "rejected", reason="not returned by the model")
                continue
            ok, why = _accept_rewrite(st, bid, got[bid], source="rewrite")
            if ok:
                st.log(bid, "structure", "applied")
            else:
                st.notes.append({"block_id": bid, "type": "kept_original", "severity": "low", "source": "pipeline", "message": why,
                                 "original_excerpt": validation._excerpt(by_id[bid].text), "revised_excerpt": validation._excerpt(got[bid])})
                st.log(bid, "structure", "rejected", reason=why)
        await emit({"type": "section", "index": sec.index, "total": len(sections), "block_ids": sec.block_ids, **vtag,
                    "revised": {str(bid): st.revised[bid] for bid in sec.block_ids if bid in st.revised}})
        await stage("rewriting", "active", f"{sec.index + 1} of {len(sections)} section(s)", progress={"done": sec.index + 1, "total": len(sections)})
    if vi == 0:
        sh.first_variant = dict(st.revised)
    n_rev = len(st.revised)
    st.report["structure"] = {"blocks": len(sh.rewritable), "applied": n_rev, "rejected": len(sh.rewritable) - n_rev, "calls": len(sections)}
    await stage("rewriting", "completed", f"{n_rev} of {len(sh.rewritable)} block(s) revised in {len(sections)} section(s)",
                progress={"done": len(sections), "total": len(sections)})

    # ---------------------------------------------------------------- C: vocabulary
    _check(cancel)
    key_terms = sh.ctx.get("key_terms") or []

    def vocab(bid: int, text: str) -> list[refine.Finding]:
        return refine.vocabulary_findings(by_id[bid].text, text, intensity=p.intensity, mode=p.mode, key_terms=key_terms)

    await _refine_stage("vocabulary", p, sh, st, vocab, emit, cancel, stage, vtag)

    # ---------------------------------------------------------------- D: style
    _check(cancel)
    forms = [f for bid in st.revised if by_id[bid].words >= 40 and (f := analysis.formality_score(st.revised[bid])) is not None]
    doc_formality = statistics.median(forms) if len(forms) >= 3 else None

    def style(bid: int, text: str) -> list[refine.Finding]:
        out = refine.style_findings(by_id[bid].text, text, mode=p.mode, intensity=p.intensity, profile_stats=sh.profile_stats,
                                    doc_formality=doc_formality)
        if approach == "concise" and by_id[bid].words >= 30 and refine._wc(text) > 0.88 * by_id[bid].words:
            out.append(refine.Finding("concise", "This is the concise version: cut redundant words and phrases so it is noticeably shorter, "
                                                 "keeping every fact, qualifier and point."))
        return out

    await _refine_stage("style", p, sh, st, style, emit, cancel, stage, vtag)

    # ---------------------------------------------------------------- E: grammar, readability & flow
    _check(cancel)
    auto_n = 0
    for bid in list(st.revised):
        fixed, fixes = refine.auto_corrections(by_id[bid].text, st.revised[bid])
        if fixes and fixed.strip() and _count_high(by_id[bid].text, fixed) <= _count_high(by_id[bid].text, st.revised[bid]):
            st.revised[bid] = fixed
            st.log(bid, "grammar", "auto", fixes)
            auto_n += len(fixes)

    def gram(bid: int, text: str) -> list[refine.Finding]:
        return refine.grammar_findings(by_id[bid].text, text)

    await _refine_stage("grammar", p, sh, st, gram, emit, cancel, stage, vtag, extra_detail=(f"{auto_n} rule-based correction(s) applied" if auto_n else None),
                        keep_active=p.intensity != "light")
    if p.intensity != "light":
        await _flow(p, st, sections, by_id, emit, cancel, stage, vtag)

    # ---------------------------------------------------------------- F: validation & repair
    _check(cancel)
    flags, summary = await _validate_and_repair(p, sh, st, emit, cancel, stage, vtag, ms)

    # ---------------------------------------------------------------- assemble
    await stage("finalizing", "active")
    changed_ids = [b.id for b in sh.rewritable if st.cur(b.id).strip() != b.text.strip()]
    out_blocks = []
    for b in blocks:
        d = b.to_dict()
        if b.id in st.revised:
            d["text"] = st.revised[b.id]
        out_blocks.append(d)
    output = join_blocks(out_blocks)
    after_blocks = parse_blocks(output)
    _mark_unrewritable(after_blocks)
    after = await asyncio.to_thread(analysis.analyze, after_blocks)
    ow, nw = p.text.split(), output.split()
    change = round(1 - difflib.SequenceMatcher(a=ow, b=nw, autojunk=False).ratio(), 2)
    await stage("finalizing", "completed", f"{len(changed_ids)} of {len(sh.rewritable)} block(s) changed")
    return {
        "approach": approach,
        "label": label,
        "output": output,
        "blocks": [{"id": b.id, "kind": b.kind, "rewritable": b.rewritable, "original": b.text, "revised": st.cur(b.id),
                    "changed": b.id in changed_ids, "part_of": b.part_of, "prefix": b.prefix, "sep": b.sep,
                    "trail": st.trail.get(b.id, [])} for b in blocks],
        "metrics_after": after["metrics"],
        "issues_after": [{"id": i["id"], "label": i["label"], "severity": i["severity"]} for i in after["issues"]],
        "flags": flags,
        "notes": st.notes,
        "validation": summary,
        "stage_report": st.report,
        "change_ratio": change,
    }


def _meaning_notes(sh: Shared, ids: list[int]) -> list[str]:
    out = []
    for bid in ids:
        m = sh.smap.get(bid) or {}
        line = semantics.describe_ledger(bid, sh.ledgers.get(bid, {}), m.get("points"), m.get("role"))
        if line:
            out.append(line)
    return out


def _count_high(original: str, text: str) -> int:
    return sum(1 for f in validation.compare_block(None, original, text) if f["severity"] == "high")


async def _refine_stage(key: str, p: Params, sh: Shared, st: State, check: Callable[[int, str], list[refine.Finding]], emit: Emit,
                        cancel: asyncio.Event, stage: Callable, vtag: dict, *, extra_detail: str | None = None,
                        keep_active: bool = False) -> None:
    """Detect → targeted model edit on flagged blocks only → guarded acceptance."""
    by_id = {b.id: b for b in sh.blocks}
    await stage(key, "active", "Checking every revised block")
    findings = {bid: check(bid, st.revised[bid]) for bid in st.revised}
    flagged = [bid for bid, f in findings.items() if f]
    rep = {"blocks": len(st.revised), "flagged": len(flagged), "applied": 0, "rejected": 0, "calls": 0,
           "examples": [f.instruction for bid in flagged for f in findings[bid]][:6]}
    st.report[key] = rep
    if not flagged:
        msg = "No problems found" + (f"; {extra_detail}" if extra_detail else "")
        if not keep_active:
            await stage(key, "completed", msg)
        else:
            await stage(key, "active", msg)
        return
    batches = _batches(flagged, {b: by_id[b].words for b in flagged}, REFINE_BATCH_WORDS, REFINE_BATCH_BLOCKS)
    ms = await llm.resolve_model(tier="light")
    for i, g in enumerate(batches):
        _check(cancel)
        await stage(key, "active", f"Fixing {len(flagged)} flagged block(s) (call {i + 1} of {len(batches)})", progress={"done": i, "total": len(batches)})
        items = [(bid, _reprotect(st.revised[bid], st.mappings[bid])[0].strip(), [f.instruction for f in findings[bid]]) for bid in g]
        mapping = {k: v for bid in g for k, v in st.mappings[bid].items()}
        try:
            raw = await llm.complete(prompts.refine_messages(stage=key, mode=p.mode, custom_instructions=p.custom_instructions,
                                                             style_block=sh.style_block, items=items, legend_text=legend(mapping)),
                                     temperature=0.3, cancel=cancel, on_activity=_activity(emit, key, f"{key} {i + 1}/{len(batches)}", vtag),
                                     label=key, stage=key, tier="light", num_predict=_cap(ms, sum(by_id[b].words for b in g) * 3 + 300))
        except GenerationCancelled:
            raise
        except llm.LLMError as e:
            for bid in g:
                st.log(bid, key, "rejected", [f.instruction for f in findings[bid]], reason=f"model call failed: {e}")
            rep["rejected"] += len(g)
            continue
        rep["calls"] += 1
        got = _parse_items(raw, g)
        for bid in g:
            instr = [f.instruction for f in findings[bid]]
            before = st.revised[bid]
            if bid not in got:
                st.log(bid, key, "rejected", instr, reason="not returned by the model")
                rep["rejected"] += 1
                continue
            cand, why = _restore_candidate(st, bid, got[bid])
            if cand is None:
                st.log(bid, key, "rejected", instr, reason=why)
                rep["rejected"] += 1
                continue
            orig = by_id[bid].text
            after_f = check(bid, cand)
            new_vocab = _introduced(orig, cand) - _introduced(orig, before)
            reason = refine.guard(key, before, cand, n_findings_before=len(findings[bid]), n_findings_after=len(after_f),
                                  high_flags_before=_count_high(orig, before), high_flags_after=_count_high(orig, cand),
                                  introduced_vocab_after=max(0, new_vocab),
                                  may_split=any(f.kind in ("long_sentence", "sentence_length", "intensity") for f in findings[bid]))
            if not reason and _reverts_improvement(by_id[bid].text, before, cand, p.intensity):
                reason = "it moved the passage back towards the original wording"
            if reason:
                st.log(bid, key, "rejected", instr, reason=reason)
                rep["rejected"] += 1
            else:
                st.revised[bid] = cand
                st.log(bid, key, "applied", instr)
                rep["applied"] += 1
    detail = f"{rep['applied']} of {len(flagged)} flagged block(s) improved" + (f", {rep['rejected']} edit(s) rejected by the safety checks" if rep["rejected"] else "")
    if extra_detail:
        detail += f"; {extra_detail}"
    await stage(key, "active" if keep_active else "completed", detail, progress={"done": len(batches), "total": len(batches)})


def _reverts_improvement(original: str, before: str, cand: str, intensity: str) -> bool:
    """A targeted edit should fix specific problems, not quietly undo the rewrite (common with small models)."""
    return intensity != "light" and refine.similarity(original, cand) > refine.similarity(original, before) + 0.15


def _introduced(original: str, text: str) -> int:
    return sum(1 for w in refine.GENERIC_VOCAB if refine._count(text, w) > refine._count(original, w))


async def _flow(p: Params, st: State, sections: list[Section], by_id: dict[int, Block], emit: Emit, cancel: asyncio.Event,
                stage: Callable, vtag: dict) -> None:
    """Paragraph-to-paragraph flow (part of stage E): fix repetitive or abrupt paragraph openings."""
    prev_detail = st.report.get("grammar", {})
    base = f"{prev_detail.get('applied', 0)} grammar/readability fix(es)"
    items = _flow_candidates(st, sections)
    if not items:
        await stage("grammar", "completed", f"{base}; paragraph openings already vary")
        return
    mapping = {k: v for bid, *_ in items for k, v in st.mappings[bid].items()}
    prot_items = []
    for bid, prev, para, problem in items:
        pt, _ = _reprotect(para, st.mappings[bid])
        prot_items.append((bid, prev, pt, problem))
    fixed = 0
    try:
        await stage("grammar", "active", f"Smoothing {len(items)} paragraph opening(s)")
        raw = await llm.complete(prompts.flow_messages(mode=p.mode, items=prot_items, legend_text=legend(mapping)), temperature=0.4,
                                 cancel=cancel, on_activity=_activity(emit, "grammar", "flow", vtag), label="flow", stage="flow", tier="light")
        got = llm.parse_blocks(raw)
        for bid, _prev, _para, problem in items:
            if bid not in got:
                continue
            before = st.cur(bid)
            cand, _why = _restore_candidate(st, bid, got[bid])
            # Only accept if the body of the paragraph is essentially unchanged (only the opening was revised).
            if cand and refine.similarity(before, cand) >= 0.6 and _count_high(by_id[bid].text, cand) <= _count_high(by_id[bid].text, before):
                st.revised[bid] = cand
                st.log(bid, "grammar", "applied", [problem])
                fixed += 1
        st.report.setdefault("grammar", {})["flow_fixed"] = fixed
        await stage("grammar", "completed", f"{base}; smoothed the opening of {fixed} of {len(items)} paragraph(s)")
    except llm.LLMError as e:
        await stage("grammar", "completed", f"{base}; flow call failed ({e})", fallback=True)


async def _validate_and_repair(p: Params, sh: Shared, st: State, emit: Emit, cancel: asyncio.Event, stage: Callable, vtag: dict,
                               ms: dict) -> tuple[list[dict], dict]:
    blocks = sh.blocks
    by_id = {b.id: b for b in blocks}
    key_terms = sh.ctx.get("key_terms")
    await stage("validating", "active", "Running automated checks")

    def det_flags(bid: int) -> list[dict]:
        b = by_id[bid]
        neighbours = " ".join(x.text for x in blocks[max(0, bid - 1):bid + 2])
        fl = validation.compare_block(bid, b.text, st.cur(bid), context=neighbours, key_terms=key_terms)
        pts = (sh.smap.get(bid) or {}).get("points") or []
        for sent in validation.unsupported_sentences(b.text, st.cur(bid), context=neighbours):
            fl.append({"block_id": bid, "type": "added_information", "severity": "medium", "source": "automated",
                       "message": f"This may add something the original does not say: “{validation._excerpt(sent, 120)}”. "
                                  "Remove it or keep only what the original states.",
                       "original_excerpt": validation._excerpt(b.text), "revised_excerpt": validation._excerpt(sent), "sentence": sent})
        for pt in validation.point_coverage(pts, st.cur(bid)):
            fl.append({"block_id": bid, "type": "missing_information", "severity": "medium", "source": "automated",
                       "message": f"This point from the original may be missing: “{pt}”.", "original_excerpt": validation._excerpt(b.text),
                       "revised_excerpt": validation._excerpt(st.cur(bid)), "point": pt})
        return fl

    def changed() -> list[int]:
        return [b.id for b in sh.rewritable if st.cur(b.id).strip() != b.text.strip()]

    det: dict[int, list[dict]] = {bid: det_flags(bid) for bid in changed()}
    ai: dict[int, list[dict]] = {}
    ai_checked, ai_note = False, None

    async def ai_check(ids: list[int], tag: str) -> bool:
        groups = _validation_batches(ids, by_id)
        for i, g in enumerate(groups):
            _check(cancel)
            await stage("validating", "active", f"AI meaning comparison{tag} {i + 1} of {len(groups)}", progress={"done": i, "total": len(groups)})
            pairs = [(bid, by_id[bid].text.strip(), st.cur(bid).strip()) for bid in g]
            pts = {bid: (sh.smap.get(bid) or {}).get("points") or [] for bid in g}
            raw = await llm.complete(prompts.validation_messages(pairs, pts), temperature=0.0, cancel=cancel, num_predict=VALIDATION_MAX_TOKENS,
                                     on_activity=_activity(emit, "validating", f"check {i + 1}/{len(groups)}", vtag), label="validation", stage="validation")
            found = validation.ai_flags(llm.parse_issues(raw), set(g), {bid: by_id[bid].text for bid in g}, {bid: st.cur(bid) for bid in g})
            for bid in g:
                ai[bid] = [f for f in found if f["block_id"] == bid]
        return True

    if p.ai_validation and det:
        try:
            ai_checked = await ai_check(list(det), "")
        except GenerationCancelled:
            raise
        except llm.LLMError as e:
            ai_note = f"The AI comparison stopped early: {e}"
    elif not p.ai_validation:
        ai_note = "AI meaning comparison was turned off; only automated checks ran."

    # ---- repair: one targeted attempt per block with meaning problems
    def problems(bid: int) -> list[str]:
        # Repair is triggered by objective or specific findings only: high-severity automated flags, points that went
        # missing, sentences that add content, and AI findings that are high or grounded. Broad medium flags
        # (a moved negation, a length change) are left for the author to judge rather than "fixed" by the model.
        out = [f["message"] for f in det.get(bid, []) if f["type"] in REPAIRABLE and
               (f["severity"] == "high" or f.get("point") or f.get("sentence") or "qualifier was dropped" in f["message"])]
        out += [f"{f['message']} (original: “{f['original_excerpt']}”)" for f in ai.get(bid, [])
                if f["type"] in REPAIRABLE and (f["severity"] == "high" or (f["severity"] == "medium" and f.get("grounded")))]
        return list(dict.fromkeys(out))[:6]

    todo = {bid: pr for bid in det if (pr := problems(bid))}
    rep = {"checked": len(sh.rewritable), "changed": len(det), "repair_attempted": len(todo), "repaired": 0, "reverted": 0, "calls": 0}
    st.report["validation"] = rep
    repaired: list[int] = []
    if todo:
        batches = _batches(list(todo), {b: by_id[b].words * 2 for b in todo}, REFINE_BATCH_WORDS * 2, REFINE_BATCH_BLOCKS)
        for i, g in enumerate(batches):
            _check(cancel)
            await stage("validating", "active", f"Repairing {len(todo)} block(s) with meaning problems (call {i + 1} of {len(batches)})")
            items = [(bid, st.protected[bid].strip(), _reprotect(st.cur(bid), st.mappings[bid])[0].strip(), todo[bid]) for bid in g]
            mapping = {k: v for bid in g for k, v in st.mappings[bid].items()}
            try:
                raw = await llm.complete(prompts.repair_messages(mode=p.mode, items=items, legend_text=legend(mapping)), temperature=0.2,
                                         cancel=cancel, on_activity=_activity(emit, "validating", f"repair {i + 1}/{len(batches)}", vtag),
                                         label="repair", stage="repair", num_predict=_cap(ms, sum(by_id[b].words for b in g) * 3 + 300))
            except GenerationCancelled:
                raise
            except llm.LLMError as e:
                for bid in g:
                    st.log(bid, "repair", "rejected", todo[bid], reason=f"model call failed: {e}")
                continue
            rep["calls"] += 1
            got = _parse_items(raw, g)
            for bid in g:
                before = st.cur(bid)
                orig = by_id[bid].text
                cand, why = _restore_candidate(st, bid, got[bid]) if bid in got else (None, "not returned by the model")
                if cand is None:
                    st.log(bid, "repair", "rejected", todo[bid], reason=why)
                    continue
                # A repair often restores a sentence together with its stock opener ("Moreover, …"); drop the opener.
                cand = refine.strip_stock_openers(cand)
                st.revised[bid] = cand
                new_det = det_flags(bid)
                old_auto = [f for f in det[bid] if f["severity"] in ("high", "medium") and f["type"] in REPAIRABLE]
                new_auto = [f for f in new_det if f["severity"] in ("high", "medium") and f["type"] in REPAIRABLE]
                reason = refine.guard("repair", before, cand, n_findings_before=len(old_auto), n_findings_after=len(new_auto),
                                      high_flags_before=_count_high(orig, before), high_flags_after=_count_high(orig, cand),
                                      introduced_vocab_after=max(0, _introduced(orig, cand) - _introduced(orig, before)))
                if not reason and old_auto and len(new_auto) >= len(old_auto) and not ai.get(bid):
                    reason = "the automated checks still found the same problems"
                stock_note = refine.stock_load(orig, cand) > refine.stock_load(orig, before)
                if reason:
                    st.revised[bid] = before
                    st.log(bid, "repair", "rejected", todo[bid], reason=reason)
                    continue
                det[bid] = new_det
                repaired.append(bid)
                # Meaning comes first: a repair that fixes meaning is kept even if it brings back some stock wording.
                st.log(bid, "repair", "applied", todo[bid], reason="restored some stock wording along with the meaning" if stock_note else None)
                st.notes.append({"block_id": bid, "type": "repaired", "severity": "low", "source": "pipeline",
                                 "message": "Corrected after validation: " + "; ".join(x.split(" (original:")[0] for x in todo[bid][:3]),
                                 "original_excerpt": validation._excerpt(orig), "revised_excerpt": validation._excerpt(cand)})
        rep["repaired"] = len(repaired)
        # Re-verify repaired blocks with the AI comparison too, so their AI flags reflect the corrected text.
        recheck = [bid for bid in repaired if bid in ai]
        if recheck and ai_checked:
            try:
                await ai_check(recheck, " (re-checking repairs)")
            except GenerationCancelled:
                raise
            except llm.LLMError:
                for bid in recheck:
                    for f in ai.get(bid, []):
                        f["message"] += " (flagged before an automatic repair; check it)"

    # ---- revert blocks whose objective problems remain: a correct original beats an improved but wrong rewrite
    for bid in list(det):
        # High-severity certainty findings are dropped approximations ("roughly"), possibilities ("may", "could"),
        # "not necessarily", or a negation lost without a compensating one: overstatements, so they count as objective.
        bad = [f for f in det[bid] if f["severity"] == "high" and (f["type"] in OBJECTIVE or f["type"] == "certainty")]
        if bad:
            st.revised[bid] = by_id[bid].text
            st.log(bid, "repair", "reverted", [f["message"] for f in bad])
            st.notes.append({"block_id": bid, "type": "kept_original", "severity": "low", "source": "pipeline",
                             "message": "Kept as written: the rewrite changed " + "; ".join(f["message"].rstrip(".") for f in bad[:2]).lower()
                                        + ", and the repair attempt did not fix it.",
                             "original_excerpt": validation._excerpt(by_id[bid].text), "revised_excerpt": ""})
            det.pop(bid)
            ai.pop(bid, None)
            rep["reverted"] += 1

    flags = [f for bid in det for f in det[bid]] + [f for bid in ai for f in ai[bid] if bid in det]
    flags.sort(key=lambda f: validation.SEVERITY[f["severity"]])
    for i, f in enumerate(flags):
        f["id"] = f"f{i}"
    summary = validation.summarize(flags, checked=len(sh.rewritable), changed=len(changed()), ai_checked=ai_checked, ai_note=ai_note)
    summary.update({"repaired": rep["repaired"], "reverted": rep["reverted"], "repair_attempted": rep["repair_attempted"]})
    bits = []
    if rep["repair_attempted"]:
        bits.append(f"{rep['repaired']} of {rep['repair_attempted']} block(s) repaired")
    if rep["reverted"]:
        bits.append(f"{rep['reverted']} kept as written")
    bits.append(f"{len(flags)} item(s) left for your review" if flags else "no remaining problems found by the checks")
    await stage("validating", "completed", _sentence_case("; ".join(bits)))
    return flags, summary


def _sentence_case(t: str) -> str:
    """Upper-case the first letter only (str.capitalize would lower-case names and acronyms such as "TTL")."""
    return t[:1].upper() + t[1:]


def _ctx_detail(ctx: dict) -> str:
    bits = [ctx.get("document_type"), ctx.get("audience") and f"for {ctx['audience']}", ctx.get("tone") and f"tone: {ctx['tone']}"]
    s = ", ".join(b for b in bits if b)
    n = len(ctx.get("key_terms") or [])
    return (s[:1].upper() + s[1:] if s else "Context identified") + (f"; {n} term(s) to keep exactly" if n else "")


def _activity(emit: Emit, stage: str, label: str | None = None, extra: dict | None = None):
    async def cb(ev: dict) -> None:
        await emit({"type": "activity", "stage": stage, **ev, **({"label": label} if label else {}), **(extra or {})})
    return cb


def _mark_unrewritable(blocks: list[Block]) -> None:
    """Line-structured text (addresses, signatures, poetry) and tiny fragments are kept as written."""
    for b in blocks:
        if not b.rewritable:
            continue
        lines = [l for l in b.text.split("\n") if l.strip()]
        if len(lines) >= 2 and sum(len(l) for l in lines) / len(lines) < 45:
            b.rewritable = False
            b.meta["kept"] = "line-structured text"
        elif b.words <= 3 or (b.words < 6 and not re.search(r"[.!?]\s*$", b.text.strip())):
            b.rewritable = False
            b.meta["kept"] = "short fragment or label"


def _reprotect(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    out = text
    n = 0
    for tok, val in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        if val in out:
            out = out.replace(val, tok, 1)
            n += 1
    return out, n


def _preview(text: str, n: int = 12) -> str:
    words = text.split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def by_text(blocks: list[Block], bid: int) -> str:
    return next((b.text for b in blocks if b.id == bid), "")


def _first_words(text: str, n: int) -> str:
    return " ".join(w.lower() for w in WORD_RE.findall(text)[:n])


def _flow_candidates(st: State, sections: list[Section]) -> list[tuple[int, str, str, str]]:
    """Paragraphs whose openings repeat the previous paragraph's opening or start with a stock transition at a section seam."""
    paras = [b for b in st.blocks if b.rewritable and b.kind == "paragraph" and b.part_of is None]
    seams = {sec.block_ids[0] for sec in sections[1:]}
    out = []
    for prev, cur in zip(paras, paras[1:]):
        pt = st.revised.get(prev.id, prev.text)
        ct = st.revised.get(cur.id, cur.text)
        problem = None
        fp, fc = _first_words(pt, 2), _first_words(ct, 2)
        low = ct.strip().lower()
        stock = next((t for t in STOCK_TRANSITIONS if low.startswith(t + ",") or low.startswith(t + " ")), None)
        if fp and fp == fc:
            problem = f"Starts with the same words as the previous paragraph (“{fc}”)."
        elif stock and _first_words(pt, 1) == _first_words(ct, 1):
            problem = f"Repeats the previous paragraph's opening transition (“{stock}”)."
        elif stock and cur.id in seams:
            problem = f"Opens with a stock transition (“{stock}”) where two separately revised sections meet."
        if problem:
            out.append((cur.id, _tail_words(pt, 60), ct.strip(), problem))
        if len(out) >= MAX_FLOW_ITEMS:
            break
    return out
