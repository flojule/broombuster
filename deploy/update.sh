#!/usr/bin/env bash
# Roll out the latest BroomBuster on flopi: pull, sync deps, restart, health-check.
#   ./deploy/update.sh          # the tip of origin/<current branch>
#   ./deploy/update.sh <rev>    # a specific revision (auto-update.sh passes the CI-checked one)
# Everything (code, frontend, .fgb data, tiles) ships via git; editable install
# runs the source tree. The repo wins: tracked files are reset to origin,
# discarding runtime edits. Untracked/
# ignored files (.env, app.sqlite) are kept.
# On failure (deps, restart, or health) the previous revision is restored and
# the bad revision is recorded in .git/broombuster-bad-rev so auto-update.sh
# does not retry it.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"   # uv; systemd units do not have this on PATH
. scripts/lib.sh                        # render_unit, healthy

branch="$(git rev-parse --abbrev-ref HEAD)"
old="$(git rev-parse HEAD)"

# uv is required: installing from uv.lock is what makes the Pi run the exact
# versions CI tested. Checked before touching the checkout.
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH; install it (curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2
  exit 1
fi

sync_deps() {
  uv sync --locked --inexact --extra api --quiet   # reproducible; keeps other extras
}

# Unit files are installed with sudo by install-*.sh, not by this script (the
# timer's sudo grant covers only the restart). Warn when the repo's templates
# have moved ahead of what is installed so the drift is visible in the logs.
check_units() {
  local unit
  for unit in broombuster.service broombuster-update.service broombuster-update.timer; do
    [ -f "/etc/systemd/system/$unit" ] || continue
    if ! render_unit "$unit" | cmp -s - "/etc/systemd/system/$unit"; then
      echo "WARNING: /etc/systemd/system/$unit differs from deploy/$unit;" \
           "re-run ./deploy/install-service.sh / ./deploy/install-autoupdate.sh" >&2
    fi
  done
}

git fetch --quiet origin "$branch"
git reset --hard "${1:-origin/${branch}}"
new="$(git rev-parse HEAD)"

if sync_deps && sudo systemctl restart broombuster && healthy; then
  rm -f .git/broombuster-bad-rev
  echo
  echo "Rolled out ${new:0:9}"
  check_units
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
