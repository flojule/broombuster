#!/usr/bin/env bash
# Run by broombuster-update.timer: deploy the newest commit on origin/main that passed CI.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch --quiet origin main || exit 0   # offline: try next tick
tip="$(git rev-parse origin/main)"
bad="$(cat .deploy-bad 2>/dev/null || true)"
revs="$(git rev-list --first-parent --max-count=20 HEAD..origin/main)"
[ -n "$revs" ] || exit 0

# Opt-in (git config broombuster.verifyCommits true): deploy only commits git verifies.
trusted() {
  [ "$(git config --bool broombuster.verifyCommits || true)" = true ] || return 0
  git verify-commit "$1" >/dev/null 2>&1
}

# Newest first; a commit without a finished CI run ([skip ci], cancelled) yields to its parent.
for rev in $revs; do
  [ "$rev" = "$bad" ] && exit 0   # failed its deploy; wait for a newer commit
  ci="$(gh run list --commit "$rev" --workflow ci.yml --json status,conclusion \
          --jq '.[0] | "\(.status) \(.conclusion)"')"
  case "$ci" in
    "null null" | "completed cancelled") continue ;;   # cancelled: superseded by a newer push
    "completed success") ;;
    completed*) echo "CI failed for ${rev:0:9}; not deploying." >&2; exit 1 ;;
    *) exit 0 ;;   # CI still running
  esac
  [ "$rev" = "$tip" ] || echo "No CI run for ${tip:0:9}; deploying ${rev:0:9}, the newest commit that passed CI."
  trusted "$rev" || { echo "${rev:0:9} has no trusted signature; not deploying." >&2; exit 1; }
  exec ./deploy/update.sh "$rev"
done
echo "No CI run for ${tip:0:9} or the commits before it; waiting for a commit CI checks."
