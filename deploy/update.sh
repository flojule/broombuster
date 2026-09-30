#!/usr/bin/env bash
# Roll out the latest BroomBuster on flopi: pull, sync deps, restart, health-check.
#   ./deploy/update.sh
# Everything (code, frontend, .fgb data, tiles) ships via git; editable install
# runs the source tree. The repo wins: tracked files are reset to origin,
# discarding runtime edits (e.g. the app's self-refreshed .fgb). Untracked/
# ignored files (.env, app.sqlite) are kept.
# On failure (deps, restart, or health) the previous revision is restored and
# the bad revision is recorded in .git/broombuster-bad-rev so auto-update.sh
# does not retry it.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # uv; systemd units do not have this on PATH

branch="$(git rev-parse --abbrev-ref HEAD)"
old="$(git rev-parse HEAD)"

sync_deps() {
  if command -v uv >/dev/null 2>&1; then
    uv sync --locked --extra api --quiet   # exact, reproducible from uv.lock
  else
    .venv/bin/pip install -e '.[api]' --quiet
  fi
}

# Health: retry while the app imports geopandas and binds the port (slow on a Pi).
healthy() {
  curl -fsS --max-time 5 --retry 30 --retry-delay 2 --retry-connrefused \
    http://127.0.0.1:8000/health
}

git fetch --quiet origin "$branch"
git reset --hard "origin/${branch}"
new="$(git rev-parse HEAD)"

if sync_deps && sudo systemctl restart broombuster && healthy; then
  echo
  echo "Rolled out ${new:0:9}"
  exit 0
fi

echo "Rollout of ${new:0:9} failed; restoring ${old:0:9}" >&2
echo "$new" > .git/broombuster-bad-rev
git reset --hard "$old"
sync_deps
sudo systemctl restart broombuster
exit 1
