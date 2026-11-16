"""Application configuration.

All data lives on the local machine. The data directory defaults to
``backend/data`` and can be overridden with ``HUMANWRITE_DATA_DIR``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    ollama_url: str
    max_upload_bytes: int
    max_concurrent_generations: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "humanwrite.sqlite3"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / "corpus"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.originals_dir, self.exports_dir, self.corpus_dir):
            d.mkdir(parents=True, exist_ok=True)


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def load_config() -> Config:
    return Config(
        data_dir=Path(os.environ.get("HUMANWRITE_DATA_DIR", _default_data_dir())).resolve(),
        ollama_url=os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434").rstrip("/"),
        max_upload_bytes=int(os.environ.get("HUMANWRITE_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        max_concurrent_generations=1,
    )


config = load_config()
