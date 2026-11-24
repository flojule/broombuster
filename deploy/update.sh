#!/usr/bin/env bash
# Deploy a revision (default: origin's tip of the current branch): reset, sync, restart, check.
set -euo pipefail
{  # parsed whole before running: the reset below rewrites this file
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # uv
export UV_PYTHON_PREFERENCE=only-system   # the service cannot see uv-managed Pythons (ProtectHome)

branch="$(git rev-parse --abbrev-ref HEAD)"
prev="$(git rev-parse HEAD)"

# Wait up to 3 min for /health (it answers once every city is loaded).
up() {
  for _ in $(seq 90); do
    curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "/health does not answer" >&2
  return 1
}

# Code and venv of revision $1.
install_rev() {
  git reset --hard "$1" && uv sync --locked --extra api --compile-bytecode --quiet
}

# Returns 1: not restarted; 2: /health or the revision's own deploy/smoke.sh failed (set -e is off).
deploy() {
  install_rev "$1" && sudo systemctl restart broombuster || return 1
  up && { [ ! -f deploy/smoke.sh ] || bash deploy/smoke.sh; } || return 2
}

git fetch --quiet origin "$branch"
target="$(git rev-parse "${1:-origin/$branch}")"
rc=0
deploy "$target" || rc=$?
if [ "$rc" = 0 ]; then
  rm -f .deploy-bad
  echo "Deployed $(git rev-parse --short HEAD)"
  exit 0
fi
if [ "$rc" = 1 ]; then
  # Not the revision's fault as far as we know: restore the code, retry on the next run.
  echo "${target:0:9} did not install; back to ${prev:0:9}, retried next run" >&2
  install_rev "$prev" || echo "Restoring ${prev:0:9} failed too: check git status, uv sync" >&2
  exit 1
fi
# Unhealthy: go back to the last good revision and tell auto-update to skip this one.
echo "${target:0:9} failed its checks; rolling back to ${prev:0:9}" >&2
echo "$target" > .deploy-bad
deploy "$prev" || echo "Rollback failed too: journalctl -u broombuster" >&2
exit 1
}
