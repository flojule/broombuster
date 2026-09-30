# Raspberry Pi 5 deployment (Ubuntu 24.04)

Always-on; guest-by-default with optional login (prefs persist
server-side only when signed in). Map data ships in git so a clone is
self-contained. The Mac runs independently via `./run.sh` / `./deploy.sh`.

`install-service.sh` writes a random `JWT_SECRET` into `.env` (gitignored) on
first run, which the unit reads via `EnvironmentFile`. The app refuses to start
in production with a secret shorter than 32 characters. The runtime DB
(`data/app.sqlite`) is gitignored and built on first boot.

| File | Runs on | Purpose |
|------|---------|---------|
| `install-service.sh` | Pi | Install + enable the systemd service for the current user |
| `broombuster.service` | Pi | systemd unit template (`__USER__`/`__REPO__` substituted on install) |
| `update.sh` | Pi | Roll out a new version: pull + locked dep sync + restart |
| `install-autoupdate.sh` | Pi | (Optional) install the poll-and-deploy timer |
| `auto-update.sh` | Pi | Poll origin; run `update.sh` when `main` advanced and CI passed on it |
| `broombuster-update.{service,timer}` | Pi | systemd timer that runs `auto-update.sh` every ~2 min |

## One-time setup

**1. On the Pi — system packages, Tailscale**
```bash
sudo apt update && sudo apt install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh   # per-user; installs to ~/.local/bin
sudo apt install -y gh && gh auth login          # auto-update reads CI status with gh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

**2. On the Pi — code + venv** (per-project `.venv`; never `--break-system-packages`)
```bash
git clone https://github.com/flojule/BroomBuster.git ~/ws/BroomBuster
cd ~/ws/BroomBuster
uv sync --locked --extra api   # creates .venv (Python 3.12), installs from uv.lock, editable
```
Editable install keeps path resolution on the source tree's `data/` + `frontend/`.
`update.sh` requires `uv` so the Pi runs exactly the versions in `uv.lock` (what
CI tested); it refuses to roll out without it.

**3. On the Pi — install the service + expose over HTTPS**
```bash
./deploy/install-service.sh   # waits for /health; restores the previous unit if the new one fails
tailscale serve --bg 8000
tailscale serve status
```

URL: `https://<pi-name>.tailf5051f.ts.net` (the Pi's own MagicDNS name).
Serve keeps it tailnet-only; Funnel (below) makes it **public on the internet**.
The production deployment currently uses Funnel: login and `/check*` routes are
per-IP rate limited (slowapi) and the interactive `/docs`, `/redoc` and
`/openapi.json` are disabled outside `DEV_MODE`.

For **off-VPN / public** access use Funnel instead of Serve (persists across
reboots; run once, not per update):
```bash
tailscale funnel --bg 8000
tailscale funnel status
```
Don't use `./funnel.sh` for always-on — it runs in the foreground and resets the
Funnel mapping on exit.

**4. (Optional) auto-deploy on push** — a timer that polls `origin` every ~2 min
and rolls out when `main` (override: `BROOMBUSTER_BRANCH`) advances **and**
`ci.yml` has passed on the new commit (idle polls don't restart the app; a
pending CI run is re-checked next tick):
```bash
./deploy/install-autoupdate.sh
```
It writes a minimal sudoers drop-in granting the timer's user passwordless
`systemctl restart broombuster` (the one privileged step in `update.sh`), then
enables `broombuster-update.timer`. With this on, you never run `update.sh` by
hand — just merge to `main`.

When a rollout is blocked and needs you — CI failed, the revision failed its
health gate and was rolled back, the checkout is on another branch, or `gh`
can't reach GitHub — the update unit exits non-zero, so it shows up in
`systemctl --failed` and `journalctl -u broombuster-update`. Each successful
rollout also warns there if an installed unit file no longer matches its
template in `deploy/`.

## Operations

| Action | Command (on the Pi) |
|--------|---------------------|
| Roll out a new version | `./deploy/update.sh` (reset to origin, discarding local edits to tracked files + locked dep sync + restart + health wait up to 180 s until every city has loaded (a failed city load fails the rollout); on failure restores the previous revision and exits 1) — not needed if the auto-update timer is on |
| Retry a failed revision | `rm .git/broombuster-bad-rev` (auto-update otherwise waits for a newer push) |
| Status / logs | `systemctl status broombuster` / `journalctl -u broombuster -f` |
| Restart | `sudo systemctl restart broombuster` |
| Stop / disable | `sudo systemctl disable --now broombuster` |
| Refresh map data | commit + push the rebuilt `.fgb`/tiles (auto-update rolls it out, or run `./deploy/update.sh`); `/health` flags data past `stale_after_days` |
| Auto-update logs | `journalctl -u broombuster-update -f`; next run: `systemctl list-timers broombuster-update.timer`; blocked: `systemctl --failed` |
| Disable auto-update | `sudo systemctl disable --now broombuster-update.timer` |

Unit-file changes are not applied by `update.sh` (the timer's sudo grant covers
only the restart); re-run `./deploy/install-service.sh` /
`./deploy/install-autoupdate.sh` after editing them. `update.sh` logs a warning
while they differ. Rollback resets code only, not
`app.sqlite`: schema migrations must stay additive (`ADD COLUMN` with a default,
see `api/db.py`) so the previous revision can still open a migrated DB.

Service auto-starts at boot and restarts on crash; `tailscale serve` persists across reboots.
