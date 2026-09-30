#!/usr/bin/env bash
# Poll origin and roll out ONLY when the tracked branch advanced. Safe to run on
# a timer: a no-op (no pull, no service restart) when there's nothing new, so it
# never bounces the app on an idle poll. Run by broombuster-update.service as the
# repo-owning user.
#   ./deploy/auto-update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

branch="$(git rev-parse --abbrev-ref HEAD)"

# Network blips shouldn't fail the unit every couple minutes — just retry next tick.
if ! git fetch --quiet origin "$branch"; then
  echo "git fetch failed; will retry next tick." >&2
  exit 0
fi

local_rev="$(git rev-parse HEAD)"
remote_rev="$(git rev-parse "origin/${branch}")"
if [ "$local_rev" = "$remote_rev" ]; then
  exit 0   # up to date — nothing to do
fi
if [ "$remote_rev" = "$(cat .git/broombuster-bad-rev 2>/dev/null || true)" ]; then
  exit 0   # this revision already failed to roll out; wait for a newer push
fi

echo "New commits on ${branch}: ${local_rev:0:9} -> ${remote_rev:0:9}; rolling out."
exec ./deploy/update.sh
