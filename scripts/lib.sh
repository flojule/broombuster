# Shared helpers for run.sh, deploy.sh, funnel.sh and deploy/*.sh. Source from
# the repo root.

# Sets PY: explicit $PYTHON, else per-project .venv, else the Mac's global
# venv, else python3.
pick_python() {
  if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
  elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
  elif [ -x "$HOME/pyenv/bin/python" ]; then
    PY="$HOME/pyenv/bin/python"
  else
    PY="python3"
  fi
}

# Builds PMTiles once if missing; without tippecanoe, falls back to the legacy
# GeoJSON renderer (PMTILES_MODE=0). Needs $PY.
ensure_tiles() {
  ls frontend/tiles/*.pmtiles >/dev/null 2>&1 && return 0
  if command -v tippecanoe >/dev/null 2>&1; then
    echo "Building map tiles (one-time)..."
    "$PY" scripts/build_pmtiles.py
  else
    echo "tippecanoe not found -- running in legacy GeoJSON mode (slower)."
    export PMTILES_MODE=0
  fi
}

# Health: the app must bind the port AND finish loading every city (slow on a
# Pi). A failed city load fails the rollout immediately; /health alone returns
# "ok" while cities are still loading, so it isn't enough to prove the data is good.
healthy() {
  local body deadline=$((SECONDS + 180))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if body="$(curl -fsS --max-time 5 http://127.0.0.1:8000/health 2>/dev/null)"; then
      case "$body" in
        *'"failed":[]'*) ;;
        *) echo "city load failed: $body" >&2; return 1 ;;
      esac
      case "$body" in
        *'"loading":[]'*) echo "$body"; return 0 ;;
      esac
    fi
    sleep 2
  done
  echo "timed out waiting for cities to load" >&2
  return 1
}
