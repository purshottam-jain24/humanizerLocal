"""Local AI-generated text detection.

Two open classifiers read every passage of about 150–300 words:

* Fakespot RoBERTa-base (``fakespot-ai/roberta-base-ai-text-detection-v1``, Apache-2.0)
* Desklib DeBERTa-v3-large (``desklib/ai-text-detector-v1.01``, MIT)

A small trained layer (``layer_v3.json``, shipped in this repository) combines
each model's verdict and its internal representation of the passage into one
score, which is then calibrated into a probability. The layer was trained on
essays, articles and — the hard case — technical answers and notes, with human
technical writing from before AI tools on the other side, so that terse
"Definition: … / Phase 1: …" writing is not flagged just for its style.
See DEVELOPMENT_LOG.md for the data and the measured accuracy.

If only the Fakespot model is installed (installs from before version 3), the
detector keeps working with that model alone and offers the upgrade.

Everything runs on this computer; the model is downloaded once, only after
the user confirms.
"""

from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx

from app.core.config import config
from app.services.extraction.sentences import count_words, split_sentences

METHOD = "classifier"
MODEL_REPO = "fakespot-ai/roberta-base-ai-text-detection-v1"
MODEL_FILES = ["config.json", "merges.txt", "model.safetensors", "special_tokens_map.json",
               "tokenizer.json", "tokenizer_config.json", "vocab.json"]
DESK_REPO = "desklib/ai-text-detector-v1.01"
DESK_FILES = ["added_tokens.json", "config.json", "model.safetensors", "special_tokens_map.json",
              "spm.model", "tokenizer.json", "tokenizer_config.json"]
SIZES_MB = {MODEL_REPO: 505, DESK_REPO: 1748}
HF_BASE = "https://huggingface.co"
LAYER_PATH = Path(__file__).with_name("layer_v3.json")

# Single-model fallback (version 2): P(AI) = sigmoid(A * logit(raw) + B). Raw 0.993 maps to 50%.
CALIBRATION_V2 = {"a": 0.8902, "b": -4.3914}

MIN_CHUNK_WORDS = 60     # below this we do not score (too little evidence)
TARGET_CHUNK_WORDS = 160
MAX_CHUNK_WORDS = 300
MAX_TOKENS = 512
MIN_DOC_WORDS = 120      # whole-document verdict needs at least this much scored text

AI_THRESHOLD = 0.5       # chunk counted as AI-generated at or above this probability
STRONG = 0.8             # "very likely" band, for labels only
POSSIBLE = 0.2           # 20–50%: shown as "possibly AI", not counted in the AI percentage
IDLE_UNLOAD_S = 300


def models_dir() -> Path:
    return config.data_dir / "detector"


def _repo_dir(repo: str) -> Path:
    return models_dir() / repo.replace("/", "__")


def libraries_installed() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers", "safetensors"))


def _have(repo: str, files: list[str]) -> bool:
    return all((_repo_dir(repo) / f).is_file() and (_repo_dir(repo) / f).stat().st_size > 0 for f in files)


def models_installed() -> bool:
    """Enough to run detection (the Fakespot model; the second model is an upgrade)."""
    return _have(MODEL_REPO, MODEL_FILES)


def full_installed() -> bool:
    return _have(MODEL_REPO, MODEL_FILES) and _have(DESK_REPO, DESK_FILES) and LAYER_PATH.is_file()


def method_version() -> int:
    return 3 if full_installed() else 2


def _missing_mb() -> int:
    return (0 if _have(MODEL_REPO, MODEL_FILES) else SIZES_MB[MODEL_REPO]) + (0 if _have(DESK_REPO, DESK_FILES) else SIZES_MB[DESK_REPO])


def model_label() -> str:
    return "Fakespot RoBERTa + Desklib DeBERTa, combined" if full_installed() else "Fakespot RoBERTa AI-text detector"


_install_lock = asyncio.Lock()
_install_state: dict[str, Any] = {"running": False}


def status() -> dict:
    libs = libraries_installed()
    models = models_installed()
    used = sum(p.stat().st_size for p in models_dir().rglob("*") if p.is_file()) if models_dir().exists() else 0
    return {
        "ready": libs and models,
        "libraries_installed": libs,
        "models_installed": models,
        "installing": _install_state["running"],
        "download_mb": _missing_mb() if not models else 0,
        "upgrade_available": models and not full_installed(),
        "upgrade_mb": _missing_mb() if models else 0,
        "method": METHOD,
        "method_version": method_version(),
        "disk_mb": round(used / 1e6),
        "model": model_label(),
        "device": _detector.device if _detector.loaded else None,
    }


