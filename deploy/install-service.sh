#!/usr/bin/env bash
# Install + enable the BroomBuster systemd service. Run on the Pi (Ubuntu).
#   ./deploy/install-service.sh
# Substitutes the current user + repo path into the unit template so no manual
# editing is needed. Requires sudo for the unit file.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"

if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "No .venv found at $REPO/.venv -- create it first:" >&2
  echo "    python3 -m venv .venv && .venv/bin/pip install -e '.[api]'" >&2
  exit 1
fi

UNIT=/etc/systemd/system/broombuster.service
sed -e "s#__USER__#$USER#g" -e "s#__REPO__#$REPO#g" \
  deploy/broombuster.service | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now broombuster
echo "Installed and started. Status:"
systemctl --no-pager status broombuster | head -12
