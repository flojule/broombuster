#!/usr/bin/env bash
# Roll out the latest BroomBuster on the Pi: pull, sync deps, restart, check.
#   ./deploy/update.sh
# Everything (code, frontend, .fgb data, tiles) ships via git; editable install
# runs the source tree, so a pull + restart is the whole rollout.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
.venv/bin/pip install -e '.[api]' --quiet   # picks up dependency changes; cheap if none
sudo systemctl restart broombuster

sleep 2
echo "Health:"
curl -fsS http://127.0.0.1:8000/health && echo
