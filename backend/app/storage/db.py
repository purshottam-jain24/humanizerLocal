"""SQLite persistence for metadata, analysis results and revision history.

Large blobs (original DOCX, extracted structure) live on disk; SQLite stores
metadata and rows that need querying.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.config import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT,
    stages_json TEXT,
    options_json TEXT,
    revision_head INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    paragraph_id TEXT,
    sentence_id TEXT,
    module TEXT NOT NULL,
    category TEXT NOT NULL,
    passage TEXT,
    start INTEGER,
    end INTEGER,
    explanation TEXT NOT NULL,
    correction TEXT,
    priority TEXT NOT NULL,
    confidence REAL,
    source TEXT NOT NULL,
    data_json TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_doc ON findings(document_id, module);

CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    paragraph_id TEXT NOT NULL,
    sentence_id TEXT,
    finding_id TEXT,
    group_id TEXT,
    scope TEXT NOT NULL,
    action TEXT NOT NULL,
    mode TEXT,
    base_text TEXT NOT NULL,
    original_text TEXT NOT NULL,
    proposed_text TEXT NOT NULL,
    span_start INTEGER,
    span_end INTEGER,
    category TEXT NOT NULL,
    explanation TEXT,
    reason TEXT,
    meaning_json TEXT,
    verification_json TEXT,
    status TEXT NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    model TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sugg_doc ON suggestions(document_id, paragraph_id);

CREATE TABLE IF NOT EXISTS revision_ops (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    op_type TEXT NOT NULL,
    paragraph_id TEXT,
    before_text TEXT,
    after_text TEXT,
    suggestion_id TEXT,
    prev_status TEXT,
    new_status TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (document_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (document_id, seq)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_sources (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    path TEXT,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    url TEXT,
    meta_json TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    embed_model TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES corpus_sources(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON corpus_chunks(source_id);

CREATE TABLE IF NOT EXISTS fingerprints (
    hash INTEGER NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES corpus_chunks(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fp_hash ON fingerprints(hash);
CREATE INDEX IF NOT EXISTS idx_fp_chunk ON fingerprints(chunk_id);

CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def now() -> float:
    return time.time()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_conn() -> sqlite3.Connection:
    path = config.db_path
    key = str(path)
    conn = getattr(_local, "conns", {}).get(key)
    if conn is None:
        config.ensure_dirs()
        with _init_lock:
            if key not in _initialized_paths:
                c = _connect(path)
                c.executescript(SCHEMA)
                c.close()
                _initialized_paths.add(key)
        conn = _connect(path)
        if not hasattr(_local, "conns"):
            _local.conns = {}
        _local.conns[key] = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    row = get_conn().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple | list = ()) -> int:
    cur = get_conn().execute(sql, params)
    return cur.rowcount


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def document_dir(document_id: str) -> Path:
    d = config.data_dir / "documents" / document_id
    d.mkdir(parents=True, exist_ok=True)
    return d
