#!/usr/bin/env bash
# Start the BroomBuster web app and open it in the browser.
#   ./run.sh            # default port 8000
#   PORT=8080 ./run.sh  # custom port
#   PYTHON=/path/python ./run.sh   # custom interpreter
set -euo pipefail
cd "$(dirname "$0")"

# flopi runs BroomBuster as a systemd service behind Tailscale Funnel. This
# script would fight it for the port. Stop the service first if you mean it.
if systemctl is-active --quiet broombuster 2>/dev/null; then
  echo "broombuster.service is running on this machine; not starting a second copy." >&2
  echo "Use it (journalctl -u broombuster -f) or stop it first: sudo systemctl stop broombuster" >&2
  exit 1
fi

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
