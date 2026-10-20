#!/usr/bin/env bash
# Dev helper for Git Bash (Windows), macOS and Linux.
# Usage (from repo root):
#   ./scripts/dev.sh setup   # create .venv and install requirements
#   ./scripts/dev.sh run     # start the API on http://localhost:8000 (mock LLM by default)
#   ./scripts/dev.sh test    # run pytest -q
set -euo pipefail
cd "$(dirname "$0")/.."
CMD="${1:-run}"
PORT="${PORT:-8000}"

if [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"; else PY=".venv/bin/python"; fi

ensure_venv() {
  if [ ! -x "$PY" ]; then
    if command -v py >/dev/null 2>&1; then
      for v in 3.11 3.12 3.13 3.10; do
        if py -"$v" -c "import sys" >/dev/null 2>&1; then py -"$v" -m venv .venv; break; fi
      done
    else
      python3 -m venv .venv || python -m venv .venv
    fi
    if [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"; else PY=".venv/bin/python"; fi
  fi
  [ -x "$PY" ] || { echo "Could not create .venv - install Python 3.11+"; exit 1; }
}

case "$CMD" in
  setup) ensure_venv; "$PY" -m pip install --upgrade pip; "$PY" -m pip install -r requirements.txt ;;
  run)   ensure_venv; "$PY" -m uvicorn backend.api.main:app --host 127.0.0.1 --port "$PORT" --reload --reload-dir backend ;;
  test)  ensure_venv; "$PY" -m pytest -q ;;
  *) echo "Unknown command '$CMD'. Use: setup | run | test"; exit 1 ;;
esac
