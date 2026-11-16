"""Typed, persisted, sequence-numbered processing events.

Each document has an append-only event log in SQLite. Live subscribers get
events through asyncio queues; reconnecting clients pass the last sequence
number they saw (SSE ``Last-Event-ID``) and receive only newer events, so
nothing is duplicated and no backend work is repeated.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any

from app.storage import db

EVENT_TYPES = {
    "document.uploaded",
    "document.validated",
    "document.stage.changed",
    "document.page.started",
    "document.page.completed",
    "document.text.extracted",
    "document.ocr.word.recognized",
    "document.structure.detected",
    "document.paragraph.started",
    "document.paragraph.completed",
    "document.analysis.started",
    "document.analysis.progress",
    "document.analysis.completed",
    "document.finding.created",
    "document.suggestion.started",
    "document.suggestion.completed",
    "document.processing.completed",
    "document.processing.cancelled",
    "document.processing.failed",
}

TERMINAL = {"document.processing.completed", "document.processing.cancelled", "document.processing.failed"}


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._seq_lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _next_seq(self, document_id: str) -> int:
        with self._seq_lock:
            if document_id not in self._seq:
                row = db.query_one("SELECT MAX(seq) AS m FROM events WHERE document_id=?", (document_id,))
                self._seq[document_id] = (row["m"] or 0) if row else 0
            self._seq[document_id] += 1
            return self._seq[document_id]

    def emit(self, document_id: str, type_: str, payload: dict[str, Any] | None = None, persist: bool = True) -> dict:
        assert type_ in EVENT_TYPES, type_
        seq = self._next_seq(document_id)
        event = {"seq": seq, "type": type_, "document_id": document_id, "ts": db.now(), "data": payload or {}}
        if persist:
            db.execute("INSERT INTO events(document_id, seq, type, payload_json, created_at) VALUES (?,?,?,?,?)",
                       (document_id, seq, type_, db.dumps(event["data"]), event["ts"]))
        for q in list(self._subs.get(document_id, ())):
            self._deliver(q, event)
        return event

    def _deliver(self, q: asyncio.Queue, event: dict) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(q.put_nowait, event)
        else:
            q.put_nowait(event)

    def history(self, document_id: str, after: int = 0) -> list[dict]:
        rows = db.query("SELECT seq, type, payload_json, created_at FROM events WHERE document_id=? AND seq>? ORDER BY seq",
                        (document_id, after))
        return [{"seq": r["seq"], "type": r["type"], "document_id": document_id, "ts": r["created_at"],
                 "data": db.loads(r["payload_json"], {})} for r in rows]

    def subscribe(self, document_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[document_id].add(q)
        return q

    def unsubscribe(self, document_id: str, q: asyncio.Queue) -> None:
        self._subs[document_id].discard(q)

    def reset(self, document_id: str) -> None:
        with self._seq_lock:
            self._seq.pop(document_id, None)


bus = EventBus()
