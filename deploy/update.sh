#!/usr/bin/env bash
# Deploy a revision (default: origin's tip of the current branch): reset, sync, restart.
set -euo pipefail
{  # parsed whole before running: the reset below rewrites this file
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # uv

branch="$(git rev-parse --abbrev-ref HEAD)"
prev="$(git rev-parse HEAD)"

# Reset to a revision, sync, restart; succeeds once the app answers (it does so
# only after every city is loaded).
deploy() {
  git reset --hard "$1"
  uv sync --locked --inexact --extra api --quiet
  sudo systemctl restart broombuster
  for _ in $(seq 90); do
    curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

git fetch --quiet origin "$branch"
target="$(git rev-parse "${1:-origin/$branch}")"
if deploy "$target"; then
  rm -f .deploy-bad
  echo "Deployed $(git rev-parse --short HEAD)"
  exit 0
fi
# Unhealthy: go back to the last good revision and tell auto-update to skip this one.
echo "${target:0:9} not healthy after 3 min; rolling back to ${prev:0:9}" >&2
echo "$target" > .deploy-bad
deploy "$prev" || echo "Rollback is unhealthy too: journalctl -u broombuster" >&2
exit 1
}
