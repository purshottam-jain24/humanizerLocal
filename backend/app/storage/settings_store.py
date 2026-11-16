"""User settings persisted in SQLite, merged over defaults."""

from __future__ import annotations

import copy
from typing import Any

from app.storage import db

DEFAULT_SETTINGS: dict[str, Any] = {
    "ollama": {
        "model": None,  # chosen by the user from installed models
        "embed_model": None,
        "temperature": 0.3,
        "num_ctx": 4096,
        "keep_alive": "5m",
    },
    "writing": {
        "tone": "academic",  # academic | neutral | formal | conversational
        "intensity": "balanced",  # light | balanced | extensive
        "custom_instructions": "",
        "language": "en-US",
        "preserve_style": True,
    },
    "analysis": {
        "llm_review": False,  # optional AI review during processing; off by default (slow with reasoning models)
        "llm_review_max_paragraphs": 40,
        "spellcheck": True,
        "similarity": True,
        "semantic_similarity": True,
        "ocr": False,
        "ai_detection": True,  # runs automatically during the scan once the detector is installed
    },
    "document": {
        "preview_zoom": 0.8,
        "autosave": True,
        "scan_animation": True,
    },
    "privacy": {
        "external_sources": False,  # off unless explicitly enabled
        "external_providers": ["openalex", "crossref"],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> dict[str, Any]:
    stored = {r["key"]: db.loads(r["value_json"]) for r in db.query("SELECT key, value_json FROM settings")}
    return _deep_merge(DEFAULT_SETTINGS, stored)


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_settings()
    for section, values in patch.items():
        if section not in DEFAULT_SETTINGS or not isinstance(values, dict):
            continue
        merged = {**current[section], **{k: v for k, v in values.items() if k in DEFAULT_SETTINGS[section]}}
        db.execute(
            "INSERT INTO settings(key, value_json) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (section, db.dumps(merged)),
        )
    return get_settings()
