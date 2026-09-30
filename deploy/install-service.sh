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
  echo "    uv sync --locked --extra api" >&2
  exit 1
fi

# Generate a production JWT secret on first install. The unit reads this via
# EnvironmentFile; .env is gitignored so the secret never enters git. An existing
# .env is left untouched so issued tokens survive reinstalls.
ENV_FILE="$REPO/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Writing $ENV_FILE with a fresh JWT_SECRET..."
  SECRET="$("$REPO/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"
  printf 'JWT_SECRET=%s\n' "$SECRET" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

. scripts/lib.sh   # healthy

# Keep the current unit so a new one that fails to start (e.g. a hardening
# option this host rejects) can be rolled back without taking the site down.
UNIT=/etc/systemd/system/broombuster.service
[ -f "$UNIT" ] && sudo cp "$UNIT" "$UNIT.bak"
sed -e "s#__USER__#$USER#g" -e "s#__REPO__#$REPO#g" \
  deploy/broombuster.service | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable broombuster
sudo systemctl restart broombuster
if ! healthy >/dev/null; then
  echo "broombuster did not come up healthy with the new unit:" >&2
  journalctl -u broombuster -n 20 --no-pager >&2 || true
  if [ -f "$UNIT.bak" ]; then
    sudo mv "$UNIT.bak" "$UNIT"
    sudo systemctl daemon-reload
    sudo systemctl restart broombuster
    echo "Restored the previous unit." >&2
  fi
  exit 1
fi
sudo rm -f "$UNIT.bak"
echo "Installed and started. Status:"
systemctl --no-pager status broombuster | head -12
