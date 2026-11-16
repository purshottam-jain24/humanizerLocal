"""Humanizer: segmentation, protection, analysis, validation, pipeline (via the fake Ollama), API, profiles, export."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.humanizer import analysis, blocks as B, pipeline, style_profiles, validation
from app.services.ollama.ollama_service import ollama
from app.storage.settings_store import update_settings
from tests import fake_ollama

ACADEMIC = """# The role of urban green spaces

In today's fast-paced world, it is important to note that urban green spaces play a crucial role in public health. Furthermore, a 2019 survey of 4,200 residents found that 62% visited a park weekly (Smith & Lee, 2019). Moreover, researchers argue that "access to nature reduces stress" [3].

## Methods

Furthermore, we surveyed 312 households in March 2021. Additionally, the response rate was 48%. Moreover, the data were analysed in order to identify trends.

- Furthermore, parks were mapped using GIS.
- Moreover, distance to the nearest park was measured.

> "Green space is not a luxury." — City report

Urban green spaces were studied across several neighbourhoods with different income levels, housing density and access to public transport, so that the comparison would reflect the city as a whole rather than one district. Each neighbourhood was visited twice, once in winter and once in summer, and the same questionnaire was used on both visits.

In conclusion, green spaces matter. See https://example.org/report for details.
"""

EMAIL = """Hi Sam,

I hope this email finds you well. I am writing to inform you that the meeting has been moved to Thursday, 14 March at 10:00. Please do not hesitate to reach out if you have any questions.

