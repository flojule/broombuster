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
    uv sync --locked --inexact --extra api --quiet   # reproducible; keeps other extras
  else
    .venv/bin/pip install -e '.[api]' --quiet
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

git fetch --quiet origin "$branch"
git reset --hard "origin/${branch}"
new="$(git rev-parse HEAD)"

if sync_deps && sudo systemctl restart broombuster && healthy; then
  rm -f .git/broombuster-bad-rev
  echo
  echo "Rolled out ${new:0:9}"
  exit 0
fi

echo "Rollout of ${new:0:9} failed; restoring ${old:0:9}" >&2
echo "$new" > .git/broombuster-bad-rev
git reset --hard "$old"
if sync_deps && sudo systemctl restart broombuster && healthy >/dev/null; then
  echo "Restored ${old:0:9} and it is healthy." >&2
else
  echo "ROLLBACK ALSO FAILED: service may be down; check journalctl -u broombuster" >&2
fi
exit 1
