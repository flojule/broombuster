#!/usr/bin/env bash
# Deploy a revision (default: origin's tip of the current branch): reset, sync, restart.
set -euo pipefail
{  # parsed whole before running: the reset below rewrites this file
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # uv

branch="$(git rev-parse --abbrev-ref HEAD)"
git fetch --quiet origin "$branch"
git reset --hard "${1:-origin/$branch}"
uv sync --locked --inexact --extra api --quiet
sudo systemctl restart broombuster

# The app answers only once every city is loaded.
for _ in $(seq 90); do
  curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 \
    && { echo "Deployed $(git rev-parse --short HEAD)"; exit 0; }
  sleep 2
done
echo "Not healthy after 3 min: journalctl -u broombuster" >&2
exit 1
}