Best regards,
Priya
"""


@pytest.fixture(scope="module")
def client():
    srv, url = fake_ollama.start()
    old = ollama.base_url
    ollama.base_url = url
    update_settings({"ollama": {"model": "qwen3:4b"}})
    with TestClient(app) as c:
        yield c
    ollama.base_url = old
    srv.shutdown()


def _sse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        data = [l[6:] for l in block.splitlines() if l.startswith("data: ")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


# ------------------------------------------------------------------ blocks
@pytest.mark.parametrize("text", [ACADEMIC, EMAIL, "One line", "\n\nLeading blanks\n\n\nTrailing\n\n", "a\nb\n- x\n- y\n1. z\n",
                                  "```\ncode here\n```\nText after.\n", "# H\nNo blank after heading\n"])
def test_blocks_round_trip_exactly(text):
    assert B.join_blocks(B.parse_blocks(text)) == text


def test_block_kinds():
    bl = B.parse_blocks(ACADEMIC)
    kinds = [b.kind for b in bl]
    assert kinds.count("heading") == 2 and kinds.count("list_item") == 2 and "quote" in kinds
    assert all(not b.rewritable for b in bl if b.kind in ("heading", "quote"))
    assert [b.text for b in bl if b.kind == "list_item"][0].startswith("Furthermore, parks")


def test_long_paragraph_is_split_and_rejoined():
    para = " ".join(f"Sentence number {i} explains one more detail about the topic at hand." for i in range(80))
    bl = B.parse_blocks(para + "\n\nNext paragraph.\n")
    parts = [b for b in bl if b.part_of == 0]
    assert len(parts) >= 2 and all(p.words <= B.MAX_BLOCK_WORDS for p in parts)
    assert B.join_blocks(bl) == para + "\n\nNext paragraph.\n"


def test_protection_covers_citations_quotes_links():
    t = 'As noted (Smith & Lee, 2019), "access to nature reduces stress" [3]; see https://example.org/x and mail a@b.com.'
    p, m = B.protect(t)
    kinds = sorted(k[1] for k in m)
    assert kinds == ["C", "C", "L", "Q", "U"], m
    assert "Smith" not in p and "access to nature" not in p
    restored, missing, extra = B.restore(p, m)
    assert restored == t and not missing and not extra
    _, missing, _ = B.restore(p.replace("⟦Q1⟧", ""), m)
    assert missing == ["⟦Q1⟧"]


# ------------------------------------------------------------------ analysis
def test_analysis_detects_patterns():
    prof = analysis.analyze(B.parse_blocks(ACADEMIC))
    ids = {i["id"] for i in prof["issues"]}
    assert {"stock_transitions", "stock_phrases", "cliche_intro"} <= ids
    assert prof["metrics"]["sentences"] > 5 and prof["guidance"]


def test_analysis_on_clean_text_is_quiet():
    text = "We moved the meeting to Thursday. It starts at ten, in room 4. Bring the draft budget if you can; we'll go through it line by line."
    prof = analysis.analyze(B.parse_blocks(text))
    assert not [i for i in prof["issues"] if i["severity"] == "high"]


# ------------------------------------------------------------------ validation
def test_validation_flags_meaning_changes():
    o = "The trial did not reduce mortality in 2019 (Smith, 2020). Patients said “it was hard”."
    r = "The trial reduced mortality in 2018. Patients found it hard."
    types = {f["type"] for f in validation.compare_block(1, o, r)}
    assert {"citation", "quotation", "number_or_date", "certainty"} <= types


def test_validation_quiet_for_style_only_change():
    o = "Furthermore, the committee approved the budget of $4,000 on Monday."
    r = "The committee approved the $4,000 budget on Monday."
    assert validation.compare_block(1, o, r) == []


def test_validate_texts_detects_missing_paragraph():
    res = validation.validate_texts("Para one is here.\n\nPara two has 42 items.\n", "Para one is here.\n")
    assert res["flags"] and res["flags"][0]["type"] == "missing_information"


# ------------------------------------------------------------------ sections
def test_sections_respect_headings_and_size():
    long = "\n\n".join(f"Paragraph {i} " + "word " * 120 + "end." for i in range(8))
    text = "# A\n\n" + long + "\n\n# B\n\nShort final paragraph here.\n"
    bl = B.parse_blocks(text)
    secs = pipeline.build_sections(bl)
    assert len(secs) >= 3
    # The short final section is merged with the one before it rather than costing its own model call.
    assert secs[-1].heading and secs[-1].heading.endswith("B")
    assert all(s.words <= pipeline.CHUNK_WORDS or len(s.block_ids) == 1 for s in secs)
    ordered = [i for s in secs for i in s.block_ids]
    assert ordered == sorted(ordered)


def test_tiny_sections_are_merged_across_headings():
    text = "# One\n\nA short opening paragraph with a few words.\n\n# Two\n\nAnother brief paragraph that says little.\n\n# Three\n\n" + \
        " ".join(["This paragraph is long enough to stand on its own as a section."] * 30) + "\n"
    secs = pipeline.build_sections(B.parse_blocks(text))
    assert len(secs) == 2
    assert secs[0].heading == "One / Two"
    assert [s.index for s in secs] == [0, 1]


# ------------------------------------------------------------------ API: pipeline
def _run(client, **body):
    r = client.post("/api/humanize/run", json={"ai_validation": True, **body})
    assert r.status_code == 200, r.text
    return _sse(r.text)


def test_full_pipeline_preserves_structure_and_exact_material(client):
    evs = _run(client, text=ACADEMIC, mode="academic", intensity="medium")
    types = [e["type"] for e in evs]
    assert types[0] == "accepted" and types[-1] == "completed", types[-3:]
    plan = next(e for e in evs if e["type"] == "plan")
    assert plan["sections"] and all(s["words"] > 0 for s in plan["sections"])
    # Live model output is reported (throttled) with the text produced so far.
    assert any(e["type"] == "activity" and e.get("tail") for e in evs)
    stages = [(e["stage"], e["state"]) for e in evs if e["type"] == "stage"]
    for key, _ in pipeline.STAGES:
        assert any(s == key and st in ("completed", "skipped") for s, st in stages), key
    res = evs[-1]["result"]
    out = res["output"]
    assert res["original"] == ACADEMIC  # original returned unchanged
    assert "Furthermore," not in out and "Moreover," not in out
    for exact in ["(Smith & Lee, 2019)", '"access to nature reduces stress"', "[3]", "https://example.org/report",
                  "# The role of urban green spaces", "## Methods", '> "Green space is not a luxury." — City report', "4,200", "62%", "March 2021"]:
        assert exact in out, exact
    assert out.count("\n- ") == 2
    assert "<think>" not in out and "Looking at the blocks" not in out
    assert res["context"]["source"] == "ai"
    assert "NOT IN TEXT TERM" not in res["context"]["key_terms"]  # ungrounded AI terms dropped
    assert res["validation"]["ai_checked"] is True
    assert any(e["type"] == "section" for e in evs)


def test_light_intensity_skips_ai_semantic_map_and_flow(client):
    evs = _run(client, text=EMAIL, mode="professional", intensity="light")
    grammar = [e for e in evs if e["type"] == "stage" and e["stage"] == "grammar"]
    assert grammar and grammar[-1]["state"] == "completed" and "opening" not in (grammar[-1]["detail"] or "")
    ctx = [e for e in evs if e["type"] == "stage" and e["stage"] == "context"][-1]
    assert "Short text" in ctx["detail"] and "mapped" not in ctx["detail"] and "qualifier" in ctx["detail"]
    assert evs[-1]["result"]["usage"]["by_stage"].get("semantic_map") is None
    out = evs[-1]["result"]["output"]
    assert out.endswith("Best regards,\nPriya\n")  # line-structured sign-off kept exactly
    assert "Thursday, 14 March at 10:00" in out


def test_lost_protected_token_keeps_original_block(client):
    text = 'FAKE_DROP_TOKEN Furthermore, the panel said "costs will rise" (Ito, 2022) during the hearing today.'
    res = _run(client, text=text, intensity="medium")[-1]["result"]
    assert res["output"] == text
    assert res["notes"] and res["notes"][0]["type"] == "kept_original"


def test_missing_block_in_model_reply_is_kept_and_noted(client):
    text = "First paragraph is fine and complete here.\n\nFAKE_SKIP_BLOCK Moreover, this one is skipped by the model.\n"
    res = _run(client, text=text)[-1]["result"]
    assert "FAKE_SKIP_BLOCK Moreover, this one is skipped" in res["output"]
    assert any(n["type"] == "kept_original" for n in res["notes"])


def test_ai_validation_flags_and_toggle(client):
    text = "FAKE_AI_FLAG Furthermore, the new policy will reduce costs for most small businesses next year."
    res = _run(client, text=text)[-1]["result"]
    assert any(f["source"] == "ai" for f in res["flags"])
    res2 = _run(client, text=text, ai_validation=False)[-1]["result"]
    assert not any(f["source"] == "ai" for f in res2["flags"]) and res2["validation"]["ai_checked"] is False


def test_long_document_is_chunked_in_order(client):
    paras = [f"Furthermore, section {i} describes finding number {i} in detail, with enough words to count as real prose for chunking purposes here."
             for i in range(60)]
    text = "# Report\n\n" + "\n\n".join(paras) + "\n"
    evs = _run(client, text=text, ai_validation=False)
    res = evs[-1]["result"]
    assert res["sections"] >= 3
    out_paras = [p for p in res["output"].split("\n\n") if p.strip()]
    assert len(out_paras) == 61
    for i in range(60):
        assert f"section {i} describes finding number {i}" in out_paras[i + 1].lower()
    progress = [e["progress"] for e in evs if e["type"] == "stage" and e["stage"] == "rewriting" and e.get("progress")]
    assert progress[-1]["done"] == progress[-1]["total"] == res["sections"]


def test_run_validation_errors(client):
    assert client.post("/api/humanize/run", json={"text": "   "}).status_code == 422
    assert client.post("/api/humanize/run", json={"text": "Hello there.", "mode": "custom"}).status_code == 422
    assert client.post("/api/humanize/run", json={"text": "Hello there.", "intensity": "max"}).status_code == 422
    assert client.post("/api/humanize/run", json={"text": "Hello there.", "profile_id": "sty_missing"}).status_code == 404
    only_heading = _run(client, text="# Just a heading\n")
    assert only_heading[-1]["type"] == "error"


def test_model_unavailable_is_reported(client):
    update_settings({"ollama": {"model": "not-installed:1b"}})
    try:
        evs = _run(client, text="Furthermore, this is a sentence that needs work.")
        assert evs[-1]["type"] == "error" and evs[-1]["code"] == "model_unavailable"
    finally:
        update_settings({"ollama": {"model": "qwen3:4b"}})


# ------------------------------------------------------------------ API: selection, analyze, validate
def test_selection_rewrite(client):
    r = client.post("/api/humanize/rewrite-selection", json={"text": "In order to win, we we must train.", "before": "Intro.", "after": "Outro.",
                                                             "action": "improve"})
    evs = _sse(r.text)
    done = evs[-1]
    assert done["type"] == "completed", evs
    assert done["proposed"] == "To win, we must train."
    assert client.post("/api/humanize/rewrite-selection", json={"text": "x y", "action": "custom"}).status_code == 422


def test_analyze_and_validate_endpoints(client):
    r = client.post("/api/humanize/analyze", json={"text": ACADEMIC})
    assert r.status_code == 200 and r.json()["issues"]
    r = client.post("/api/humanize/validate", json={"original": "We saw 12 cases (Kim, 2021).", "revised": "We saw cases."})
    types = {f["type"] for f in r.json()["flags"]}
    assert {"citation", "number_or_date"} <= types


def test_options(client):
    o = client.get("/api/humanize/options").json()
    assert {m["value"] for m in o["modes"]} == {"natural", "conversational", "academic", "professional", "simple", "personal", "custom"}
    assert [i["value"] for i in o["intensities"]] == ["light", "medium", "extensive"]
    assert len(o["selection_actions"]) == 13 and {"flow", "tone"} <= {a["value"] for a in o["selection_actions"]}
    assert [a["value"] for a in o["approaches"]] == ["balanced", "restructured", "concise", "flowing"]
    assert o["max_variations"] == 3 and any(t["value"] == "friendly" for t in o["tones"])
    assert o["provider"]["local"] is True and o["provider"]["model_ready"] is True


def test_rate_limit(client):
    from app.api import humanizer

    humanizer.limiter.hits.clear()
    codes = [client.post("/api/humanize/profiles/analyze", json={"samples": "too short"}).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10 and codes[10] == 429
    humanizer.limiter.hits.clear()


# ------------------------------------------------------------------ profiles
SAMPLES = ("I've always thought the best way to learn something is to break it. So that's what I do — I take things apart, "
           "see what happens, and put them back together. Sometimes it works. Often it doesn't! But you learn either way. ") * 6


def test_profile_lifecycle(client):
    r = client.post("/api/humanize/profiles/analyze", json={"samples": SAMPLES})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["ai_used"] and draft["stats"]["contractions_per_100_words"] > 1
    assert all(len(e.split()) <= 4 for e in draft["expressions"])
    r = client.post("/api/humanize/profiles", json={"name": "Blog voice", "description": draft["description"], "traits": draft["traits"],
                                                    "expressions": draft["expressions"], "stats": draft["stats"], "sample_words": draft["sample_words"]})
    pid = r.json()["id"]
    # Raw samples are not stored anywhere in the profile.
    assert "take things apart" not in json.dumps(r.json())
    r = client.patch(f"/api/humanize/profiles/{pid}", json={"name": "Renamed", "traits": ["Short sentences"]})
    assert r.json()["name"] == "Renamed" and r.json()["traits"] == ["Short sentences"]
    assert any(p["id"] == pid for p in client.get("/api/humanize/profiles").json())
    evs = _run(client, text="Furthermore, this paragraph should take the profile into account when it is rewritten.", profile_id=pid)
    assert evs[-1]["type"] == "completed"
    assert client.delete(f"/api/humanize/profiles/{pid}").json()["ok"]
    assert client.delete(f"/api/humanize/profiles/{pid}").status_code == 404


def test_profile_prompt_never_contains_samples():
    stats = style_profiles.sample_stats(SAMPLES)
    block = style_profiles.prompt_block({"name": "x", "description": "", "traits": [], "expressions": style_profiles.habitual_expressions(SAMPLES),
                                         "stats": stats})
    assert "take things apart, see what happens" not in block


# ------------------------------------------------------------------ extraction & export
def test_extract_txt_and_docx_and_errors(client, fixtures_dir):
    r = client.post("/api/humanize/extract", files={"file": ("a.txt", ACADEMIC.encode(), "text/plain")})
    assert r.status_code == 200 and r.json()["text"].startswith("# The role")
    r = client.post("/api/humanize/extract", files={"file": ("rich.docx", fixtures_dir["rich"].read_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "docx" and r.json()["words"] > 20
    assert client.post("/api/humanize/extract", files={"file": ("x.doc", b"abc", "x")}).status_code == 422
    assert client.post("/api/humanize/extract", files={"file": ("x.txt", b"", "x")}).status_code == 422


def test_extract_pdf(client):
    pdf = _minimal_pdf("Hello from a PDF document. It has two sentences.")
    r = client.post("/api/humanize/extract", files={"file": ("doc.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    assert "Hello from a PDF document" in r.json()["text"]


@pytest.mark.parametrize("fmt", ["txt", "md", "docx"])
def test_export_formats(client, fmt):
    r = client.post("/api/humanize/export", json={"text": ACADEMIC, "format": fmt, "filename": "My essay"})
    assert r.status_code == 200
    assert "My essay." + fmt in r.headers["content-disposition"]
    if fmt == "docx":
        from docx import Document

        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert z.testzip() is None
        d = Document(io.BytesIO(r.content))
        styles = [p.style.name for p in d.paragraphs]
        assert "Heading 1" in styles and "Heading 2" in styles and "List Bullet" in styles and "Quote" in styles
        assert any("(Smith & Lee, 2019)" in p.text for p in d.paragraphs)
    elif fmt == "txt":
        assert r.content.decode().startswith("The role of urban green spaces")
    else:
        assert r.content.decode() == ACADEMIC


def _minimal_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


# ------------------------------------------------------------------ independence from the detector
def test_humanizer_does_not_depend_on_detector():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for f in list((root / "services" / "humanizer").glob("*.py")) + [root / "api" / "humanizer.py"]:
        src = f.read_text()
        assert "services.detection" not in src and "ai_detector" not in src, f


def test_negation_idioms_are_not_flagged_but_real_negations_are():
    assert validation.compare_block(1, "Please do not hesitate to reach out if you have questions.", "If you have questions, contact me.") == []
    assert any(f["type"] == "certainty" for f in validation.compare_block(1, "The drug does not work.", "The drug works."))


TECH_DOC = """## Installation

