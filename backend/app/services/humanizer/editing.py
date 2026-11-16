"""Sentence/paragraph-level rewriting and style-profile generation."""

from __future__ import annotations

import asyncio
import re
from typing import AsyncIterator

from app.services.humanizer import llm, prompts, style_profiles, validation
from app.services.humanizer.blocks import legend, protect, restore
from app.services.ollama.ollama_service import GenerationCancelled, OllamaUnavailable
from app.services.writing.prompts import parse_tagged

MAX_SELECTION_CHARS = 6000
CONTEXT_CHARS = 700


async def rewrite_selection(*, text: str, before: str, after: str, action: str, mode: str, custom_instruction: str | None,
                            profile_id: str | None, audience: str | None, cancel: asyncio.Event, tone: str | None = None,
                            alternatives: int = 1) -> AsyncIterator[dict]:
    """Yield: started, activity…, completed{proposed, alternatives[], explanation, flags} | error | cancelled.

    With ``alternatives`` > 1 the model writes several genuinely different versions in one call (cheaper than
    one call each); every alternative is restored and validated separately, and unusable ones are dropped."""
    lead = re.match(r"^\s*", text).group(0)
    trail = re.search(r"\s*$", text).group(0)
    core = text.strip()
    protected, mapping = protect(core)
    style_block = None
    if profile_id:
        prof = style_profiles.get_profile(profile_id)
        if prof:
            style_block = style_profiles.prompt_block(prof)
    alternatives = max(1, min(3, alternatives))
    msgs = prompts.selection_messages(action=action, text=protected, before=before[-CONTEXT_CHARS:], after=after[:CONTEXT_CHARS], mode=mode,
                                      custom_instruction=custom_instruction, style_block=style_block, audience=audience,
                                      legend_text=legend(mapping), tone=tone, alternatives=alternatives)
    queue: asyncio.Queue = asyncio.Queue()

    async def on_activity(ev: dict) -> None:
        await queue.put({"type": "activity", **ev})

    words = len(core.split())
    ms = await llm.resolve_model()
    cap = int(words * (4.5 if action == "expand" else 2.6) * alternatives + 300) if (ms.get("think") is False or ms.get("fast_model")) else None

    async def work() -> str:
        temp = 0.7 if alternatives > 1 else 0.6 if action in ("rephrase", "natural") else 0.45
        return await llm.complete(msgs, temperature=temp, cancel=cancel, on_activity=on_activity, num_predict=cap,
                                  label=prompts.SELECTION_LABELS.get(action, action), stage="selection")

    yield {"type": "started", "model": ms["model"]}
    task = asyncio.create_task(work())
    try:
        while not task.done():
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=0.25)
                yield ev
            except asyncio.TimeoutError:
                continue
        while not queue.empty():
            yield queue.get_nowait()
        raw = task.result()
    except GenerationCancelled:
        yield {"type": "cancelled"}
        return
    except (OllamaUnavailable, llm.LLMError) as e:
        yield {"type": "error", "message": str(e)[:400]}
        return
    finally:
        if not task.done():
            cancel.set()
            task.cancel()
    revisions = llm.parse_revisions(raw)
    if not revisions:
        rev, _e, _c = parse_tagged(raw)
        revisions = [rev.strip()] if rev and rev.strip() else []
    _r, explanation, _complete = parse_tagged(raw)
    if not revisions:
        yield {"type": "error", "message": "The model returned no usable text. Try again."}
        return
    context = before[-CONTEXT_CHARS:] + " " + after[:CONTEXT_CHARS]
    options, rejected = [], []
    for rev in revisions[:alternatives]:
        restored, missing, extra = restore(rev, mapping)
        if missing or extra:
            rejected.append(rev)
            continue
        if any(restored.strip() == o["text"].strip() for o in options):
            continue
        flags = validation.compare_block(None, core, restored, context=context)
        if action != "expand":  # "expand" may legitimately elaborate; it is told to use only what the text supports
            for sent in validation.unsupported_sentences(core, restored, context=context):
                flags.append({"block_id": None, "type": "added_information", "severity": "medium", "source": "automated",
                              "message": f"This may add something the original does not say: “{validation._excerpt(sent, 120)}”.",
                              "original_excerpt": validation._excerpt(core), "revised_excerpt": validation._excerpt(sent)})
        options.append({"text": lead + restored.strip() + trail, "flags": flags})
    if not options:
        flags = [{"type": "citation", "severity": "high", "source": "automated",
                  "message": "The model altered a protected citation, quotation or link, so this version was not used.",
                  "original_excerpt": core[:160], "revised_excerpt": (rejected or [""])[0][:160]}]
        yield {"type": "error", "message": "The model altered a protected citation, quotation or link. Try again.", "flags": flags}
        return
    # Prefer alternatives with fewer high-severity meaning flags; keep the model's order otherwise.
    options.sort(key=lambda o: (sum(1 for f in o["flags"] if f["severity"] == "high"),
                                sum(1 for f in o["flags"] if f["type"] == "added_information")))
    yield {"type": "completed", "original": text, "proposed": options[0]["text"], "flags": options[0]["flags"], "alternatives": options,
           "explanation": explanation, "action": action, "tone": tone, "dropped": len(rejected)}


async def build_style_profile(samples: str, cancel: asyncio.Event) -> dict:
    """Analyse samples (statistics always, plus an AI description when the model is available)."""
    samples = samples[:style_profiles.MAX_SAMPLE_CHARS]
    stats = style_profiles.sample_stats(samples)
    if stats["words"] < style_profiles.MIN_SAMPLE_WORDS:
        raise ValueError(f"Add at least {style_profiles.MIN_SAMPLE_WORDS} words of your writing (you have {stats['words']}).")
    lines = style_profiles.describe_stats(stats)
    expressions = style_profiles.habitual_expressions(samples)
    description, traits, ai_used, ai_error = "", [], False, None
    excerpt = samples[:6000]
    try:
        raw = await llm.complete(prompts.style_messages(lines, excerpt), temperature=0.2, cancel=cancel, label="style")
        parsed = llm.parse_json(raw)
        if isinstance(parsed, dict):
            description = str(parsed.get("description") or "").strip()
            if parsed.get("tone"):
                description = (description + f" Tone: {str(parsed['tone']).strip()}.").strip()
            traits = [str(t).strip() for t in (parsed.get("traits") or []) if str(t).strip()]
            traits += [f"Avoids: {str(t).strip()}" for t in (parsed.get("avoid") or []) if str(t).strip()][:4]
            # Guard against copying: drop any trait that reproduces a long run of words from the samples.
            traits = [t for t in traits if not _copies(t, samples)]
            if _copies(description, samples):
                description = ""
            ai_used = True
    except GenerationCancelled:
        raise
    except (OllamaUnavailable, llm.LLMError) as e:
        ai_error = str(e)[:300]
    if not description:
        description = " ".join(lines[:3])
    return {"stats": stats, "summary": lines, "expressions": expressions, "description": description, "traits": traits[:12],
            "ai_used": ai_used, "ai_error": ai_error, "sample_words": stats["words"]}


def _copies(text: str, samples: str, n: int = 8) -> bool:
    words = re.findall(r"\w+", text.lower())
    sw = " " + " ".join(re.findall(r"\w+", samples.lower())) + " "
    for i in range(len(words) - n + 1):
        if " " + " ".join(words[i:i + n]) + " " in sw:
            return True
    return False
