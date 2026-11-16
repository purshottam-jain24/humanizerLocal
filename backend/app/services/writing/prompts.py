"""Prompt construction for local writing assistance."""

from __future__ import annotations

import re

OBJ = "￼"
MARK_RE = re.compile(r"⟦(\d+)⟧")

ACTIONS = {
    "fix_grammar": "Fix grammar errors only.",
    "fix_spelling": "Correct spelling mistakes only.",
    "improve_punctuation": "Improve punctuation only.",
    "improve_clarity": "Improve clarity so the point is easier to understand.",
    "simplify": "Simplify the wording while keeping the meaning and key terms.",
    "rephrase": "Rephrase the text with different sentence construction while keeping the meaning.",
    "rewrite_paragraph": "Rewrite the paragraph for clarity, flow and organisation.",
    "academic_tone": "Improve the academic tone: precise, objective, measured language. Do not add hedging that changes the claims.",
    "improve_transitions": "Improve the transitions between sentences so ideas connect logically.",
    "remove_repetition": "Remove unnecessary repetition.",
    "concise": "Make the text more concise without losing information.",
    "expand": "Expand the explanation using only information and reasoning already present in the text and its context. Do not add new facts, data, examples or sources.",
    "readability": "Improve readability: shorter sentences where helpful, clear structure, plain words.",
    "terminology": "Make terminology consistent with the document's existing terms.",
    "preserve_style": "Improve the text while preserving the author's personal voice and word choices as much as possible.",
    "custom": "Follow the user's custom instruction.",
    "fix_finding": "Fix the specific issue described.",
}

ACTION_LABELS = {
    "fix_grammar": "Fix grammar", "fix_spelling": "Correct spelling", "improve_punctuation": "Improve punctuation",
    "improve_clarity": "Improve clarity", "simplify": "Simplify wording", "rephrase": "Rephrase",
    "rewrite_paragraph": "Rewrite paragraph", "academic_tone": "Academic tone", "improve_transitions": "Improve transitions",
    "remove_repetition": "Remove repetition", "concise": "Make concise", "expand": "Expand explanation",
    "readability": "Improve readability", "terminology": "Terminology consistency", "preserve_style": "Preserve my style",
    "custom": "Custom instruction", "fix_finding": "Fix issue",
}

MODES = {
    "light": "LIGHT editing: correct grammar, spelling and punctuation with minimal changes. Keep the original wording wherever possible.",
    "balanced": "BALANCED editing: improve sentence construction and clarity, remove unnecessary repetition, improve transitions. Preserve the meaning and the author's voice.",
    "extensive": "EXTENSIVE editing: you may restructure sentences, improve organisation and explanations, and remove redundant content. Preserve the original intended meaning exactly.",
}

ALT_STYLES = [
    ("minimal", "Alternative style: minimal correction — the smallest change that fixes the problems."),
    ("clearer", "Alternative style: clearer wording — prioritise clarity for the reader."),
    ("concise", "Alternative style: more concise — remove words that do not add meaning."),
    ("restructured", "Alternative style: restructured — reorder or recombine sentences for better flow."),
]

RULES = """Rules you must follow:
- Return ONLY the revised text inside <revision>…</revision>, then a one or two sentence explanation inside <explanation>…</explanation>.
- Never invent facts, statistics, quotations, names, dates or references. Never add citations that are not already present.
- Keep every citation, number, name and technical term from the original unless the task is explicitly to fix it.
- Keep every marker like ⟦1⟧ exactly once and in the same relative position; they represent footnotes, images or fields.
- Do not add headings, bullet points, quotation marks around the whole text, or commentary inside <revision>.
- Write in the same language and variety of English as the original.
- Do not try to make text evade AI detection; improve clarity, accuracy and expression only."""


def protect(text: str) -> tuple[str, int]:
    n = 0
    out = []
    for ch in text:
        if ch == OBJ:
            n += 1
            out.append(f"⟦{n}⟧")
        else:
            out.append(ch)
    return "".join(out), n


def restore(text: str, expected: int) -> tuple[str, bool]:
    found = [int(m.group(1)) for m in MARK_RE.finditer(text)]
    ok = sorted(found) == list(range(1, expected + 1)) and found == sorted(found)
    return MARK_RE.sub(OBJ, text), ok


