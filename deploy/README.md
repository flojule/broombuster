# Raspberry Pi 5 deployment (Ubuntu 24.04)

Always-on, tailnet-only; guest-by-default with optional login (prefs persist
server-side only when signed in). Map data ships in git so a clone is
self-contained. The Mac runs independently via `./run.sh` / `./deploy.sh`.

`install-service.sh` writes a random `JWT_SECRET` into `.env` (gitignored) on
first run, which the unit reads via `EnvironmentFile`. The runtime DB
(`data/app.sqlite`) is gitignored and built on first boot.

| File | Runs on | Purpose |
|------|---------|---------|
| `install-service.sh` | Pi | Install + enable the systemd service for the current user |
| `broombuster.service` | Pi | systemd unit template (`__USER__`/`__REPO__` substituted on install) |
| `update.sh` | Pi | Roll out a new version: pull + reinstall + restart |
| `install-autoupdate.sh` | Pi | (Optional) install the poll-and-deploy timer |
| `auto-update.sh` | Pi | Poll origin; run `update.sh` only when the branch advanced |
| `broombuster-update.{service,timer}` | Pi | systemd timer that runs `auto-update.sh` every ~2 min |

## One-time setup

**1. On the Pi — system packages, Tailscale**
```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

**2. On the Pi — code + venv** (per-project `.venv`; never `--break-system-packages`)
```bash
git clone https://github.com/flojule/BroomBuster.git ~/ws/BroomBuster
cd ~/ws/BroomBuster
python3 -m venv .venv
.venv/bin/pip install -e '.[api]'
```
`-e` (editable) keeps path resolution on the source tree's `data/` + `frontend/`.

**3. On the Pi — install the service + expose over HTTPS**
```bash
./deploy/install-service.sh
tailscale serve --bg 8000
tailscale serve status
```

URL: `https://<pi-name>.tailf5051f.ts.net` (the Pi's own MagicDNS name).

For **off-VPN / public** access use Funnel instead of Serve (persists across
reboots; run once, not per update):
```bash
tailscale funnel --bg 8000
tailscale funnel status
```
Don't use `./funnel.sh` for always-on — it runs in the foreground and resets the
Funnel mapping on exit.

**4. (Optional) auto-deploy on push** — a timer that polls `origin` every ~2 min
and rolls out only when the tracked branch advances (idle polls don't restart
the app):
```bash
./deploy/install-autoupdate.sh
```
It writes a minimal sudoers drop-in granting the timer's user passwordless
`systemctl restart broombuster` (the one privileged step in `update.sh`), then
enables `broombuster-update.timer`. With this on, you never run `update.sh` by
hand — just push to the branch the Pi tracks.

## Operations

| Action | Command (on the Pi) |
|--------|---------------------|
| Roll out a new version | `./deploy/update.sh` (reset to origin, discarding local edits to tracked files + reinstall + restart + health) — not needed if the auto-update timer is on |
| Status / logs | `systemctl status broombuster` / `journalctl -u broombuster -f` |
| Restart | `sudo systemctl restart broombuster` |
| Stop / disable | `sudo systemctl disable --now broombuster` |
| Refresh map data | commit + push the rebuilt `.fgb`/tiles (auto-update rolls it out, or run `./deploy/update.sh`) |
| Auto-update logs | `journalctl -u broombuster-update -f`; next run: `systemctl list-timers broombuster-update.timer` |
| Disable auto-update | `sudo systemctl disable --now broombuster-update.timer` |

Service auto-starts at boot and restarts on crash; `tailscale serve` persists across reboots.
