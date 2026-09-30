#!/usr/bin/env bash
# Start the BroomBuster web app and open it in the browser.
#   ./run.sh            # default port 8000
#   PORT=8080 ./run.sh  # custom port
#   PYTHON=/path/python ./run.sh   # custom interpreter
set -euo pipefail
cd "$(dirname "$0")"

. scripts/lib.sh
pick_python

PORT="${PORT:-8000}"
export DEV_MODE=true   # skip JWT so no JWT_SECRET is needed locally

ensure_tiles

URL="http://localhost:${PORT}"
echo "BroomBuster running at ${URL}  (Ctrl-C to stop)"
# Open the browser shortly after the server comes up.
if command -v open >/dev/null 2>&1; then ( sleep 1.5; open "$URL" ) & fi

exec "$PY" -m uvicorn broombuster.api.app:app --host 0.0.0.0 --port "$PORT"
