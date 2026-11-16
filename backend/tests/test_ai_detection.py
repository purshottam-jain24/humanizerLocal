"""AI-detection plumbing: chunking, calibration, aggregation and API.

The real models are not needed: the scorer is replaced by a deterministic
stand-in so these tests stay fast and offline.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.detection import ai_detector_service as det


def _para(i: int, text: str, kind: str = "paragraph", **kw) -> dict:
    return {"id": f"p{i}", "text": text, "kind": kind, "part": "body", "table": None, "word_count": len(text.split()), **kw}


SENT = "The committee reviewed the proposal in detail and agreed on several changes to the budget. "


def test_chunks_respect_headings_and_skip_non_prose():
    paras = [
        _para(0, "Introduction", kind="heading"),
        _para(1, SENT * 5),                      # ~80 words
        _para(2, SENT * 5),
        _para(3, "Methods", kind="heading"),
        _para(4, SENT * 6),
        _para(5, "A table cell " * 30, table={"id": "t"}),
        _para(6, "Smith, J. (2020). A reference entry. " * 5, is_reference_entry=True),
    ]
    chunks = det.build_chunks(paras)
    ids = [[pid for pid, _ in c["parts"]] for c in chunks]
    assert ids == [["p1", "p2"], ["p4"]]  # heading boundary kept; table + reference skipped


def test_long_paragraph_split_at_sentences():
    chunks = det.build_chunks([_para(1, SENT * 30)])  # 450 words
    assert len(chunks) >= 2
    assert all(c["parts"][0][0] == "p1" for c in chunks)
    assert all(c["text"].rstrip().endswith(".") for c in chunks)
    assert sum(c["words"] for c in chunks) == pytest.approx(450, abs=5)


def test_calibration_is_monotonic_and_strict():
    assert det.probability(0.2) < det.probability(0.9) < det.probability(0.99) < det.probability(0.9999)
    assert det.probability(0.9) < 0.5          # raw 0.9 is not enough to call a passage AI
    assert det.probability(0.9999) > 0.9
    assert 0.0 < det.probability(0.0) and det.probability(1.0) < 1.0


def test_analyze_aggregates_word_weighted(monkeypatch):
    monkeypatch.setattr(det, "libraries_installed", lambda: True)
    monkeypatch.setattr(det, "models_installed", lambda: True)
    # Section A will be scored "AI" (raw 0.99999), section B "human" (raw 0.05).
    monkeypatch.setattr(det._detector, "score", lambda texts: [{"score": 0.99999 if "AIAI" in x else 0.05, "tokens": 200} for x in texts])
    paras = [
        _para(0, "A", kind="heading"), _para(1, ("AIAI " + SENT) * 9),
        _para(2, "B", kind="heading"), _para(3, SENT * 9),
        _para(4, "C", kind="heading"), _para(5, "Too short to score."),
    ]
    r = det.analyze(paras)
    assert r["available"] and r["verdict"] == "mixed"
    assert 45 <= r["ai_percent"] <= 55
    assert r["paragraphs"]["p1"]["label"] == "ai"
    assert r["paragraphs"]["p3"]["label"] == "human"
    assert "p5" not in r["paragraphs"]


def test_analyze_insufficient_text(monkeypatch):
    monkeypatch.setattr(det, "libraries_installed", lambda: True)
    monkeypatch.setattr(det, "models_installed", lambda: True)
    monkeypatch.setattr(det._detector, "score", lambda texts: [{"score": 0.5, "tokens": 50} for _ in texts])
    r = det.analyze([_para(1, SENT * 5)])
    assert r["verdict"] == "insufficient" and r["ai_percent"] is None


def test_api_requires_confirmation_and_setup(fixtures_dir, monkeypatch):
    with TestClient(app) as c:
        st = c.get("/api/ai-detector").json()
        assert {"ready", "libraries_installed", "models_installed", "download_mb"} <= st.keys()
        assert c.post("/api/ai-detector/install", json={}).status_code == 400

        data = fixtures_dir["rich"].read_bytes()
        doc = c.post("/api/documents", files={"file": ("rich.docx", data)}).json()
        t0 = time.time()
        while c.get(f"/api/documents/{doc['id']}").json()["status"] not in ("ready", "failed") and time.time() - t0 < 60:
            time.sleep(0.1)

        monkeypatch.setattr(det, "ready", lambda: False)
        assert c.post(f"/api/documents/{doc['id']}/ai-detection").status_code == 409

        monkeypatch.setattr(det, "ready", lambda: True)
        monkeypatch.setattr(det, "libraries_installed", lambda: True)
        monkeypatch.setattr(det, "models_installed", lambda: True)
        monkeypatch.setattr(det._detector, "score", lambda texts: [{"score": 0.7, "tokens": 120} for _ in texts])
        r = c.post(f"/api/documents/{doc['id']}/ai-detection")
        assert r.status_code == 200, r.text
        rep = r.json()
        assert rep["available"] and rep["text_version"] == "original"
        stored = c.get(f"/api/documents/{doc['id']}/analysis").json()["reports"]["ai_detection"]
        assert stored["ai_percent"] == rep["ai_percent"]


def test_reprocess_endpoint_starts_a_job(fixtures_dir):
    """Regression: /process ran in a worker thread with no event loop and returned 500."""
    with TestClient(app) as c:
        doc = c.post("/api/documents?autostart=false", files={"file": ("rich.docx", fixtures_dir["rich"].read_bytes())}).json()
        r = c.post(f"/api/documents/{doc['id']}/process", json={"restart": True})
        assert r.status_code == 200 and r.json()["started"] is True
        t0 = time.time()
        while c.get(f"/api/documents/{doc['id']}").json()["status"] not in ("ready", "failed") and time.time() - t0 < 60:
            time.sleep(0.1)
        assert c.get(f"/api/documents/{doc['id']}").json()["status"] == "ready"


def test_layer_v3_file_is_consistent():
    """The shipped combining layer matches the feature layout the detector builds."""
    import json

    layer = json.loads(det.LAYER_PATH.read_text())
    n = 1 + 768 + 1 + 1024   # fakespot logit + pooled state, desklib logit + pooled state
    assert len(layer["mu"]) == len(layer["sd"]) == len(layer["w"]) == n
    assert all(s > 0 for s in layer["sd"])
    assert layer["calibration"]["a"] > 0


def test_version_follows_installed_models(monkeypatch):
    """Only the first model installed: version 2 fallback, upgrade offered. Both: version 3."""
    monkeypatch.setattr(det, "_have", lambda repo, files: repo == det.MODEL_REPO)
    assert det.method_version() == 2 and det.models_installed()
    st = det.status()
    assert st["upgrade_available"] and st["upgrade_mb"] == det.SIZES_MB[det.DESK_REPO]
    monkeypatch.setattr(det, "_have", lambda repo, files: True)
    assert det.method_version() == 3 and not det.status()["upgrade_available"]
    # v3 calibration is monotonic too, and "possible" sits between human and AI
    assert det.probability(0.01) < det.probability(0.5) < det.probability(0.99)
    assert det.label_for(0.3) == "possible" and det.label_for(0.1) == "human" and det.label_for(0.6) == "likely_ai"
