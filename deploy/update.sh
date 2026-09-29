#!/usr/bin/env bash
# Roll out the latest BroomBuster on the Pi: pull, sync deps, restart, check.
#   ./deploy/update.sh
# Everything (code, frontend, .fgb data, tiles) ships via git; editable install
# runs the source tree, so a pull + restart is the whole rollout. The repo wins:
# tracked files are reset to origin, discarding runtime edits (e.g. the app's
# self-refreshed .fgb). Untracked/ignored files (.env, app.sqlite) are kept.
set -euo pipefail
cd "$(dirname "$0")/.."

branch="$(git rev-parse --abbrev-ref HEAD)"
git fetch --quiet origin "$branch"
git reset --hard "origin/${branch}"
.venv/bin/pip install -e '.[api]' --quiet   # picks up dependency changes; cheap if none
sudo systemctl restart broombuster

sleep 2
echo "Health:"
curl -fsS http://127.0.0.1:8000/health && echo
