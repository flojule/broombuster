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

UNIT=/etc/systemd/system/broombuster.service
sed -e "s#__USER__#$USER#g" -e "s#__REPO__#$REPO#g" \
  deploy/broombuster.service | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now broombuster
echo "Installed and started. Status:"
systemctl --no-pager status broombuster | head -12
