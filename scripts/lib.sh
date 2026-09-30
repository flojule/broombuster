# Shared helpers for run.sh, deploy.sh, funnel.sh. Source from the repo root.

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
