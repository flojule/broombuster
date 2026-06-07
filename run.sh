#!/usr/bin/env bash
# Start the BroomBuster web app and open it in the browser.
#   ./run.sh            # default port 8000
#   PORT=8080 ./run.sh  # custom port
#   PYTHON=/path/python ./run.sh   # custom interpreter
set -euo pipefail
cd "$(dirname "$0")"

# Interpreter: prefer an explicit $PYTHON, else this Mac's global venv, else python3.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "$HOME/pyenv/bin/python" ]; then
  PY="$HOME/pyenv/bin/python"
else
  PY="python3"
fi

PORT="${PORT:-8000}"
export DEV_MODE=true   # skip JWT so no JWT_SECRET is needed locally

# PMTILES vector tiles are the default renderer. Build them once if missing;
# if tippecanoe isn't installed, fall back to the legacy GeoJSON renderer so
# the page still loads.
if ! ls frontend/tiles/*.pmtiles >/dev/null 2>&1; then
  if command -v tippecanoe >/dev/null 2>&1; then
    echo "Building map tiles (one-time)…"
    "$PY" scripts/build_pmtiles.py
  else
    echo "tippecanoe not found — running in legacy GeoJSON mode (slower)."
    export PMTILES_MODE=0
  fi
fi

URL="http://localhost:${PORT}"
echo "BroomBuster running at ${URL}  (Ctrl-C to stop)"
# Open the browser shortly after the server comes up.
if command -v open >/dev/null 2>&1; then ( sleep 1.5; open "$URL" ) & fi

exec "$PY" -m uvicorn broombuster.api.app:app --host 0.0.0.0 --port "$PORT"
