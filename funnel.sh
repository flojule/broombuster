#!/usr/bin/env bash
# Serve BroomBuster on the PUBLIC internet via Tailscale Funnel.
#
#   ./funnel.sh             # public HTTPS at https://<machine>.<tailnet>.ts.net
#   PORT=8080 ./funnel.sh   # proxy a different local port
#   PYTHON=/path ./funnel.sh
#
# Unlike ./deploy.sh (tailnet-only, no login), this exposes the app to anyone
# with the URL, so it runs with REAL auth, not DEV_MODE:
#   * Guest by default — anyone can browse the map and add cars/homes with no
#     login (guest data stays in their own browser, per device).
#   * One shared account (seeded below) you can hand out "if needed"; everyone
#     who signs into it shares one server-saved set of cars/homes.
#   * Self-registration is DISABLED (ALLOW_REGISTRATION=false) so random
#     visitors can't create accounts.
#
# Funnel prerequisites (one-time, in the Tailscale admin console):
#   * HTTPS + MagicDNS enabled:        https://login.tailscale.com/admin/dns
#   * Funnel allowed in the ACL policy: add "funnel" node attributes for this
#     machine — https://tailscale.com/kb/1223/funnel
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

. scripts/lib.sh
pick_python

# --- Secrets: a real JWT_SECRET, persisted in .env (gitignored) -------------
# Generated once and reused across restarts so existing sessions survive a
# reboot. We export it ourselves rather than relying on dotenv so auth.py sees
# it at import time.
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')"
  echo "JWT_SECRET=${JWT_SECRET}" >> .env
  echo "Generated a new JWT_SECRET and saved it to .env"
fi
export JWT_SECRET
export ALLOW_REGISTRATION=false   # public: shared account + guests only
unset DEV_MODE 2>/dev/null || true

# --- Tailscale must be running and logged in --------------------------------
if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not connected. Start it and log in first:" >&2
  echo "    sudo tailscale up" >&2
  echo "Then re-run ./funnel.sh" >&2
  exit 1
fi

# --- Seed / ensure the shared account ---------------------------------------
# Set SEED_PASSWORD to choose the password; otherwise one is generated and
# printed once. Re-runs reset the password to the supplied/generated value.
echo "Ensuring the shared account exists…"
"$PY" scripts/seed_account.py || {
  echo "Could not seed the shared account — continuing; you can run" >&2
  echo "    $PY scripts/seed_account.py" >&2
  echo "manually later." >&2
}

ensure_tiles

# --- Expose the local port publicly over HTTPS (background, persists) -------
if ! tailscale funnel --bg "$PORT" >funnel_err.log 2>&1; then
  echo "tailscale funnel failed:" >&2
  cat funnel_err.log >&2
  echo >&2
  echo "Funnel needs HTTPS + MagicDNS enabled and the 'funnel' node attribute" >&2
  echo "in your tailnet ACL. See https://tailscale.com/kb/1223/funnel" >&2
  rm -f funnel_err.log
  exit 1
fi
rm -f funnel_err.log

# --- Compute the public URL from the node's MagicDNS name -------------------
HOST=$("$PY" -c "import json,subprocess;print(json.loads(subprocess.check_output(['tailscale','status','--json']))['Self']['DNSName'].rstrip('.'))")
URL="https://${HOST}"

cleanup() {
  echo
  echo "Removing Tailscale funnel mapping…"
  tailscale funnel reset >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo
echo "BroomBuster is PUBLIC on the internet at:"
echo "    ${URL}"
echo
echo "Anyone with the link can use it as a guest (no login). Share the seeded"
echo "account credentials only if you want someone to share your saved cars."
echo "Ctrl-C to stop and remove the public mapping."
echo

# Bind localhost only; Tailscale Funnel terminates TLS and proxies to it.
exec "$PY" -m uvicorn broombuster.api.app:app --host 127.0.0.1 --port "$PORT"