# ---------------------------------------------------------------- install
async def install() -> AsyncIterator[dict]:
    """Install libraries (pip) and download whichever models are missing. Caller must have the user's confirmation."""
    if _install_lock.locked():
        yield {"phase": "error", "message": "An installation is already running."}
        return
    async with _install_lock:
        _install_state["running"] = True
        try:
            if not libraries_installed():
                yield {"phase": "libraries", "message": "Installing detector libraries (PyTorch, Transformers)…"}
                req = Path(__file__).resolve().parents[3] / "requirements-detector.txt"
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                assert proc.stdout
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").strip()
                    if line:
                        yield {"phase": "libraries", "message": line[:160]}
                if await proc.wait() != 0:
                    yield {"phase": "error", "message": "Installing the detector libraries failed. See the terminal for details."}
                    return
                importlib.invalidate_caches()
            total = 0
            done = 0
            plan: list[tuple[str, str, int]] = []
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as c:
                for repo, files in ((MODEL_REPO, MODEL_FILES), (DESK_REPO, DESK_FILES)):
                    for f in files:
                        dest = _repo_dir(repo) / f
                        if dest.is_file() and dest.stat().st_size > 0:
                            continue
                        r = await c.head(f"{HF_BASE}/{repo}/resolve/main/{f}")
                        size = int(r.headers.get("x-linked-size") or r.headers.get("content-length") or 0)
                        plan.append((repo, f, size))
                        total += size
                for repo, f, size in plan:
                    dest = _repo_dir(repo) / f
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    part = dest.with_suffix(dest.suffix + ".part")
                    have = part.stat().st_size if part.exists() else 0
                    headers = {"Range": f"bytes={have}-"} if have else {}
                    async with c.stream("GET", f"{HF_BASE}/{repo}/resolve/main/{f}", headers=headers) as r:
                        if r.status_code not in (200, 206):
                            yield {"phase": "error", "message": f"Download failed ({r.status_code}) for {repo}/{f}."}
                            return
                        if r.status_code == 200:
                            have = 0
                        done += have
                        last = 0.0
                        with open(part, "ab" if have else "wb") as fh:
                            async for chunk in r.aiter_bytes(1 << 20):
                                fh.write(chunk)
                                done += len(chunk)
                                now = time.monotonic()
                                if now - last > 0.25:
                                    last = now
                                    yield {"phase": "models", "completed": done, "total": total,
                                           "message": "Downloading the detector models"}
                    part.replace(dest)
            _detector.unload()  # pick up the new model on next use
            yield {"phase": "done", "message": "AI detection is ready."}
        finally:
            _install_state["running"] = False


def remove_models() -> None:
    _detector.unload()
    if models_dir().exists():
        shutil.rmtree(models_dir())


# ---------------------------------------------------------------- scoring
def clean_text(t: str) -> str:
    """The normalisation the model was trained with (whitespace collapsed to single spaces)."""
    t = t.replace("\ufffc", " ").replace("\n", " ").replace("\t", " ").replace("\r", " ").replace(" ,", ",")
    return re.sub(" +", " ", t).strip()


