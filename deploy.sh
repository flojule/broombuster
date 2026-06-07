#!/usr/bin/env bash
# Serve BroomBuster for phone + laptop over Tailscale HTTPS, with no login.
#
#   ./deploy.sh             # serve on tailnet at https://<machine>.<tailnet>.ts.net
#   PORT=8080 ./deploy.sh   # proxy a different local port
#   PYTHON=/path ./deploy.sh
#
# DEV_MODE=true => no account / no sign-in. The phone and the laptop share the
# same saved cars (one local user). The app listens only on 127.0.0.1; the
# tailnet is the only way in, so Tailscale device auth is the access control.
# Real HTTPS from Tailscale is what makes one-tap GPS and PWA install work on
# the phone (browsers block geolocation on insecure origins).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
export DEV_MODE=true   # skip auth on both API and frontend

# Interpreter: explicit $PYTHON, else per-project venv (Linux/Pi), else this
# Mac's global venv, else python3.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "$HOME/pyenv/bin/python" ]; then
  PY="$HOME/pyenv/bin/python"
else
  PY="python3"
fi

# 1. Tailscale must be running and logged in.
if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not connected. Start it and log in first:" >&2
  echo "    sudo tailscale up" >&2
  echo "Then re-run ./deploy.sh" >&2
  exit 1
fi

# 2. Build map tiles once if missing (same as run.sh); fall back to GeoJSON.
if ! ls frontend/tiles/*.pmtiles >/dev/null 2>&1; then
  if command -v tippecanoe >/dev/null 2>&1; then
    echo "Building map tiles (one-time)..."
    "$PY" scripts/build_pmtiles.py
  else
    echo "tippecanoe not found -- running in legacy GeoJSON mode (slower)."
    export PMTILES_MODE=0
  fi
fi

# 3. Expose the local port over HTTPS on the tailnet (background, persists).
if ! tailscale serve --bg "$PORT" >serve_err.log 2>&1; then
  echo "tailscale serve failed:" >&2
  cat serve_err.log >&2
  echo "If serve/HTTPS is not enabled, click the link above (or enable" >&2
  echo "HTTPS + MagicDNS at https://login.tailscale.com/admin/dns), then re-run." >&2
  rm -f serve_err.log
  exit 1
fi
rm -f serve_err.log

# 4. Compute the tailnet URL from the node's MagicDNS name.
HOST=$("$PY" -c "import json,subprocess;print(json.loads(subprocess.check_output(['tailscale','status','--json']))['Self']['DNSName'].rstrip('.'))")
URL="https://${HOST}"

# Remove the serve mapping when the server stops, so the tailnet name stops
# pointing at a dead port.
cleanup() {
  echo
  echo "Removing Tailscale serve mapping..."
  tailscale serve reset >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "BroomBuster (no-login) live on your tailnet:"
echo "    ${URL}"
echo
echo "On your phone: install the Tailscale app, log in to the same tailnet,"
echo "open ${URL}, then 'Add to Home Screen' to install it as an app."
echo "Ctrl-C to stop."
echo

exec "$PY" -m uvicorn broombuster.api.app:app --host 127.0.0.1 --port "$PORT"