def build_messages(*, action: str, mode: str, tone: str, target_text: str, scope: str, paragraph_text: str,
                   prev_text: str | None, next_text: str | None, doc_title: str | None, section_heading: str | None,
                   terminology: list[str], custom_instruction: str | None, global_instructions: str | None,
                   finding: dict | None = None, alt_style: str | None = None, avoid: list[str] | None = None) -> list[dict]:
    system = (
        "You are HumanWrite, a careful editor helping an author improve their own document. "
        f"{MODES.get(mode, MODES['balanced'])} Preferred tone: {tone}.\n\n{RULES}"
    )
    ctx = []
    if doc_title:
        ctx.append(f"Document title: {doc_title}")
    if section_heading:
        ctx.append(f"Current section: {section_heading}")
    if terminology:
        ctx.append("Key terms used in the document (keep them consistent): " + ", ".join(terminology[:20]))
    if prev_text:
        ctx.append(f"Previous paragraph (context only, do not rewrite):\n{prev_text[:1200]}")
    if scope == "sentence":
        ctx.append(f"Full paragraph containing the sentence (context only):\n{paragraph_text[:2500]}")
    if next_text:
        ctx.append(f"Next paragraph (context only, do not rewrite):\n{next_text[:1200]}")
    task = ACTIONS.get(action, ACTIONS["improve_clarity"])
    if action == "custom" and custom_instruction:
        task = f"User instruction: {custom_instruction.strip()[:800]}"
    elif custom_instruction:
        task += f" Additional user instruction: {custom_instruction.strip()[:800]}"
    if finding:
        task += f"\nIssue to address ({finding.get('category')}): {finding.get('explanation')}"
        if finding.get("passage"):
            task += f"\nAffected passage: “{finding['passage'][:400]}”"
    if alt_style:
        task += "\n" + dict(ALT_STYLES).get(alt_style, "")
    if avoid:
        task += "\nProduce a version that is noticeably different from these earlier versions:\n" + "\n".join(f"- {a[:400]}" for a in avoid[:3])
    if global_instructions:
        system += f"\n\nThe author's standing preferences: {global_instructions.strip()[:800]}"
    unit = "sentence" if scope == "sentence" else "paragraph"
    user = ("\n\n".join(ctx) + "\n\n" if ctx else "") + f"Task: {task}\n\nRevise this {unit}:\n<original>{target_text}</original>"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


REVIEW_SYSTEM = """You are a meticulous copy editor. Identify concrete writing problems in the paragraph.
Report only real issues: grammar, spelling, punctuation, awkward wording, unclear statements, wordiness, weak transitions, vague claims, abrupt tone changes, overly complicated sentences.
Do NOT flag formal or academic language just because it is formal. Do NOT flag technical terms. Do not comment on content you cannot verify.
For each issue copy the exact passage from the paragraph character-for-character (short: a few words to one sentence).
Respond with JSON only."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "passage": {"type": "string"},
                    "category": {"type": "string", "enum": ["grammar", "spelling", "punctuation", "awkward_wording", "clarity",
                                                            "wordiness", "transition", "vague_statement", "tone", "complexity", "flow"]},
                    "explanation": {"type": "string"},
                    "correction": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["passage", "category", "explanation", "correction", "priority"],
            },
        }
    },
    "required": ["issues"],
}


def review_messages(paragraph_text: str, prev_text: str | None, doc_title: str | None) -> list[dict]:
    ctx = f"Document: {doc_title}\n" if doc_title else ""
    if prev_text:
        ctx += f"Previous paragraph (context only): {prev_text[:600]}\n"
    return [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": f"{ctx}\nParagraph to review:\n{paragraph_text}\n\nReturn at most 6 issues as JSON."},
    ]


def parse_tagged(output: str) -> tuple[str | None, str | None, bool]:
    """Extract (revision, explanation, complete) from a streamed model reply."""
    rev = re.search(r"<revision>(.*?)(</revision>|$)", output, re.S)
    exp = re.search(r"<explanation>(.*?)(</explanation>|$)", output, re.S)
    revision = rev.group(1).strip() if rev else None
    explanation = exp.group(1).strip() if exp else None
    complete = bool(rev and rev.group(2))
    if revision is None:
        # Small models sometimes skip tags: accept a plain reply if it does not look like commentary.
        stripped = re.sub(r"^(here is|here's|revised( text)?:|sure[,!]).*?\n", "", output.strip(), flags=re.I)
        if stripped and "<" not in stripped[:20]:
            revision = stripped.strip().strip('"“”')
            complete = True
    return revision, explanation, complete
