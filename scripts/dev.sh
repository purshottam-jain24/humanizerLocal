#!/usr/bin/env bash
# Start HumanWrite: backend (FastAPI, port 8000) + frontend (Next.js, port 3000).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf "\033[1m%s\033[0m\n" "$*"; }
fail() { printf "\n\033[31m%s\033[0m\n\n" "$*"; exit 1; }

# --- prerequisites ---------------------------------------------------------
PY=""
for c in python3.14 python3.13 python3.12 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,14) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
[ -n "$PY" ] || [ -x "$ROOT/backend/.venv/bin/python" ] || fail "Python 3.12–3.14 is required.
  macOS:   brew install python@3.13
  Other:   https://www.python.org/downloads/"
command -v node >/dev/null 2>&1 || fail "Node.js 20 or newer is required.
  macOS:   brew install node
  Other:   https://nodejs.org/"

# --- first-run setup -------------------------------------------------------
if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  say "First run: setting up the backend (about a minute)…"
  "$PY" -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q --upgrade pip
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  say "First run: setting up the interface (about a minute)…"
  (cd "$ROOT/frontend" && npm install --silent)
fi

if ! curl -s -m 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "Note: the AI assistant (Ollama) isn't running. Spelling, grammar and style checks still work."
  echo "      To turn on AI: install Ollama from https://ollama.com, then run: ollama pull qwen3:4b"
fi

for port in 8000 3000; do
  if lsof -ti ":$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. HumanWrite may already be running in another terminal:"
    echo "  open http://127.0.0.1:3000, or stop the other copy (Ctrl+C there) and run ./start.sh again."
    echo "  To free the port: kill \$(lsof -ti :$port)"
    exit 1
  fi
done

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning) &
(cd "$ROOT/frontend" && npm run dev --silent -- --hostname 127.0.0.1 --port 3000 >/dev/null) &

for _ in $(seq 1 60); do
  if curl -s -m 2 -o /dev/null http://127.0.0.1:3000/ && curl -s -m 2 -o /dev/null http://127.0.0.1:8000/api/health; then break; fi
  sleep 1
done
say ""
say "  HumanWrite is running →  http://127.0.0.1:3000"
say "  Press Ctrl+C to stop."
say ""
if [ "${HUMANWRITE_NO_BROWSER:-}" != "1" ]; then
  (command -v open >/dev/null && open http://127.0.0.1:3000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:3000) || true
fi
wait
