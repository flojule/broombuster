#!/usr/bin/env bash
# Install the BroomBuster service and its auto-update timer (run once on the host).
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --locked --extra api

for unit in broombuster.service broombuster-update.service broombuster-update.timer; do
  sed -e "s#__USER__#$(id -un)#g" -e "s#__REPO__#$PWD#g" "deploy/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done

# The update timer runs as this user and may restart the service, nothing else.
SUDOERS=/etc/sudoers.d/broombuster-update
printf '%s ALL=(root) NOPASSWD: %s restart broombuster\n' "$(id -un)" "$(command -v systemctl)" \
  | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -cf "$SUDOERS" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now broombuster broombuster-update.timer
sudo systemctl restart broombuster
