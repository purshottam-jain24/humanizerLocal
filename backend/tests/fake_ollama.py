"""A tiny HTTP server speaking the subset of the Ollama API HumanWrite uses.

TEST-ONLY. It lets the test-suite exercise the real streaming, cancellation
and JSON-review code paths without downloading model weights. It is never
used by the application.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    slow = False

    def log_message(self, *a):  # silence
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json({"version": "0.0.0-test"})
        if self.path == "/api/tags":
            return self._json({"models": [
                {"name": "qwen3:4b", "size": 2_500_000_000, "details": {"family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M"}},
                {"name": "embeddinggemma:latest", "size": 600_000_000, "details": {"family": "gemma-embedding", "parameter_size": "300M"}},
            ]})
        if self.path == "/api/ps":
            return self._json({"models": []})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/embed":
            vecs = []
            for text in req["input"]:
                h = hashlib.sha256(text.lower().encode()).digest()
                vecs.append([b / 255 for b in h[:32]])
            return self._json({"embeddings": vecs})
        if self.path == "/api/chat":
            msgs = req["messages"]
            user = msgs[-1]["content"]
            humanizer = _humanizer_reply(msgs)
            if humanizer is not None:
                chunks = [humanizer[i:i + 9] for i in range(0, len(humanizer), 9)]
            elif req.get("format"):
                para = user.split("Paragraph to review:\n", 1)[-1].split("\n\nReturn at most", 1)[0]
                issues = []
                m = re.search(r"\b(\w+) \1\b", para)
                if m:
                    issues.append({"passage": m.group(), "category": "grammar", "explanation": "Repeated word.",
                                   "correction": m.group(1), "priority": "high"})
                issues.append({"passage": "THIS TEXT DOES NOT EXIST", "category": "clarity", "explanation": "hallucinated",
                               "correction": "x", "priority": "low"})
                content = json.dumps({"issues": issues})
                chunks = [content]
            else:
                orig = re.search(r"<original>(.*)</original>", user, re.S).group(1)
                revised = re.sub(r"\b(\w+) \1\b", r"\1", orig).replace("In order to", "To").replace("recieve", "receive")
                n_alt = re.search(r"Write (\d) genuinely different alternatives", msgs[0]["content"])
                if n_alt:
                    alts = [revised, "Put simply: " + revised[:1].lower() + revised[1:], revised.rstrip(".") + ", in short."]
                    out = "".join(f'<revision n="{i + 1}">{a}</revision>' for i, a in enumerate(alts[:int(n_alt.group(1))]))
                    out += "<explanation>Three different takes.</explanation>"
                else:
                    out = f"<revision>{revised}</revision><explanation>Removed a repeated word and simplified wording.</explanation>"
                chunks = [out[i:i + 7] for i in range(0, len(out), 7)]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                for c in chunks:
                    self.wfile.write((json.dumps({"message": {"role": "assistant", "content": c}, "done": False}) + "\n").encode())
                    self.wfile.flush()
                    if Handler.slow:
                        time.sleep(0.05)
                self.wfile.write((json.dumps({"message": {"role": "assistant", "content": ""}, "done": True,
                                              "eval_count": len(chunks), "eval_duration": 1_000_000}) + "\n").encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if self.path == "/api/generate":
            return self._json({"done": True})
        self._json({"error": "not found"}, 404)


FAKE_STOCK = [("Furthermore, ", ""), ("Moreover, ", "Also, "), ("Additionally, ", ""), ("It is important to note that ", ""),
              ("in today's fast-paced world, ", ""), ("In today's fast-paced world, ", ""), ("plays a crucial role in", "matters for"),
              ("in order to", "to"), ("due to the fact that", "because")]


def _humanizer_reply(msgs: list[dict]) -> str | None:
    """Deterministic stand-ins for the humanizer's prompts (rewrite, flow, context, validation, style)."""
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    user = msgs[-1]["content"]
    if "Each REVISION below" in system:  # repair: copy the original back unless told the problem is unrepairable
        out = []
        for m in re.finditer(r"\[Block (\d+)\]\nORIGINAL: (.*?)\nREVISION: (.*?)\nProblems to fix:", user, re.S):
            fixed = m.group(3) if "FAKE_UNREPAIRABLE" in m.group(2) else m.group(2).replace("Furthermore, ", "")
            out.append(f'<b id="{m.group(1)}">{fixed}</b>')
        return "\n".join(out)
    if "careful line editor doing one focused pass" in system:  # targeted refinement
        out = []
        for m in re.finditer(r"<b id=\"(\d+)\">(.*?)</b>", user, re.S):
            text = m.group(2)
            if "FAKE_REFINE_BREAK" in text:
                text = re.sub(r"\d+", "", text)  # a careless edit that drops numbers: must be rejected by the guard
            for a, b in (("utilize", "use"), ("leverage", "use"), ("crucial", "important"), ("seamlessly", "smoothly")):
                text = text.replace(a, b)
            out.append(f'<b id="{m.group(1)}">{text}</b>')
        return "\n".join(out)
    if "help an editor understand a document" in system:  # semantic map
        blocks = []
        for m in re.finditer(r"\[(P\d+)\]\n(.*?)(?=\n\n\[P\d+\]|\n\nReturn JSON)", user, re.S):
            words = re.findall(r"[A-Za-z]+", m.group(2))
            blocks.append({"block": m.group(1), "points": [" ".join(words[len(words) // 2:len(words) // 2 + 7]),
                                                                "zebras migrate across invented savannah plains"],
                           "role": "continues the argument"})
        return json.dumps({"blocks": blocks})
    if "Blocks to revise:" in user or "smoothing the flow" in system:
        body = user.split("Blocks to revise:", 1)[-1]
        out = []
        for m in re.finditer(r"<b id=\"(\d+)\">(.*?)</b>", body, re.S):
            text = m.group(2)
            if "FAKE_DROP_TOKEN" in text:
                text = re.sub(r"⟦[A-Z]\d+⟧", "", text)  # simulate a model that loses a protected token
            for a, b in FAKE_STOCK:
                text = text.replace(a, b)
            if text[:1].islower():
                text = text[0].upper() + text[1:]
            if "FAKE_SKIP_BLOCK" in text:
                continue
            if "FAKE_NUMBER_DRIFT" in text:
                text = text.replace("22:00", "00:00")  # a rewrite that corrupts a time: validation must catch and repair it
            if "FAKE_INTRODUCE_VOCAB" in text:
                text = text.replace("uses", "utilize").replace("helps", "seamlessly helps")
            out.append(f'<b id="{m.group(1)}">{text}</b>')
        # Mimic a reasoning model leaking its thoughts into the answer.
        return "<think>Looking at the blocks.</think>\n" + "\n".join(out)
    if "analyse documents for an editor" in system:
        return json.dumps({"document_type": "essay", "audience": "general readers", "purpose": "Explain a topic.", "tone": "formal",
                           "main_points": ["Point one"], "key_terms": ["urban green spaces", "NOT IN TEXT TERM"],
                           "claims_to_preserve": [], "context_notes": []})
    if "fact-checking editor" in system:
        issues = []
        if "FAKE_AI_FLAG" in user:
            bid = int(re.search(r"\[Block (\d+)\]", user).group(1))
            issues.append({"block": bid, "type": "changed_claim", "severity": "high", "original": "", "revised": "",
                           "explanation": "The revised text changes the claim."})
        return "Here you go:\n```json\n" + json.dumps({"issues": issues}) + "\n```"
    if "describe an author's writing style" in system:
        return json.dumps({"description": "Direct and plain, with short paragraphs.", "tone": "friendly",
                           "traits": ["Opens with the main point", "Uses short sentences"], "avoid": ["jargon"]})
    return None


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
