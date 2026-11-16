"""HumanWrite API — local-first document intelligence backend."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import detection, documents, humanizer, system
from app.core.config import config
from app.events.document_event_service import bus
from app.storage import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.get_conn()
    bus.bind_loop(asyncio.get_running_loop())
    # Documents interrupted by a server stop are marked so the user can resume them.
    for d in db.query("SELECT id, stages_json FROM documents WHERE status='processing'"):
        stages = db.loads(d["stages_json"], [])
        for s in stages:
            if s["state"] == "active":
                s["state"] = "pending"
                s["detail"] = "Interrupted — resume to continue"
        db.execute("UPDATE documents SET status='interrupted', stages_json=? WHERE id=?", (db.dumps(stages), d["id"]))
    yield


app = FastAPI(title="HumanWrite", version="1.0.0", lifespan=lifespan)
# Extra origins (e.g. a second local frontend on another port) can be allowed with HUMANWRITE_CORS_ORIGINS="http://127.0.0.1:3001,…".
_extra_origins = [o.strip() for o in os.environ.get("HUMANWRITE_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", *_extra_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system.router)
app.include_router(documents.router)
app.include_router(detection.router)
app.include_router(humanizer.router)