Furthermore, run `pip install humanwrite==2.1.0` in order to install the package. Moreover, Python 3.12 or newer is required.

```bash
pip install humanwrite==2.1.0
export HW_MODE=strict  # Furthermore, this comment must not change
```

Additionally, see https://docs.example.com/install#linux for Linux notes.
"""


def test_technical_doc_preserves_code_exactly(client):
    res = _run(client, text=TECH_DOC, mode="professional", ai_validation=False)[-1]["result"]
    out = res["output"]
    assert "```bash\npip install humanwrite==2.1.0\nexport HW_MODE=strict  # Furthermore, this comment must not change\n```" in out
    assert "`pip install humanwrite==2.1.0`" in out and "https://docs.example.com/install#linux" in out
    assert "Python 3.12" in out and "## Installation" in out
    assert "Moreover," not in out.split("```")[0]


def test_repetitive_and_poorly_structured_text_is_profiled():
    text = ("The project is good. The project is useful. The project is important. The project is very good and the project "
            "helps people. The project is good because the project is useful and the project is important for the project team.")
    prof = analysis.analyze(B.parse_blocks(text))
    ids = {i["id"] for i in prof["issues"]}
    assert "repetitive_openings" in ids and ("redundancy" in ids or "vocabulary_repetition" in ids)


def test_restore_accepts_exactly_spelled_out_protected_text():
    p, m = B.protect('Parks help (Kaplan, 1995). He said "it works" [3].')
    spelled = p.replace("⟦C1⟧", "(Kaplan, 1995)")
    _, missing, extra = B.restore(spelled, m)
    assert not missing and not extra
    altered = p.replace("⟦Q1⟧", "'it works'")
    _, missing, _ = B.restore(altered, m)
    assert missing == ["⟦Q1⟧"]


def test_block_losing_token_is_retried_once(client):
    # The fake drops tokens only when FAKE_DROP_TOKEN is present, on every attempt -> retry happens, then original kept.
    text = 'FAKE_DROP_TOKEN Furthermore, the panel said "costs will rise" (Ito, 2022) during the hearing today.'
    evs = _run(client, text=text, ai_validation=False)
    assert any(e["type"] == "activity" and e.get("activity") == "retrying" for e in evs)
    assert evs[-1]["result"]["output"] == text


# ------------------------------------------------------------------ speed
def test_fast_speed_prefers_a_model_without_reasoning(monkeypatch):
    import asyncio

    from app.services.humanizer import llm

    async def with_fast():
        return {"qwen3:4b", "qwen3:4b-instruct-2507-q4_K_M"}

    async def without_fast():
        return {"qwen3:4b"}

    monkeypatch.setattr(llm.ollama, "installed_names", with_fast)
    fast = asyncio.run(llm.resolve_model("fast"))
    assert fast["model"] == "qwen3:4b-instruct-2507-q4_K_M" and fast["fast_model"]
    thorough = asyncio.run(llm.resolve_model("thorough"))
    assert thorough["model"] != fast["model"] and thorough["think"] is None
    monkeypatch.setattr(llm.ollama, "installed_names", without_fast)
    fallback = asyncio.run(llm.resolve_model("fast"))
    # No fast model installed: the selected model is used with its reasoning switched off.
    assert fallback["think"] is False and not fallback["fast_model"]


def test_meaning_check_batches_several_sections_per_call():
    text = "\n\n".join(" ".join(["Sentence number %d is here." % i] * 30) for i in range(10))
    bl = B.parse_blocks(text)
    batches = pipeline._validation_batches([b.id for b in bl if b.words], {b.id: b for b in bl})
    assert sum(len(b) for b in batches) == len([b for b in bl if b.words])
    assert len(batches) < len([b for b in bl if b.words])
    assert all(sum(bl[i].words for i in g) <= pipeline.VALIDATION_WORDS or len(g) == 1 for g in batches)


def test_truncated_meaning_check_reply_keeps_complete_issues():
    from app.services.humanizer import llm

    cut = '{"issues": [{"block": 3, "type": "certainty", "severity": "medium", "original": "runs", "revised": "can run", ' \
          '"explanation": "Adds a hedge."}, {"block": 4, "type": "changed_claim", "severity": "hi'
    got = llm.parse_issues(cut)
    assert [i["block"] for i in got["issues"]] == [3]


def test_short_labels_are_not_rewritten():
    bl = B.parse_blocks("Answer 6.\n\nA lock is a variable associated with a data item that controls access to it by transactions.\n")
    pipeline._mark_unrewritable(bl)
    assert [b.rewritable for b in bl if b.words] == [False, True]


# ------------------------------------------------------------------ multi-stage engine
def test_every_stage_reports_real_work(client):
    res = _run(client, text=ACADEMIC, mode="academic", intensity="medium")[-1]["result"]
    by_stage = res["usage"]["by_stage"]
    assert {"context", "semantic_map", "structure", "validation"} <= set(by_stage), by_stage
    assert res["usage"]["calls"] == res["llm_calls"] == sum(v["calls"] for v in by_stage.values())
    rep = res["stage_report"]
    assert {"structure", "vocabulary", "style", "grammar", "validation"} <= set(rep)
    assert rep["structure"]["applied"] > 0
    # Each block carries a trail of what each stage did to it.
    assert any(t["stage"] == "structure" and t["status"] == "applied" for b in res["blocks"] for t in b["trail"])


def test_semantic_map_keeps_only_grounded_points(client):
    evs = _run(client, text=ACADEMIC, mode="academic", intensity="medium")
    ctx = evs[-1]["result"]["context"]
    pts = [p for v in ctx["semantic_map"].values() for p in v["points"]]
    assert pts and not any("zebras" in p for p in pts)
    detail = [e for e in evs if e["type"] == "stage" and e["stage"] == "context"][-1]["detail"]
    assert "ungrounded point(s) discarded" in detail
    assert ctx["ledger"] and any(l["numbers"] for l in ctx["ledger"].values())


def test_meaning_error_is_repaired_after_validation(client):
    text = "FAKE_NUMBER_DRIFT The maintenance window runs from 22:00 to 02:00 UTC, during which the dashboard is unavailable to every customer."
    res = _run(client, text=text, mode="professional")[-1]["result"]
    assert "22:00" in res["output"] and "00:00 to" not in res["output"]
    assert res["validation"]["repaired"] == 1 and any(n["type"] == "repaired" for n in res["notes"])
    assert not any(f["type"] == "number_or_date" for f in res["flags"])


def test_unrepairable_meaning_error_reverts_block(client):
    text = "FAKE_NUMBER_DRIFT FAKE_UNREPAIRABLE The maintenance window runs from 22:00 to 02:00 UTC, so the dashboard is down for every customer."
    res = _run(client, text=text, mode="professional")[-1]["result"]
    assert res["output"] == text
    assert res["validation"]["reverted"] == 1
    assert any(n["type"] == "kept_original" and n["message"].startswith("Kept as written") for n in res["notes"])


def test_vocabulary_stage_removes_generic_words_the_rewrite_introduced(client):
    text = "FAKE_INTRODUCE_VOCAB The team uses a small script that helps with imports every morning before the nightly build starts."
    res = _run(client, text=text, ai_validation=False)[-1]["result"]
    assert "utilize" not in res["output"] and "seamlessly" not in res["output"]
    assert res["stage_report"]["vocabulary"]["applied"] == 1
    trail = next(b["trail"] for b in res["blocks"] if b["rewritable"])
    assert any(t["stage"] == "vocabulary" and t["status"] == "applied" for t in trail)


def test_refinement_that_breaks_meaning_is_rejected(client):
    text = "FAKE_INTRODUCE_VOCAB FAKE_REFINE_BREAK The team uses 3 scripts that helps with 40 imports every morning before 6 builds."
    res = _run(client, text=text, ai_validation=False)[-1]["result"]
    for n in ("3", "40", "6"):
        assert n in res["output"]
    trail = next(b["trail"] for b in res["blocks"] if b["rewritable"])
    rej = [t for t in trail if t["stage"] == "vocabulary" and t["status"] == "rejected"]
    assert rej and "meaning" in rej[0]["reason"]


def test_multiple_variations_share_understanding_and_rebuild_exactly(client):
    evs = _run(client, text=ACADEMIC, mode="academic", approaches=["balanced", "concise", "restructured"], ai_validation=False)
    res = evs[-1]["result"]
    assert [v["approach"] for v in res["variants"]] == ["balanced", "concise", "restructured"]
    assert res["settings"]["approaches"] == ["balanced", "concise", "restructured"]
    for v in res["variants"]:
        rebuilt = "".join(b["prefix"] + b["revised"] + b["sep"] for b in v["blocks"])
        assert rebuilt == v["output"]
    # Understanding runs once; the structural rewrite runs once per section per variation.
    assert res["usage"]["by_stage"]["context"]["calls"] == 1
    assert res["usage"]["by_stage"]["structure"]["calls"] >= 3 * res["sections"]
    assert {e.get("variant") for e in evs if e["type"] == "stage" and e["stage"] == "rewriting"} == {0, 1, 2}
    assert client.post("/api/humanize/run", json={"text": "Hello there.", "approaches": ["weird"]}).status_code == 422
    assert client.post("/api/humanize/run", json={"text": "Hello there.", "approaches": []}).status_code == 422


def test_selection_alternatives_and_tone(client):
    r = client.post("/api/humanize/rewrite-selection", json={"text": "In order to win, we we must train.", "action": "rephrase", "alternatives": 3})
    done = _sse(r.text)[-1]
    assert done["type"] == "completed" and len(done["alternatives"]) == 3
    assert len({a["text"] for a in done["alternatives"]}) == 3
    r = client.post("/api/humanize/rewrite-selection", json={"text": "Send the report today.", "action": "tone", "tone": "friendly"})
    assert _sse(r.text)[-1]["type"] == "completed"
    assert client.post("/api/humanize/rewrite-selection", json={"text": "x y z", "action": "tone", "tone": "angry"}).status_code == 422
    assert client.post("/api/humanize/rewrite-selection", json={"text": "x y z", "action": "flow"}).status_code == 200


# ------------------------------------------------------------------ deterministic checks
def test_qualifier_and_negation_checks():
    q = validation.qualifier_changes
    assert q("overestimates sleep by roughly 30 minutes", "overestimates sleep by 30 minutes")[0][0] == "high"
    assert q("overestimates sleep by roughly 30 minutes", "overestimates sleep by about 30 minutes") == []
    assert q("Let us talk about the plan.", "Let us discuss the plan.") == []  # "about" without a quantity is not a hedge
    assert q("The most important thing is practice.", "Practice matters above all.") == []
    assert q("Please let me know should you have questions.", "Let me know if you have questions.") == []
    assert any("recommendation" in m for _, m in q("Universities should consider sleep.", "Universities must plan for sleep."))
    n = validation.negation_changes
    assert n("The link is not necessarily causal.", "The link is necessarily causal.")
    assert n("The vendor has not yet delivered.", "The vendor hasn’t delivered yet.") == []
    assert n("Please do not hesitate to ask.", "Feel free to ask.") == []
    assert validation.compare_block(1, "We run 3 Redis nodes.", "We run three Redis nodes.") == []


def test_vocabulary_and_style_detectors():
    from app.services.humanizer import refine

    v = refine.vocabulary_findings("We use a cache to make reads fast.", "We utilize a robust cache to seamlessly make reads fast.",
                                   intensity="medium", mode="natural", key_terms=[])
    kinds = {f.kind for f in v}
    assert "introduced_vocab" in kinds
    assert refine.vocabulary_findings("The write-through cache helps.", "The cache helps.", intensity="light", mode="natural",
                                      key_terms=["write-through cache"])[0].kind == "term_changed"
    s = refine.style_findings("Could your team please not deploy schema changes until the migration is complete next week?",
                              "Don't deploy any schema changes until the migration is complete next week, team.", mode="professional",
                              intensity="medium", profile_stats=None)
    assert any(f.kind == "courtesy" for f in s)
    calm = "It is fine and we do not mind the wait at all, because it is quiet here and the staff are calm, kind and patient with everyone who comes in."
    s = refine.style_findings(calm, calm, mode="conversational",
                              intensity="medium", profile_stats=None)
    assert any(f.kind == "contractions" for f in s)
    assert refine.style_findings("We do not mind.", "We don't mind at all, honestly, it's okay and you know it's fine by us.",
                                 mode="academic", intensity="medium", profile_stats=None)


def test_refine_guard():
    from app.services.humanizer import refine

    before = "The cache adds approximately 3 ms of latency to every write request."
    assert refine.guard("vocabulary", before, before.replace("approximately", "about"), n_findings_before=1, n_findings_after=0,
                        high_flags_before=0, high_flags_after=0, introduced_vocab_after=0) is None
    assert "meaning" in refine.guard("vocabulary", before, before, n_findings_before=1, n_findings_after=0, high_flags_before=0,
                                     high_flags_after=1, introduced_vocab_after=0)
    assert "far more" in refine.guard("vocabulary", before, "Something entirely different is written here now.", n_findings_before=1,
                                      n_findings_after=0, high_flags_before=0, high_flags_after=0, introduced_vocab_after=0)
    assert "did not resolve" in refine.guard("style", before, before, n_findings_before=2, n_findings_after=2, high_flags_before=0,
                                             high_flags_after=0, introduced_vocab_after=0)


def test_semantic_ledger_and_grounding():
    from app.services.humanizer import semantics

    led = semantics.ledger("Sleep loss may impair memory, although the effect varies. The link is not necessarily causal, "
                           "because workload could confound it. Students slept roughly 34 minutes more.")
    assert any("may" in q for q in led["qualifiers"]) and any("roughly" in q for q in led["qualifiers"])
    assert any("not necessarily" in n for n in led["negations"])
    assert {l["kind"] for l in led["links"]} >= {"concession", "cause"}
    assert "34 minutes" in led["numbers"]
    assert semantics.grounded("Sleep loss may impair memory", "Sleep loss may impair memory, although…")
    assert not semantics.grounded("zebras migrate across savannah plains", "Sleep loss may impair memory.")


def test_whole_number_tokens_catch_rearranged_times_but_allow_reformatting():
    flags = validation.compare_block(1, "The window runs from 22:00 to 02:00 UTC.", "The window runs from 22:00 to 00:02 UTC.")
    assert flags and flags[0]["type"] == "number_or_date" and "02:00" in flags[0]["message"]
    assert validation.compare_block(1, "Costs fell 12%.", "Costs fell 21%.")[0]["severity"] == "high"
    assert validation.compare_block(1, "Attendance improved by 9% among 1,840 students.", "Attendance rose 9 per cent among 1840 students.") == []


def test_duplicate_sentences_and_stray_punctuation_are_found():
    from app.services.humanizer import refine

    dup = refine.grammar_findings("Entries expire after 300 seconds. Sessions are excluded.",
                                  "Cache entries expire after 300 seconds. Sessions are excluded. The TTL for cache entries is set to 300 seconds.")
    assert dup and dup[0].kind == "duplicate_sentence"
    fixed, notes = refine.auto_corrections("x", 'He said "it works.". Then left.')
    assert fixed == 'He said "it works." Then left.' and notes
    assert refine.guard("vocabulary", "Entries expire after 300 seconds.", "Entries expire after 300 seconds. The TTL is 300 seconds.",
                        n_findings_before=1, n_findings_after=0, high_flags_before=0, high_flags_after=0,
                        introduced_vocab_after=0) == "it added a sentence instead of changing words"


def test_key_terms_exclude_everyday_words():
    assert not pipeline._is_term("time") and not pipeline._is_term("data")
    assert all(pipeline._is_term(t) for t in ("TTL", "write-through", "PostgreSQL", "user_sessions", "consistent hashing", "p99"))


def test_untagged_single_block_reply_is_accepted():
    assert pipeline._parse_items("REVISION: The fixed text.", [4]) == {4: "The fixed text."}
    assert pipeline._parse_items("The fixed text.", [4, 5]) == {}


def test_signed_times_and_requests():
    from app.services.humanizer import refine, semantics

    assert validation.compare_block(1, "from 22:00 to 02:00 UTC", "from 22:00 to -02:00 UTC")[0]["type"] == "number_or_date"
    assert validation.compare_block(1, "between 10-12 people", "between 10-12 people") == []
    led = semantics.ledger("I would like to kindly request that your team does not deploy schema changes. Please do not hesitate to ask.")
    assert len(led["requests"]) == 1 and "request" in led["requests"][0]
    assert "keep as a request" in semantics.describe_ledger(2, led, None, None)
    # A style edit may not append a sentence unless it was asked to split one.
    assert "added a sentence" in refine.guard("style", "Don't deploy changes.", "Don't deploy changes. Could you please hold them?",
                                              n_findings_before=1, n_findings_after=0, high_flags_before=0, high_flags_after=0,
                                              introduced_vocab_after=0)
    assert refine.style_findings("Please hold the release.", "Hold the release.", mode="professional", intensity="medium",
                                 profile_stats=None)[0].kind == "courtesy"


def test_descriptive_modals_and_emphasis_are_not_hedges():
    assert validation.qualifier_changes("You learn how hot the pan should be.", "You learn when the pan is hot.") == []
    assert validation.qualifier_changes("But this is not true at all.", "That is not true.") == []


def test_repair_drops_restored_stock_openers_and_clause_duplicates_are_found():
    from app.services.humanizer import refine

    assert refine.strip_stock_openers("Moreover, interventions work. In conclusion, act now.") == "Interventions work. Act now."
    f = refine.grammar_findings("x", "The move is postponed because the vendor has not yet delivered the replacement servers. No data is affected. "
                                     "This postponement is due to the vendor's failure to deliver the replacement servers, and nothing else changes.")
    assert f and f[0].kind == "duplicate_sentence"
    assert refine._near_duplicates("Students who sleep less earn lower grades. Workload may confound this, and teachers agree.") == []


def test_targeted_edit_that_undoes_the_rewrite_is_detected():
    orig = "In order to improve speed, the team made the decision to utilize a cache for the reads of the database."
    rewrite = "To speed up database reads, the team added a cache."
    assert pipeline._reverts_improvement(orig, rewrite, orig.replace("utilize", "use"), "medium")
    assert not pipeline._reverts_improvement(orig, rewrite, rewrite.replace("added", "introduced"), "medium")
    assert not pipeline._reverts_improvement(orig, rewrite, orig, "light")


def test_unrepaired_overstatement_reverts_block(client):
    # The fake repair keeps FAKE_UNREPAIRABLE revisions unchanged; the fake rewrite drops "roughly" via the token below.
    text = ("FAKE_UNREPAIRABLE Self-reported sleep tends to overestimate actual sleep by roughly 30 to 45 minutes in most "
            "of the published studies we reviewed.")
    import tests.fake_ollama as fo
    old = list(fo.FAKE_STOCK)
    fo.FAKE_STOCK.append(("by roughly ", "by "))
    try:
        res = _run(client, text=text, ai_validation=False)[-1]["result"]
    finally:
        fo.FAKE_STOCK[:] = old
    assert res["output"] == text and res["validation"]["reverted"] == 1
