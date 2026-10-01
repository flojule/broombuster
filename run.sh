#!/usr/bin/env bash
# Start the BroomBuster web app locally (DEV_MODE: no login) and open it.
#   ./run.sh                       # port 8000, ./.venv's python
#   PORT=8080 PYTHON=/path/python ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

# flopi runs BroomBuster as a systemd service behind Tailscale Funnel; a second
# copy would fight it for the port. Stop the service first if you mean it.
if systemctl is-active --quiet broombuster 2>/dev/null; then
  echo "broombuster.service is running on this machine; not starting a second copy." >&2
  echo "Use it (journalctl -u broombuster -f) or stop it first: sudo systemctl stop broombuster" >&2
  exit 1
fi

PY="${PYTHON:-.venv/bin/python}"
PORT="${PORT:-8000}"
export DEV_MODE=true   # skip JWT so no JWT_SECRET is needed locally

URL="http://localhost:${PORT}"
echo "BroomBuster running at ${URL}  (Ctrl-C to stop)"
if command -v open >/dev/null 2>&1; then ( sleep 1.5; open "$URL" ) & fi
exec "$PY" -m uvicorn broombuster.api.app:app --host 0.0.0.0 --port "$PORT"