class _Detector:
    """Loads lazily, unloads after IDLE_UNLOAD_S. ``score`` returns a raw P(AI) in [0, 1] per text."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.loaded = False
        self.version = 0
        self.device: str | None = None
        self._last_used = 0.0
        self.parts: dict[str, Any] = {}

    def _load(self) -> None:
        import json

        import torch
        from safetensors.torch import load_file
        from transformers import AutoConfig, AutoModel, AutoModelForSequenceClassification, AutoTokenizer

        self.device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        fs = _repo_dir(MODEL_REPO)
        fk = AutoModelForSequenceClassification.from_pretrained(fs).to(self.device).eval()
        ai = int({v.lower(): k for k, v in fk.config.id2label.items()}.get("ai", 1))
        self.parts = {"fs_tok": AutoTokenizer.from_pretrained(fs), "fs": fk, "fs_ai": ai}
        self.version = method_version()
        if self.version == 3:
            dk = _repo_dir(DESK_REPO)
            cfg = AutoConfig.from_pretrained(dk)
            enc = AutoModel.from_config(cfg)
            sd = load_file(str(dk / "model.safetensors"))
            enc.load_state_dict({k[6:]: v for k, v in sd.items() if k.startswith("model.")})
            head = torch.nn.Linear(cfg.hidden_size, 1)
            head.load_state_dict({"weight": sd["classifier.weight"], "bias": sd["classifier.bias"]})
            del sd
            layer = json.loads(LAYER_PATH.read_text())
            t = lambda k: torch.tensor(layer[k], dtype=torch.float32, device=self.device)  # noqa: E731
            self.parts.update({"dk_tok": AutoTokenizer.from_pretrained(dk), "dk": enc.to(self.device).eval(),
                               "dk_head": head.to(self.device).eval(),
                               "mu": t("mu"), "sd": t("sd"), "w": t("w"), "b": float(layer["b"]), "layer": layer})
        self.loaded = True
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def _idle_watch(self) -> None:
        while self.loaded:
            time.sleep(30)
            if self.loaded and time.monotonic() - self._last_used > IDLE_UNLOAD_S and not self._lock.locked():
                self.unload()

    def unload(self) -> None:
        with self._lock:
            if not self.loaded:
                return
            self.parts = {}
            self.loaded = False
            try:
                import torch
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:  # noqa: BLE001
                pass

    def _features(self, text: str) -> tuple[Any, int]:
        """[fakespot AI-logit, fakespot mean-pooled state, desklib logit, desklib mean-pooled state] (v3)."""
        import torch

        P = self.parts
        e = P["fs_tok"](text, return_tensors="pt", truncation=True, max_length=MAX_TOKENS).to(self.device)
        n = int(e["input_ids"].shape[1])
        o = P["fs"](**e, output_hidden_states=True)
        lg = o.logits[0].float()
        z_fs = lg[P["fs_ai"]] - lg[1 - P["fs_ai"]]
        if self.version < 3:
            return z_fs, n
        m = e["attention_mask"].unsqueeze(-1).float()
        pool_fs = (o.hidden_states[-1].float() * m).sum(1)[0] / m.sum()
        d = P["dk_tok"](text, return_tensors="pt", truncation=True, max_length=MAX_TOKENS).to(self.device)
        h = P["dk"](input_ids=d["input_ids"], attention_mask=d["attention_mask"])[0].float()
        md = d["attention_mask"].unsqueeze(-1).float()
        pool_dk = (h * md).sum(1)[0] / md.sum()
        z_dk = P["dk_head"](pool_dk.unsqueeze(0))[0, 0]
        return torch.cat([z_fs.view(1), pool_fs, z_dk.view(1), pool_dk]), n

    def score(self, texts: list[str]) -> list[dict]:
        """Raw P(AI) for each text before calibration (v3: the combined layer; v2: Fakespot alone)."""
        import torch

        with self._lock:
            if not self.loaded or self.version != method_version():
                if self.loaded:
                    self.parts = {}
                self._load()
            self._last_used = time.monotonic()
            out = []
            for t in texts:
                with torch.no_grad():
                    f, n = self._features(clean_text(t))
                    if n < 16:
                        out.append({"score": None, "tokens": n})
                        continue
                    if self.version < 3:
                        z = float(f)
                    else:
                        P = self.parts
                        z = float(((f - P["mu"]) / P["sd"]) @ P["w"] + P["b"])
                out.append({"score": 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z)))), "tokens": n})
            self._last_used = time.monotonic()
            return out


_detector = _Detector()


def _calibration() -> dict:
    if method_version() == 3:
        global _LAYER_CAL
        if _LAYER_CAL is None:
            import json
            _LAYER_CAL = json.loads(LAYER_PATH.read_text())["calibration"]
        return _LAYER_CAL
    return CALIBRATION_V2


_LAYER_CAL: dict | None = None


def probability(raw: float) -> float:
    """Calibrated P(AI-generated) from the raw score."""
    raw = min(max(raw, 1e-12), 1 - 1e-12)
    cal = _calibration()
    z = cal["a"] * math.log(raw / (1 - raw)) + cal["b"]
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))


def label_for(p: float) -> str:
    if p >= STRONG:
        return "ai"
    if p >= AI_THRESHOLD:
        return "likely_ai"
    if p >= POSSIBLE:
        return "possible"
    return "human"


def _eligible(p: dict) -> bool:
    """Running prose in the main body: no headings, captions, table cells, references or headers/footers."""
    return (p.get("part") == "body" and not p.get("table") and p.get("kind") in ("paragraph", "list_item")
            and not p.get("is_reference_entry") and not p.get("is_reference_heading")
            and p.get("text", "").strip() != "")


def build_chunks(paragraphs: list[dict]) -> list[dict]:
    """Group body paragraphs into ~TARGET_CHUNK_WORDS windows; split long paragraphs at sentence boundaries.

    Chunks never cross a heading, so a human-written section and an AI-written one are judged separately.
    """
    chunks: list[dict] = []
    cur: dict | None = None

    section = 0

    def flush() -> None:
        nonlocal cur
        if cur and cur["words"] > 0:
            chunks.append(cur)
        cur = None

    for p in paragraphs:
        if p.get("kind") in ("heading", "title"):
            flush()
            section += 1
            continue
        if not _eligible(p):
            continue
        text = p["text"].replace("￼", " ").strip()
        words = count_words(text)
        if words == 0:
            continue
        if words > MAX_CHUNK_WORDS:
            flush()
            piece, pw = [], 0
            for s, e in split_sentences(text):
                sent = text[s:e]
                sw = count_words(sent)
                if pw and pw + sw > TARGET_CHUNK_WORDS * 1.3:
                    chunks.append({"text": " ".join(piece), "words": pw, "parts": [(p["id"], pw)], "section": section})
                    piece, pw = [], 0
                piece.append(sent)
                pw += sw
            if piece:
                if pw < MIN_CHUNK_WORDS and chunks and chunks[-1]["parts"][0][0] == p["id"]:
                    last = chunks[-1]
                    last["text"] += " " + " ".join(piece)
                    last["words"] += pw
                    last["parts"][0] = (p["id"], last["parts"][0][1] + pw)
                else:
                    chunks.append({"text": " ".join(piece), "words": pw, "parts": [(p["id"], pw)], "section": section})
            continue
        if cur and cur["words"] + words > MAX_CHUNK_WORDS:
            flush()
        if cur is None:
            cur = {"text": text, "words": words, "parts": [(p["id"], words)], "section": section}
        else:
            cur["text"] += "\n\n" + text
            cur["words"] += words
            cur["parts"].append((p["id"], words))
        if cur["words"] >= TARGET_CHUNK_WORDS:
            flush()
    flush()
    # A trailing short chunk is merged into its predecessor in the same section when possible.
    merged: list[dict] = []
    for ch in chunks:
        if (ch["words"] < MIN_CHUNK_WORDS and merged and merged[-1]["section"] == ch["section"]
                and merged[-1]["words"] + ch["words"] <= MAX_CHUNK_WORDS * 1.2):
            merged[-1]["text"] += "\n\n" + ch["text"]
            merged[-1]["words"] += ch["words"]
            merged[-1]["parts"] += ch["parts"]
        else:
            merged.append(ch)
    return merged


def analyze(paragraphs: list[dict], on_progress: Callable[[int, int], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> dict:
    """Score a document. Blocking (runs model inference); call from a worker thread."""
    if not (libraries_installed() and models_installed()):
        return {"available": False, "reason": "AI detection isn't set up yet. Turn it on in Settings → AI detection."}
    t0 = time.perf_counter()
    chunks = build_chunks(paragraphs)
    scorable = [c for c in chunks if c["words"] >= MIN_CHUNK_WORDS]
    segments = []
    for i, ch in enumerate(scorable):
        if cancelled and cancelled():
            raise InterruptedError
        res = _detector.score([ch["text"]])[0]
        if res["score"] is None:
            continue
        p = probability(res["score"])
        segments.append({
            "paragraph_ids": [pid for pid, _ in ch["parts"]],
            "words": ch["words"],
            "score": round(res["score"], 5),
            "probability": round(p, 3),
            "label": label_for(p),
            "preview": ch["text"][:160],
        })
        if on_progress:
            on_progress(i + 1, len(scorable))

    total_words = sum(count_words(p["text"]) for p in paragraphs if _eligible(p))
    scored_words = sum(s["words"] for s in segments)
    ai_words = sum(s["words"] for s in segments if s["probability"] >= AI_THRESHOLD)
    possible_words = sum(s["words"] for s in segments if POSSIBLE <= s["probability"] < AI_THRESHOLD)
    para: dict[str, dict] = {}
    for s in segments:
        for pid in s["paragraph_ids"]:
            prev = para.get(pid)
            if prev is None or s["probability"] > prev["probability"]:
                para[pid] = {"probability": s["probability"], "label": s["label"]}

    if scored_words < MIN_DOC_WORDS:
        verdict = "insufficient"
        ai_percent = None
        possible_percent = None
        overall = None
    else:
        ai_percent = round(100 * ai_words / scored_words)
        possible_percent = round(100 * possible_words / scored_words)
        overall = round(sum(s["probability"] * s["words"] for s in segments) / scored_words, 3)
        verdict = "ai" if ai_percent >= 80 else "mixed" if ai_percent >= 20 else "human"
    return {
        "available": True,
        "method": METHOD,
        "method_version": method_version(),
        "model": model_label(),
        "ai_percent": ai_percent,
        "possible_percent": possible_percent,
        "human_percent": None if ai_percent is None else 100 - ai_percent - (possible_percent or 0),
        "overall_probability": overall,
        "verdict": verdict,
        "words_total": total_words,
        "words_scored": scored_words,
        "segments": segments,
        "paragraphs": para,
        "thresholds": {"ai": AI_THRESHOLD, "strong": STRONG, "possible": POSSIBLE, "min_chunk_words": MIN_CHUNK_WORDS},
        "duration_s": round(time.perf_counter() - t0, 2),
        "analyzed_at": time.time(),
    }


def ready() -> bool:
    return libraries_installed() and models_installed()


os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
