"""Personal writing-style profiles.

A profile is derived from writing samples the user provides. Only the *derived* profile
is stored (statistics, a short description, traits and a few short habitual expressions);
the samples themselves are discarded after analysis and are never used for anything else.
Users can rename, edit and delete profiles at any time.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter

from app.services.analysis.ai_writing_analysis_service import STOCK_TRANSITIONS
from app.services.analysis.readability_service import metrics_for
from app.services.extraction.sentences import WORD_RE, split_sentences
from app.services.humanizer.analysis import CONTRACTION, FIRST_SECOND, STOPWORDS, formality_score
from app.storage import db

MIN_SAMPLE_WORDS = 150
MAX_SAMPLE_CHARS = 60_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS style_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    traits_json TEXT NOT NULL DEFAULT '[]',
    expressions_json TEXT NOT NULL DEFAULT '[]',
    stats_json TEXT NOT NULL DEFAULT '{}',
    sample_words INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

TRANSITION_WORDS = sorted(set(STOCK_TRANSITIONS + [
    "but", "so", "still", "yet", "instead", "meanwhile", "then", "also", "anyway", "besides", "for example", "for instance",
    "in fact", "of course", "that said", "even so", "in short", "as a result", "because of this", "on top of that", "after all",
]))


def _ensure() -> None:
    db.get_conn().executescript(SCHEMA)


def _row(r: dict) -> dict:
    return {
        "id": r["id"], "name": r["name"], "description": r["description"],
        "traits": db.loads(r["traits_json"], []), "expressions": db.loads(r["expressions_json"], []),
        "stats": db.loads(r["stats_json"], {}), "sample_words": r["sample_words"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def list_profiles() -> list[dict]:
    _ensure()
    return [_row(r) for r in db.query("SELECT * FROM style_profiles ORDER BY updated_at DESC")]


def get_profile(pid: str) -> dict | None:
    _ensure()
    r = db.query_one("SELECT * FROM style_profiles WHERE id=?", (pid,))
    return _row(r) if r else None


def create_profile(*, name: str, description: str, traits: list[str], expressions: list[str], stats: dict, sample_words: int) -> dict:
    _ensure()
    pid = db.new_id("sty")
    t = db.now()
    db.execute("INSERT INTO style_profiles(id,name,description,traits_json,expressions_json,stats_json,sample_words,created_at,updated_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               (pid, name.strip()[:80] or "My style", description.strip()[:1500], db.dumps(_clean_list(traits, 12, 140)),
                db.dumps(_clean_list(expressions, 20, 40)), db.dumps(stats), sample_words, t, t))
    return get_profile(pid)


def update_profile(pid: str, patch: dict) -> dict | None:
    _ensure()
    cur = get_profile(pid)
    if not cur:
        return None
    name = (patch.get("name") if patch.get("name") is not None else cur["name"]).strip()[:80] or cur["name"]
    desc = (patch.get("description") if patch.get("description") is not None else cur["description"]).strip()[:1500]
    traits = _clean_list(patch["traits"], 12, 140) if patch.get("traits") is not None else cur["traits"]
    expr = _clean_list(patch["expressions"], 20, 40) if patch.get("expressions") is not None else cur["expressions"]
    stats = cur["stats"]
    sample_words = cur["sample_words"]
    if patch.get("stats") is not None:
        stats = patch["stats"]
        sample_words = int(patch.get("sample_words") or sample_words)
    db.execute("UPDATE style_profiles SET name=?, description=?, traits_json=?, expressions_json=?, stats_json=?, sample_words=?, updated_at=? WHERE id=?",
               (name, desc, db.dumps(traits), db.dumps(expr), db.dumps(stats), sample_words, db.now(), pid))
    return get_profile(pid)


def delete_profile(pid: str) -> bool:
    _ensure()
    return db.execute("DELETE FROM style_profiles WHERE id=?", (pid,)) > 0


def _clean_list(items: list, n: int, maxlen: int) -> list[str]:
    out = []
    for x in items or []:
        s = str(x).strip()
        if s and s not in out:
            out.append(s[:maxlen])
    return out[:n]


# ------------------------------------------------------------------ analysis
def sample_stats(text: str) -> dict:
    """Deterministic style statistics of writing samples."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sents = [p[s:e] for p in paras for s, e in split_sentences(p)]
    lengths = [len(WORD_RE.findall(s)) for s in sents if WORD_RE.findall(s)]
    words = WORD_RE.findall(text)
    n = max(1, len(words))
    low = text.lower()
    trans = Counter()
    for s in sents:
        sl = s.lower().lstrip("“\"'(")
        for t in TRANSITION_WORDS:
            if sl.startswith(t + ",") or sl.startswith(t + " "):
                trans[t] += 1
                break
    rd = metrics_for(text, len(sents) or 1)
    para_lengths = [len(WORD_RE.findall(p)) for p in paras]
    stats = {
        "words": len(words),
        "avg_sentence_length": round(statistics.mean(lengths), 1) if lengths else 0,
        "sentence_length_sd": round(statistics.pstdev(lengths), 1) if len(lengths) > 1 else 0,
        "short_sentence_share": round(sum(1 for l in lengths if l <= 8) / len(lengths), 2) if lengths else 0,
        "long_sentence_share": round(sum(1 for l in lengths if l >= 30) / len(lengths), 2) if lengths else 0,
        "avg_paragraph_length": round(statistics.mean(para_lengths), 1) if para_lengths else 0,
        "sentences_per_paragraph": round(len(sents) / max(1, len(paras)), 1),
        "contractions_per_100_words": round(100 * len(CONTRACTION.findall(text)) / n, 2),
        "first_person_per_100_words": round(100 * len(re.findall(r"\b(i|me|my|we|our|us)\b", low)) / n, 2),
        "second_person_per_100_words": round(100 * len(re.findall(r"\b(you|your)\b", low)) / n, 2),
        "personal_pronouns_per_100_words": round(100 * len(FIRST_SECOND.findall(text)) / n, 2),
        "questions_share": round(sum(1 for s in sents if s.strip().endswith("?")) / max(1, len(sents)), 2),
        "dashes_per_100_words": round(100 * (text.count("—") + text.count(" – ") + text.count(" - ")) / n, 2),
        "semicolons_per_100_words": round(100 * text.count(";") / n, 2),
        "parentheses_per_100_words": round(100 * text.count("(") / n, 2),
        "exclamations_per_100_words": round(100 * text.count("!") / n, 2),
        "formality": formality_score(text),
        "flesch_reading_ease": rd.get("flesch_reading_ease"),
        "grade_level": rd.get("flesch_kincaid_grade"),
        "preferred_transitions": [t for t, _ in trans.most_common(6)],
        "lexical_variety": round(len({w.lower() for w in words}) / n, 3),
    }
    return stats


