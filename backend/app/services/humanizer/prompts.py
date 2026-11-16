"""Prompts for the humanizer pipeline.

Objective: clear, natural, coherent writing that keeps the author's meaning. The prompts never
mention AI detectors and never ask the model to degrade quality or add deliberate errors.
"""

from __future__ import annotations

MODES: dict[str, dict] = {
    "natural": {"label": "Natural", "hint": "Clear, fluent everyday writing",
                "prompt": "Write clear, fluent, everyday prose with natural sentence construction. Avoid stiff or unnecessarily formal language."},
    "conversational": {"label": "Conversational", "hint": "Relaxed and direct",
                       "prompt": "Use a relaxed, conversational register: contractions where natural, direct address where it fits, plain transitions. Stay accurate and avoid slang that the context does not support."},
    "academic": {"label": "Academic", "hint": "Precise, readable scholarship",
                 "prompt": "Keep academic precision and an objective register. Improve readability and flow. Preserve technical vocabulary, hedging that reflects real uncertainty, citations and the structure of every argument."},
    "professional": {"label": "Professional", "hint": "Clear workplace communication",
                     "prompt": "Write clear professional communication: direct, courteous and specific. Remove corporate jargon, buzzwords and unnecessary formality."},
    "simple": {"label": "Simple English", "hint": "Accessible vocabulary",
               "prompt": "Use simple, accessible English: common words, short sentences, one idea per sentence. Break up complicated sentences. Keep technical terms that are needed but make the surrounding sentence easy."},
    "personal": {"label": "Personal", "hint": "Individual, less generic voice",
                 "prompt": "Give the writing an individual voice: natural variation in sentence construction, concrete wording and fewer generic phrases. Do not invent personal anecdotes, opinions or experiences that are not in the text."},
    "custom": {"label": "Custom", "hint": "Describe the style you want",
               "prompt": "Follow the author's custom style instructions below."},
}

INTENSITIES: dict[str, dict] = {
    "light": {"label": "Light", "hint": "Minimal changes",
              "prompt": "LIGHT edit: keep most of the original sentence structure and wording. Fix awkward phrasing, grammar and obvious filler only. Do not reorder sentences."},
    "medium": {"label": "Medium", "hint": "Better sentences and transitions",
               "prompt": "MEDIUM edit: improve sentence construction, remove redundancy and filler, improve transitions, and vary rhythm. You may combine, split or reorder sentences inside a block when it helps."},
    "extensive": {"label": "Extensive", "hint": "Rework structure for flow",
                  "prompt": "EXTENSIVE edit: rework sentence and paragraph structure where necessary to improve overall flow, clarity and readability. Keep every argument, claim and piece of information; keep the author's intended meaning."},
}

# Distinct rewriting approaches for multiple variations. Each is a different *legitimate* way an editor could
# revise the same text; all keep the meaning. "balanced" is the default single-version approach.
APPROACHES: dict[str, dict] = {
    "balanced": {"label": "Balanced", "hint": "Better sentences, close to your structure", "temperature": 0.0,
                 "prompt": ""},
    "restructured": {"label": "Restructured", "hint": "Main point first, sentences reorganised", "temperature": 0.15,
                     "prompt": "Approach for this version: lead each paragraph with its main point, then supporting detail. Reorder, "
                               "combine or split sentences within a paragraph wherever it makes the logic easier to follow."},
    "concise": {"label": "Concise", "hint": "Tightest version, every point kept", "temperature": -0.1,
                "prompt": "Approach for this version: make it as tight as possible. Cut redundancy, throat-clearing and filler; prefer "
                          "short, direct sentences; use the fewest words that still keep every fact, qualifier and argument."},
    "flowing": {"label": "Flowing", "hint": "Connected, varied rhythm", "temperature": 0.1,
                "prompt": "Approach for this version: make the prose read smoothly as connected reasoning. Show how ideas relate "
                          "(because, so, but, which means) instead of listing them, and vary sentence length and openings."},
}

DOCUMENT_TYPES = ["auto", "essay", "academic paper", "report", "email", "blog post", "article", "technical documentation",
                  "cover letter", "story", "notes", "other"]

