#!/usr/bin/env bash
# Run BroomBuster locally on http://localhost:${PORT:-8000}.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec .venv/bin/python -m uvicorn broombuster.api.app:app --port "${PORT:-8000}"