def habitual_expressions(text: str, limit: int = 12) -> list[str]:
    """Short recurring expressions (2–4 words) — habits, never whole sentences."""
    toks = [w.lower() for w in WORD_RE.findall(text)]
    out: Counter = Counter()
    for n in (2, 3, 4):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            content = sum(1 for w in g if w not in STOPWORDS)
            if content == 0 or content == n and n > 2:
                continue
            out[" ".join(g)] += 1
    picked: list[str] = []
    for g, c in out.most_common(80):
        if c < 2:
            break
        if any(g in p or p in g for p in picked):
            continue
        picked.append(g)
        if len(picked) >= limit:
            break
    return picked


def describe_stats(stats: dict) -> list[str]:
    """Plain-language guidance derived from statistics (used in prompts and shown to the user)."""
    out = []
    a = stats.get("avg_sentence_length") or 0
    if a:
        rng = "short" if a < 13 else "medium-length" if a < 21 else "long"
        out.append(f"Sentences are typically {rng} (about {a:.0f} words; spread {stats.get('sentence_length_sd', 0):.0f}).")
    c = stats.get("contractions_per_100_words", 0)
    out.append("Uses contractions freely." if c >= 1.5 else "Uses contractions occasionally." if c >= 0.4 else "Rarely uses contractions.")
    p = stats.get("first_person_per_100_words", 0)
    if p >= 2:
        out.append("Writes in the first person often.")
    if stats.get("second_person_per_100_words", 0) >= 1.5:
        out.append("Addresses the reader directly (“you”).")
    if stats.get("questions_share", 0) >= 0.08:
        out.append("Uses questions to move the text along.")
    if stats.get("dashes_per_100_words", 0) >= 0.5:
        out.append("Likes dashes for asides.")
    if stats.get("semicolons_per_100_words", 0) >= 0.4:
        out.append("Uses semicolons to join related clauses.")
    if stats.get("parentheses_per_100_words", 0) >= 0.6:
        out.append("Uses parenthetical remarks.")
    f = stats.get("formality")
    if f is not None:
        out.append("Register is casual." if f < 35 else "Register is neutral." if f < 65 else "Register is formal.")
    if stats.get("preferred_transitions"):
        out.append("Preferred connectives: " + ", ".join(stats["preferred_transitions"][:5]) + ".")
    spp = stats.get("sentences_per_paragraph")
    if spp:
        out.append(f"Paragraphs average about {spp:.0f} sentences.")
    return out


def prompt_block(profile: dict) -> str:
    """How a profile is described to the rewriting model. Never contains sample sentences."""
    lines = [f"Write in the voice of the author's saved style profile “{profile['name']}”."]
    if profile.get("description"):
        lines.append(profile["description"])
    lines += describe_stats(profile.get("stats") or {})
    if profile.get("traits"):
        lines.append("Traits: " + "; ".join(profile["traits"][:10]) + ".")
    if profile.get("expressions"):
        lines.append("Expressions this author naturally uses (use sparingly, only where they fit): " + ", ".join(f"“{e}”" for e in profile["expressions"][:10]) + ".")
    lines.append("Match the style, not the content: never copy sentences from the author's other writing, and do not force expressions in.")
    return "\n".join(lines)