SELECTION_ACTIONS: dict[str, str] = {
    "improve": "Improve the writing: clearer, more fluent and better constructed.",
    "natural": "Make it sound more natural, the way a thoughtful person would write it.",
    "simplify": "Simplify: plainer words and simpler sentences, same meaning.",
    "professional": "Make it more professional: direct, courteous, no jargon.",
    "conversational": "Make it more conversational and relaxed while staying accurate.",
    "clarity": "Improve clarity so the point is easy to understand on first reading.",
    "redundancy": "Remove redundancy and filler without losing information.",
    "rephrase": "Rephrase with different sentence construction while keeping the meaning.",
    "expand": "Expand the explanation using only information and reasoning already present in the text and its context. Do not add new facts, data, examples or sources.",
    "shorten": "Shorten it substantially while keeping every essential point.",
    "flow": "Improve the flow: smoother transitions between sentences and from the text before it, varied sentence openings and rhythm, "
            "logical order. Keep the content.",
    "tone": "Change the tone as requested while keeping every fact and claim.",
    "custom": "Follow the user's instruction.",
}
TONES = {
    "formal": "formal and precise", "friendly": "warm and friendly", "confident": "confident and direct (without overstating any claim)",
    "neutral": "neutral and objective", "persuasive": "persuasive, using only the arguments already present",
    "empathetic": "empathetic and considerate", "casual": "casual and relaxed",
}
SELECTION_LABELS = {
    "improve": "Improve writing", "natural": "Make more natural", "simplify": "Simplify", "professional": "More professional",
    "conversational": "More conversational", "clarity": "Improve clarity", "redundancy": "Remove redundancy", "rephrase": "Rephrase",
    "expand": "Expand explanation", "shorten": "Shorten", "flow": "Improve flow", "tone": "Change tone", "custom": "Custom instruction",
}

CORE_RULES = """Non-negotiable rules:
- Preserve meaning. Keep every fact, claim, argument, number, date, name, technical term and the strength of each claim (do not add or remove hedges like "may", "can", "might", "always", "not": if the original says something "runs" or "is", the revision must not say it "can run" or "may be").
- Never invent facts, examples, analogies, metaphors, statistics, quotations, sources or citations, and never add claims or commentary the original does not make.
- Keep formulas, code, symbols and notation exactly as written (for example "->", "<=", "X ->> Y", SQL keywords and relation schemas); do not convert them to other characters.
- Tokens such as ⟦C1⟧ (citation), ⟦Q1⟧ (quotation), ⟦U1⟧ (link), ⟦L1⟧ (e-mail) and ⟦K1⟧ (code) stand for exact text that must not change. Keep every token exactly once, unchanged, in a sensible position.
- Do not intentionally introduce errors, typos or awkwardness. The result must be correct, polished, natural writing.
- Avoid stock phrases ("in today's fast-paced world", "it is important to note", "plays a crucial role", "delve into"), filler, stacked transitions ("Furthermore", "Moreover", "Additionally") and synonym-swapping for its own sake. Prefer plain, specific wording.
- Keep the language and spelling variety (e.g. British or American English) of the original."""


def context_messages(digest: str, *, doc_type: str | None, audience: str | None, stats_terms: list[str]) -> list[dict]:
    system = ("You analyse documents for an editor before they are revised. Read the excerpt and describe what the author is communicating. "
              "Be factual and brief. Respond with one JSON object only, no prose.")
    given = []
    if doc_type and doc_type != "auto":
        given.append(f"The author says the document type is: {doc_type}.")
    if audience:
        given.append(f"The author says the audience is: {audience}.")
    schema = ('{"document_type": "...", "audience": "...", "purpose": "one sentence", "tone": "a few words", '
              '"main_points": ["..."], "key_terms": ["technical terms and names that must stay exactly"], '
              '"claims_to_preserve": ["specific claims, figures or commitments whose exact meaning matters"], '
              '"context_notes": ["expressions whose meaning depends on context, or anything an editor must not change"]}')
    user = (("\n".join(given) + "\n\n") if given else "") + \
        (f"Candidate terms found automatically: {', '.join(stats_terms[:25])}\n\n" if stats_terms else "") + \
        f"Excerpt (headings and opening sentences of each paragraph):\n<excerpt>\n{digest}\n</excerpt>\n\n" \
        f"Return JSON in exactly this shape (at most 6 items per list):\n{schema}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def describe_context(ctx: dict) -> str:
    parts = []
    for key, label in (("document_type", "Document type"), ("audience", "Audience"), ("purpose", "Purpose"), ("tone", "Original tone")):
        if ctx.get(key):
            parts.append(f"{label}: {ctx[key]}")
    if ctx.get("main_points"):
        parts.append("Main points: " + "; ".join(ctx["main_points"][:6]))
    if ctx.get("key_terms"):
        parts.append("Terms to keep exactly: " + ", ".join(ctx["key_terms"][:20]))
    if ctx.get("claims_to_preserve"):
        parts.append("Claims whose exact meaning must be preserved: " + "; ".join(ctx["claims_to_preserve"][:6]))
    if ctx.get("context_notes"):
        parts.append("Notes: " + "; ".join(ctx["context_notes"][:4]))
    return "\n".join(parts)


