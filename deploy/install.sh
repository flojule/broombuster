#!/usr/bin/env bash
# Install the BroomBuster service and its auto-update timer (run once on the host, and after a unit change).
set -euo pipefail
cd "$(dirname "$0")/.."
trap '[ $? = 0 ] || echo "install.sh failed: auto-deploy stays paused until it completes" >&2' EXIT

# No deploy while installing: stop the timer, let a running update finish.
sudo systemctl stop broombuster-update.timer 2>/dev/null || true
while systemctl is-active --quiet broombuster-update.service; do sleep 1; done

UV_PYTHON_PREFERENCE=only-system uv sync --locked --extra api
mkdir -p "$HOME/.cache/uv"   # writable for the update service (missing paths are skipped)

# Sign-in settings (secrets): created once, edited by hand.
[ -f .env ] || install -m 600 .env.example .env

# The service can write only its state directory, so a BB_DB in .env must point there.
STATE=/var/lib/broombuster
if grep -q '^BB_DB=' .env; then
  db="$(sed -n 's/^BB_DB=//p' .env | tail -n 1)"
  db="${db%\"}" db="${db#\"}"
  case "$db" in
    *..*) echo ".env: BB_DB=$db must not contain '..'" >&2; exit 1 ;;
    "$STATE"/?*) ;;
    *) echo ".env: BB_DB=$db must be under $STATE/ (or remove the line)" >&2; exit 1 ;;
  esac
else
  db="$STATE/users.sqlite"
fi

# Move the account database out of data/ (read-only to the service) before the new unit runs.
if [ -e data/users.sqlite ] && sudo test -e "$db"; then
  echo "Both data/users.sqlite and $db exist: keep one (move the other away), then rerun." >&2
  exit 1
fi
if [ -e data/users.sqlite ]; then
  sudo systemctl stop broombuster 2>/dev/null || true
  if [ -e data/users.sqlite-journal ] || [ -e data/users.sqlite-wal ]; then
    echo "data/users.sqlite-journal or -wal exists: start and stop broombuster once, then rerun." >&2
    exit 1
  fi
  sudo install -d -o "$(id -un)" -m 700 "$(dirname "$db")"
  sudo install -o "$(id -un)" -m 600 data/users.sqlite "$db"
  mv data/users.sqlite data/users.sqlite.bak
  echo "Moved data/users.sqlite to $db (old copy: data/users.sqlite.bak)"
fi

for unit in broombuster.service broombuster-update.service broombuster-update.timer; do
  sed -e "s#__USER__#$(id -un)#g" -e "s#__REPO__#$PWD#g" -e "s#__HOME__#$HOME#g" "deploy/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done

# The update timer runs as this user and may restart the service, nothing else.
SUDOERS=/etc/sudoers.d/broombuster-update
printf '%s ALL=(root) NOPASSWD: %s restart broombuster\n' "$(id -un)" "$(command -v systemctl)" \
  | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -cf "$SUDOERS" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now broombuster
sudo systemctl restart broombuster
sudo systemctl enable --now broombuster-update.timer
