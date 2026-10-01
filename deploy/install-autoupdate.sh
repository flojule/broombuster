#!/usr/bin/env bash
# Install the auto-update timer: polls origin and rolls out when the tracked
# branch advances. Run on the Pi AFTER install-service.sh.
#   ./deploy/install-autoupdate.sh
# To remove: sudo systemctl disable --now broombuster-update.timer
#            sudo rm /etc/systemd/system/broombuster-update.{service,timer}
#            sudo rm /etc/sudoers.d/broombuster-update && sudo systemctl daemon-reload
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib.sh   # render_unit

# update.sh restarts the app via `sudo systemctl restart broombuster`; the timer
# runs as $USER, so grant exactly that one command passwordless (nothing else)
# so it can restart non-interactively. Use the resolved absolute path so the
# rule matches what sudo executes.
SYSTEMCTL="$(command -v systemctl)"
SUDOERS=/etc/sudoers.d/broombuster-update
printf '%s ALL=(root) NOPASSWD: %s restart broombuster\n' "$USER" "$SYSTEMCTL" \
  | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
if ! sudo visudo -cf "$SUDOERS" >/dev/null; then
  echo "sudoers validation failed; removing $SUDOERS" >&2
  sudo rm -f "$SUDOERS"
  exit 1
fi

for unit in broombuster-update.service broombuster-update.timer; do
  render_unit "$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now broombuster-update.timer
echo "Auto-update enabled. Timer:"
systemctl --no-pager list-timers broombuster-update.timer | head -3
