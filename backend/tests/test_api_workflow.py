"""End-to-end workflow through the HTTP API.

upload → processing events → analysis → suggestions (rule + streamed AI)
→ accept / reject / manual edit / undo / redo → preview → export → validate.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ollama.ollama_service import ollama
from app.storage.settings_store import update_settings
from tests import fake_ollama


@pytest.fixture(scope="module")
def client():
    srv, url = fake_ollama.start()
    old = ollama.base_url
    ollama.base_url = url
    with TestClient(app) as c:
        yield c
    ollama.base_url = old
    srv.shutdown()


def _wait_ready(client, doc_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/documents/{doc_id}").json()
        if d["status"] in ("ready", "failed", "cancelled"):
            return d
        time.sleep(0.1)
    raise AssertionError("processing timed out")


def _sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        data = [l[6:] for l in block.splitlines() if l.startswith("data: ")]
        ev = [l[7:] for l in block.splitlines() if l.startswith("event: ")]
        if data:
            out.append({"event": ev[0] if ev else None, "data": json.loads("\n".join(data))})
    return out


def test_rejects_invalid_uploads(client):
    r = client.post("/api/documents", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 422
    r = client.post("/api/documents", files={"file": ("broken.docx", b"PK\x03\x04garbage", "application/octet-stream")})
    assert r.status_code == 422
    r = client.post("/api/documents", files={"file": ("enc.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "x")})
    assert r.status_code == 422 and "password" in r.json()["detail"]["message"]


def test_full_workflow(client, fixtures_dir):
    update_settings({"ollama": {"model": "qwen3:4b", "embed_model": "embeddinggemma:latest"},
                     "analysis": {"llm_review": True, "llm_review_max_paragraphs": 3}})
    # Seed the corpus with a source that shares a passage with the document.
    src = ("Background reading. Urban green spaces play a crucial role in improving public health and the wellbeing of "
           "residents across many large cities around the world, according to several surveys.")
    r = client.post("/api/corpus/upload", files=[("files", ("source.txt", src.encode(), "text/plain"))])
    assert r.status_code == 200 and r.json()["imported"], r.json()

    data = fixtures_dir["rich"].read_bytes()
    sha_before = hashlib.sha256(data).hexdigest()
    r = client.post("/api/documents", files={"file": ("rich.docx", data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    doc = _wait_ready(client, doc_id)
    assert doc["status"] == "ready", doc
    assert all(s["state"] in ("completed", "skipped") for s in doc["stages"]), doc["stages"]

    # Event log replay: ordered, no duplicates, reconnection from a sequence number.
    with client.stream("GET", f"/api/documents/{doc_id}/events") as resp:
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "stream.idle" in body:
                break
    events = [e["data"] for e in _sse(body) if e["event"] is None]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    types = {e["type"] for e in events}
    for t in ("document.uploaded", "document.validated", "document.text.extracted", "document.structure.detected",
              "document.paragraph.started", "document.paragraph.completed", "document.finding.created",
              "document.analysis.completed", "document.suggestion.completed", "document.processing.completed"):
        assert t in types, t
    extracted = [e for e in events if e["type"] == "document.text.extracted"]
    st = client.get(f"/api/documents/{doc_id}/structure").json()
    assert [e["data"]["paragraph"]["id"] for e in extracted] == [p["id"] for p in st["paragraphs"]]
    mid = seqs[len(seqs) // 2]
    with client.stream("GET", f"/api/documents/{doc_id}/events", headers={"Last-Event-ID": str(mid)}) as resp:
        body2 = ""
        for chunk in resp.iter_text():
            body2 += chunk
            if "stream.idle" in body2:
                break
    replay = [e["data"]["seq"] for e in _sse(body2) if e["event"] is None]
    assert replay and min(replay) == mid + 1

    # Analysis reports exist, separated by module, with cautious AI-pattern labels.
    an = client.get(f"/api/documents/{doc_id}/analysis").json()
    assert set(an["reports"]) >= {"readability", "ai_patterns", "academic", "similarity"}
    assert an["reports"]["ai_patterns"]["level"] in ("No notable pattern detected", "Some patterns worth reviewing",
                                                     "Multiple patterns worth reviewing", "Insufficient text for meaningful analysis")
    assert "probability" not in json.dumps(an["reports"]["ai_patterns"]).lower()
    assert an["reports"]["similarity"]["external"]["checked"] is False  # off by default

    findings = client.get(f"/api/documents/{doc_id}/findings").json()
    cats = {(f["module"], f["category"]) for f in findings}
    assert ("writing", "grammar") in cats and ("writing", "spelling") in cats
    assert any(f["module"] == "similarity" and f["data"]["source"].get("title") == "source.txt" for f in findings)
    llm = [f for f in findings if f["source"] == "llm"]
    assert llm and all("DOES NOT EXIST" not in f["passage"] for f in llm)  # hallucinated passage discarded

    intro = next(p for p in st["paragraphs"] if p["text"].startswith("Urban green spaces"))
    sugs = client.get(f"/api/documents/{doc_id}/suggestions", params={"paragraph_id": intro["id"]}).json()
    spelling = next(s for s in sugs if s["original_text"] == "recieve")
    assert spelling["proposed_text"] == "receive" and spelling["status"] == "pending"

    # Accept a rule suggestion.
    r = client.post(f"/api/documents/{doc_id}/suggestions/{spelling['id']}/accept", json={})
    assert r.status_code == 200, r.text
    assert "receive data" in r.json()["text"]

    # Stream an AI paragraph rewrite.
    with client.stream("POST", f"/api/documents/{doc_id}/suggestions/generate",
                       json={"paragraph_id": intro["id"], "action": "fix_grammar", "mode": "light"}) as resp:
        gen_body = "".join(resp.iter_text())
    evs = _sse(gen_body)
    assert evs[0]["event"] == "started"
    tokens = [e for e in evs if e["event"] == "token"]
    assert len(tokens) > 3  # genuinely streamed in pieces
    done = evs[-1]
    assert done["event"] == "completed", evs[-1]
    ai = done["data"]["suggestion"]
    assert "the the" not in ai["proposed_text"] and ai["status"] == "pending"
    assert ai["meaning"]["level"] in ("likely_preserved", "review", "possible_change")

    # Alternative in the same group, then accept the first one.
    with client.stream("POST", f"/api/documents/{doc_id}/suggestions/generate",
                       json={"paragraph_id": intro["id"], "action": "fix_grammar", "alt_style": "concise", "group_id": ai["group_id"]}) as resp:
        alt = _sse("".join(resp.iter_text()))[-1]["data"]["suggestion"]
    assert alt["group_id"] == ai["group_id"]
    r = client.post(f"/api/documents/{doc_id}/suggestions/{ai['id']}/accept", json={})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/documents/{doc_id}/suggestions", params={"paragraph_id": intro["id"]}).json()
    assert next(s for s in client.get(f"/api/documents/{doc_id}/suggestions").json() if s["id"] == alt["id"])["status"] == "superseded"

    # Manual edit of another paragraph, protected marker enforcement.
    fn = next(p for p in st["paragraphs"] if p["text"].startswith("Green space access"))
    bad = client.post(f"/api/documents/{doc_id}/paragraphs/{fn['id']}/edit", json={"text": "No marker here."})
    assert bad.status_code == 409
    good_text = fn["text"].replace("unevenly distributed", "distributed unevenly")
    assert client.post(f"/api/documents/{doc_id}/paragraphs/{fn['id']}/edit", json={"text": good_text}).status_code == 200

    # Reject something, then undo/redo.
    other = next(s for s in client.get(f"/api/documents/{doc_id}/suggestions", params={"status": "pending"}).json())
    assert client.post(f"/api/documents/{doc_id}/suggestions/{other['id']}/reject").status_code == 200
    u = client.post(f"/api/documents/{doc_id}/undo").json()
    assert u["op"]["op_type"] == "reject" and u["can_redo"]
    assert next(s for s in client.get(f"/api/documents/{doc_id}/suggestions").json() if s["id"] == other["id"])["status"] == "pending"
    client.post(f"/api/documents/{doc_id}/undo")  # undo manual edit
    st2 = client.get(f"/api/documents/{doc_id}/structure").json()
    assert next(p for p in st2["paragraphs"] if p["id"] == fn["id"])["text"] == fn["text"]
    client.post(f"/api/documents/{doc_id}/redo")
    st3 = client.get(f"/api/documents/{doc_id}/structure").json()
    assert next(p for p in st3["paragraphs"] if p["id"] == fn["id"])["text"] == good_text
    hist = client.get(f"/api/documents/{doc_id}/revisions").json()
    assert [o["state"] for o in hist["ops"]].count("undone") == 1

    # Batch accept skips verification-required items.
    pend = client.get(f"/api/documents/{doc_id}/suggestions", params={"status": "pending"}).json()
    res = client.post(f"/api/documents/{doc_id}/suggestions/batch", json={"ids": [s["id"] for s in pend], "action": "accept"}).json()
    for sk in res["skipped"]:
        s = next(x for x in pend if x["id"] == sk["id"])
        assert ("verification" in sk["reason"] or "meaning" in sk["reason"] or "no longer" in sk["reason"]
                or "more than once" in sk["reason"] or "changed" in sk["reason"]), sk

    # Preview reflects changes; contains bookmark markers.
    pv = client.get(f"/api/documents/{doc_id}/preview.docx")
    assert pv.status_code == 200
    xml = zipfile.ZipFile(io.BytesIO(pv.content)).read("word/document.xml").decode()
    assert "hw_" in xml and "distributed unevenly" in xml

    # Export and validate.
    rep = client.post(f"/api/documents/{doc_id}/export").json()
    assert rep["ok"], rep
    assert rep["validation"]["ok"], rep["validation"]
    assert rep["paragraphs_changed"] >= 2
    dl = client.get(f"/api/exports/{rep['export_id']}/download")
    assert dl.status_code == 200
    exported = zipfile.ZipFile(io.BytesIO(dl.content))
    assert "hw_" not in exported.read("word/document.xml").decode()  # no preview markers in real export
    assert exported.read("word/media/image1.png") == zipfile.ZipFile(io.BytesIO(data)).read("word/media/image1.png")

    # The original is byte-identical.
    orig = client.get(f"/api/documents/{doc_id}/original.docx").content
    assert hashlib.sha256(orig).hexdigest() == sha_before

    # Reanalysis on modified paragraphs.
    ra = client.post(f"/api/documents/{doc_id}/reanalyze", json={"paragraph_ids": [intro["id"]]}).json()
    assert ra["paragraphs"] == 1


def test_generation_cancel_keeps_incomplete_draft(client, fixtures_dir):
    update_settings({"ollama": {"model": "qwen3:4b"}, "analysis": {"llm_review": False}})
    data = fixtures_dir["repetitive"].read_bytes()
    doc_id = client.post("/api/documents", files={"file": ("rep.docx", data, "x")}).json()["id"]
    _wait_ready(client, doc_id)
    st = client.get(f"/api/documents/{doc_id}/structure").json()
    pid = st["paragraphs"][0]["id"]
    # Starlette's TestClient buffers streamed bodies, so drive the generator directly
    # (the same code path the SSE endpoint uses) and cancel mid-stream.
    import asyncio

    from app.services.writing import suggestion_service

    async def run():
        evs = []
        n_tokens = 0
        async for ev in suggestion_service.generate(doc_id, paragraph_id=pid, action="concise"):
            evs.append(ev)
            if ev["type"] == "token":
                n_tokens += 1
                if n_tokens == 3:
                    assert suggestion_service.cancel(evs[0]["gen_id"])
        return evs

    fake_ollama.Handler.slow = True
    try:
        evs = asyncio.run(run())
    finally:
        fake_ollama.Handler.slow = False
    assert evs[-1]["type"] == "cancelled", evs[-1]
    s = evs[-1]["suggestion"]
    assert s["status"] == "incomplete"
    assert 0 < len(s["proposed_text"]) < 60  # partial text kept as a draft
    r = client.post(f"/api/documents/{doc_id}/suggestions/{s['id']}/accept", json={})
    assert r.status_code == 409  # incomplete drafts cannot be accepted as-is


def test_no_model_reports_clearly(client, fixtures_dir):
    update_settings({"ollama": {"model": None}})
    data = fixtures_dir["mixed"].read_bytes()
    doc_id = client.post("/api/documents", files={"file": ("m.docx", data, "x")}).json()["id"]
    _wait_ready(client, doc_id)
    pid = client.get(f"/api/documents/{doc_id}/structure").json()["paragraphs"][0]["id"]
    with client.stream("POST", f"/api/documents/{doc_id}/suggestions/generate", json={"paragraph_id": pid}) as resp:
        evs = _sse("".join(resp.iter_text()))
    assert evs[-1]["event"] == "error" and "No local model selected" in evs[-1]["data"]["message"]


def test_long_document_processes(client, fixtures_dir):
    update_settings({"analysis": {"llm_review": False}})
    data = fixtures_dir["long"].read_bytes()
    t0 = time.time()
    doc_id = client.post("/api/documents", files={"file": ("long.docx", data, "x")}).json()["id"]
    d = _wait_ready(client, doc_id, timeout=120)
    assert d["status"] == "ready"
    st = client.get(f"/api/documents/{doc_id}/structure").json()
    assert st["summary"]["paragraphs"] >= 400
    assert time.time() - t0 < 60
    # Delete removes everything.
    assert client.delete(f"/api/documents/{doc_id}").json()["ok"]
    assert client.get(f"/api/documents/{doc_id}").status_code == 404