def rewrite_messages(*, mode: str, intensity: str, custom_instructions: str | None, style_block: str | None, audience: str | None,
                     context_text: str, guidance: list[str], section_heading: str | None, prev_text: str | None,
                     next_text: str | None, blocks: list[tuple[int, str, str]], legend_text: str, approach: str = "balanced",
                     meaning_notes: list[str] | None = None, other_version: str | None = None) -> list[dict]:
    """``blocks``: list of (id, kind, protected_text)."""
    m = MODES.get(mode, MODES["natural"])
    ap = APPROACHES.get(approach, APPROACHES["balanced"])["prompt"]
    system = ("You are an experienced human editor revising an author's own document so it reads clearly and naturally, "
              "the way a skilled writer would put it for this audience. Understand what each passage means in context before changing it. "
              "Focus on sentence construction, paragraph organisation and flow; vocabulary, style and grammar get a separate check later, "
              "so never trade accuracy for polish.\n\n"
              f"Style: {m['prompt']}\n{INTENSITIES.get(intensity, INTENSITIES['medium'])['prompt']}" + (f"\n{ap}" if ap else "") +
              f"\n\n{CORE_RULES}")
    if mode == "custom" and custom_instructions:
        system += f"\n\nAuthor's style instructions: {custom_instructions.strip()[:1200]}"
    elif custom_instructions:
        system += f"\n\nAdditional instructions from the author: {custom_instructions.strip()[:1200]}"
    if style_block:
        system += f"\n\n{style_block}"
    if audience:
        system += f"\n\nWrite for this audience: {audience.strip()[:200]}."
    system += ("\n\nOutput format: rewrite every block you are given, in the same order, as <b id=\"N\">revised text</b> — one tag per block, "
               "same ids. A list item stays a single list item (no bullet characters). Output nothing else.")
    ctx = []
    if context_text:
        ctx.append(f"About the document:\n{context_text}")
    if section_heading:
        ctx.append(f"Current section heading: {section_heading}")
    if meaning_notes:
        ctx.append("Meaning each block must keep (from the semantic analysis; keep every point, qualifier, negation and logical link):\n- "
                   + "\n- ".join(meaning_notes[:14]))
    if guidance:
        ctx.append("What the writing analysis found (address where relevant):\n- " + "\n- ".join(guidance[:10]))
    if prev_text:
        ctx.append(f"Text just before this section (already revised; context only, do not output it):\n{prev_text}")
    if next_text:
        ctx.append(f"Text just after this section (context only, do not output it):\n{next_text}")
    if legend_text:
        ctx.append(f"Protected tokens (keep exactly):\n{legend_text}")
    body = "\n".join(f"<b id=\"{bid}\">{txt}</b>" + (" (list item)" if kind == "list_item" else "") for bid, kind, txt in blocks)
    if other_version:
        ctx.append("Another version of these blocks already exists (shown for contrast only; do not output it). Yours must be a "
                   "genuinely different revision — different sentence construction, order and wording where that serves your approach — "
                   f"while keeping exactly the same meaning:\n{other_version}")
    if ap:
        # Repeated next to the text: small models follow an instruction more reliably when it is close to the task.
        ctx.append(f"For this version — {ap}")
    user = "\n\n".join(ctx) + f"\n\nBlocks to revise:\n{body}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def flow_messages(*, mode: str, items: list[tuple[int, str, str, str]], legend_text: str) -> list[dict]:
    """``items``: (block_id, previous_paragraph, paragraph, problem)."""
    m = MODES.get(mode, MODES["natural"])
    system = ("You are an editor smoothing the flow between paragraphs of a revised document. For each paragraph, change ONLY its opening "
              "(the first sentence, or its first few words) so it connects naturally to the previous paragraph and does not repeat the "
              "previous paragraph's opening pattern. Leave the rest of the paragraph exactly as it is.\n"
              f"Style: {m['prompt']}\n\n{CORE_RULES}\n\nOutput: <b id=\"N\">full paragraph with the revised opening</b> for each paragraph. Nothing else.")
    parts = []
    for bid, prev, para, problem in items:
        parts.append(f"Previous paragraph (context only):\n{prev}\nProblem: {problem}\n<b id=\"{bid}\">{para}</b>")
    user = ("Protected tokens (keep exactly):\n" + legend_text + "\n\n" if legend_text else "") + "\n\n".join(parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


STAGE_FOCUS = {
    "vocabulary": "vocabulary: replace vague, generic or needlessly formal words and stock phrases with plain, specific wording; "
                  "restore technical terms exactly; never swap a simple word for a fancier synonym",
    "style": "style: make the passage match the requested style and register consistently",
    "grammar": "grammar and readability: fix the listed grammar, punctuation and readability problems",
}


def refine_messages(*, stage: str, mode: str, custom_instructions: str | None, style_block: str | None,
                    items: list[tuple[int, str, list[str]]], legend_text: str) -> list[dict]:
    """Targeted edits for one refinement stage. ``items``: (block_id, protected_text, instructions)."""
    m = MODES.get(mode, MODES["natural"])
    system = (f"You are a careful line editor doing one focused pass — {STAGE_FOCUS[stage]}. For each block, make ONLY the listed edits "
              "(and the minimum rewording needed around them). Leave every other sentence exactly as it is.\n\n"
              f"Style: {m['prompt']}\n\n{CORE_RULES}")
    if mode == "custom" and custom_instructions:
        system += f"\n\nAuthor's style instructions: {custom_instructions.strip()[:800]}"
    if style_block and stage == "style":
        system += f"\n\n{style_block}"
    system += "\n\nOutput: <b id=\"N\">the full block with the edits applied</b> for each block, same ids. Nothing else."
    parts = []
    for bid, text, instr in items:
        parts.append(f"<b id=\"{bid}\">{text}</b>\nEdits for block {bid}:\n- " + "\n- ".join(instr))
    user = ("Protected tokens (keep exactly):\n" + legend_text + "\n\n" if legend_text else "") + "\n\n".join(parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def repair_messages(*, mode: str, items: list[tuple[int, str, str, list[str]]], legend_text: str) -> list[dict]:
    """Fix meaning problems found by validation. ``items``: (block_id, original, revision, problems)."""
    m = MODES.get(mode, MODES["natural"])
    system = ("You are a fact-checking editor. Each REVISION below was rewritten from its ORIGINAL but the checks found places where "
              "the meaning changed. Correct the REVISION so it says exactly what the ORIGINAL says — same facts, numbers, dates, "
              "qualifiers (e.g. roughly, may, most, not necessarily), negations, recommendations and points — while keeping the "
              "revision's improved wording everywhere else. Do not simply copy the original back unless that is the only correct fix.\n\n"
              f"Style: {m['prompt']}\n\n{CORE_RULES}\n\nOutput: <b id=\"N\">corrected revision</b> for each block. Nothing else.")
    parts = []
    for bid, orig, rev, problems in items:
        parts.append(f"[Block {bid}]\nORIGINAL: {orig}\nREVISION: {rev}\nProblems to fix:\n- " + "\n- ".join(problems[:6]))
    user = ("Protected tokens (keep exactly):\n" + legend_text + "\n\n" if legend_text else "") + "\n\n".join(parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


MAX_VALIDATION_ISSUES = 6


def validation_messages(pairs: list[tuple[int, str, str]], points: dict[int, list[str]] | None = None) -> list[dict]:
    system = ("You are a meticulous fact-checking editor. Compare each ORIGINAL block with its REVISED version and report only real "
              "problems where the revision changes meaning: missing information, changed or contradicted claims, altered numbers or dates, "
              "changed technical terms, removed or altered citations or quotations, changed certainty (e.g. 'may' → 'will'), changed arguments, "
              "or added information that is not in the original. Do NOT report style, wording or tone changes that keep the meaning. "
              "Respond with one JSON object only.")
    points = points or {}
    body = "\n\n".join(f"[Block {bid}]\nORIGINAL: {o}\n" + (f"POINTS THE ORIGINAL MAKES: {' | '.join(points[bid])}\n" if points.get(bid) else "")
                       + f"REVISED: {r}" for bid, o, r in pairs)
    schema = ('{"issues": [{"block": 0, "type": "missing_information|changed_claim|number_or_date|terminology|citation|quotation|'
              'certainty|argument|added_information|contradiction", "severity": "high|medium|low", "original": "exact excerpt, max 12 words", '
              '"revised": "exact excerpt, max 12 words", "explanation": "max 20 words"}]}')
    user = (f"{body}\n\nReturn JSON in this shape (an empty list if there are no meaning problems). Report at most {MAX_VALIDATION_ISSUES} "
            f"issues, the most important first; keep every field short.\n{schema}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


SELECTION_ANGLES = [
    "",
    "Make this alternative noticeably different in sentence construction from the first (e.g. different order or sentence boundaries).",
    "Make this alternative the most concise of the three.",
]


def selection_messages(*, action: str, text: str, before: str, after: str, mode: str, custom_instruction: str | None,
                       style_block: str | None, audience: str | None, legend_text: str, tone: str | None = None,
                       alternatives: int = 1) -> list[dict]:
    m = MODES.get(mode, MODES["natural"])
    task = SELECTION_ACTIONS.get(action, SELECTION_ACTIONS["improve"])
    if action == "tone":
        task = f"Change the tone to {TONES.get(tone or '', tone or 'neutral and objective')}, keeping every fact, qualifier and claim."
    if action == "custom" and custom_instruction:
        task = f"User instruction: {custom_instruction.strip()[:800]}"
    elif custom_instruction:
        task += f" Additional instruction: {custom_instruction.strip()[:800]}"
    system = (f"You are an experienced editor revising one passage of the author's document.\nStyle: {m['prompt']}\n\n{CORE_RULES}")
    if style_block:
        system += f"\n\n{style_block}"
    if audience:
        system += f"\n\nAudience: {audience.strip()[:200]}."
    if alternatives <= 1:
        system += ("\n\nRevise ONLY the passage inside <original>. It must fit seamlessly between the surrounding text. "
                   "Return the revised passage inside <revision>…</revision>, then one short sentence inside <explanation>…</explanation>.")
    else:
        angles = "\n".join(f"- Alternative {i + 1}: {SELECTION_ANGLES[i] or 'your best version.'}" for i in range(alternatives))
        system += (f"\n\nRevise ONLY the passage inside <original>. Write {alternatives} genuinely different alternatives, each of which "
                   f"must fit seamlessly between the surrounding text and keep the full meaning:\n{angles}\n"
                   "Return each as <revision n=\"1\">…</revision>, <revision n=\"2\">…</revision> and so on, then one short sentence "
                   "inside <explanation>…</explanation> describing how they differ.")
    ctx = []
    if before:
        ctx.append(f"Text before the passage (context only):\n…{before}")
    if after:
        ctx.append(f"Text after the passage (context only):\n{after}…")
    if legend_text:
        ctx.append(f"Protected tokens (keep exactly):\n{legend_text}")
    user = "\n\n".join(ctx) + f"\n\nTask: {task}\n\n<original>{text}</original>"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def style_messages(stats_lines: list[str], excerpt: str) -> list[dict]:
    system = ("You describe an author's writing style so an editor can imitate the style — never the content. Respond with one JSON object only. "
              "Do not quote or paraphrase any sentence from the samples; describe habits in general terms.")
    schema = ('{"description": "2–3 sentences describing voice, rhythm and register", "tone": "a few words", '
              '"traits": ["short style habits, e.g. \'opens paragraphs with the main point\'"], "avoid": ["things this author does not do"]}')
    user = ("Measured statistics:\n- " + "\n- ".join(stats_lines) + f"\n\nWriting samples:\n<samples>\n{excerpt}\n</samples>\n\n"
            f"Return JSON in this shape (at most 8 traits, 5 avoid):\n{schema}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
