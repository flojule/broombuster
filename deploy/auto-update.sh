#!/usr/bin/env bash
# Poll origin and roll out ONLY when the tracked branch advanced AND CI passed
# on the new commit. Safe to run on a timer: a no-op (no pull, no service
# restart) when there's nothing new, so it never bounces the app on an idle
# poll. Run by broombuster-update.service as the repo-owning user.
#   ./deploy/auto-update.sh
#   BROOMBUSTER_BRANCH=other ./deploy/auto-update.sh   # track another branch
#
# Exits non-zero (the unit shows "failed" in `systemctl --failed`) whenever a
# rollout is blocked and needs a person: checkout on the wrong branch, CI
# failed, the revision already failed to roll out, or gh can't reach GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."

branch="${BROOMBUSTER_BRANCH:-main}"

current="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current" != "$branch" ]; then
  echo "Checkout is on '${current}', not '${branch}'; not updating." \
       "Run: git checkout ${branch}" >&2
  exit 1
fi

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
  echo "${remote_rev:0:9} failed to roll out earlier; waiting for a newer push" \
       "(or rm .git/broombuster-bad-rev to retry)." >&2
  exit 1
fi

# Deploy only what CI has passed. Pending (or not started yet): check next tick.
if ! ci="$(gh run list --commit "$remote_rev" --workflow ci.yml \
             --json status,conclusion \
             --jq '.[0] | if . then "\(.status) \(.conclusion)" else "" end')"; then
  echo "Could not read CI status for ${remote_rev:0:9} (gh auth/network?)." >&2
  exit 1
fi
case "$ci" in
  "completed success") ;;
  completed*)
    echo "CI did not pass for ${remote_rev:0:9} (${ci#completed }); not rolling out." >&2
    exit 1 ;;
  *)
    echo "Waiting for CI on ${remote_rev:0:9} (${ci:-no run yet})."
    exit 0 ;;
esac

echo "New commits on ${branch}: ${local_rev:0:9} -> ${remote_rev:0:9}; CI passed; rolling out."
# Pin the checked revision so a push landing meanwhile is not deployed unchecked.
exec ./deploy/update.sh "$remote_rev"
