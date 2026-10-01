#!/usr/bin/env bash
# Run by broombuster-update.timer: deploy origin/main once CI has passed on it.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch --quiet origin main || exit 0   # offline: try next tick
rev="$(git rev-parse origin/main)"
[ "$rev" = "$(git rev-parse HEAD)" ] && exit 0

ci="$(gh run list --commit "$rev" --workflow ci.yml --json status,conclusion \
        --jq '.[0] | "\(.status) \(.conclusion)"')"
case "$ci" in
  "completed success") exec ./deploy/update.sh "$rev" ;;
  completed*) echo "CI failed for ${rev:0:9}; not deploying." >&2; exit 1 ;;
  *) exit 0 ;;   # CI still running
esac
