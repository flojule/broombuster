# Raspberry Pi 5 deployment (Ubuntu 24.04)

Always-on, no-login, tailnet-only. The Mac stays usable independently via
`./run.sh` or `./deploy.sh`.

Map data (`.fgb` + `.pmtiles`) is committed to git, so a clone has everything.

| File | Runs on | Purpose |
|------|---------|---------|
| `install-service.sh` | Pi | Install + enable the systemd service for the current user |
| `broombuster.service` | Pi | systemd unit template (`__USER__`/`__REPO__` substituted on install) |
| `update.sh` | Pi | Roll out a new version: pull + reinstall + restart |

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
The `-e` (editable) is required: the app finds `data/` and `frontend/`
relative to the source tree, so a non-editable install resolves them inside
`.venv/lib/...` and the map data won't load.

**3. On the Pi — install the service + expose over HTTPS**
```bash
./deploy/install-service.sh
tailscale serve --bg 8000
tailscale serve status
```

URL: `https://<pi-name>.tailf5051f.ts.net` (the Pi's own MagicDNS name).

## Operations

| Action | Command (on the Pi) |
|--------|---------------------|
| Roll out a new version | `./deploy/update.sh` (pull + reinstall + restart + health) |
| Status / logs | `systemctl status broombuster` / `journalctl -u broombuster -f` |
| Restart | `sudo systemctl restart broombuster` |
| Stop / disable | `sudo systemctl disable --now broombuster` |
| Refresh map data | commit + push the rebuilt `.fgb`/tiles, then `./deploy/update.sh` |

The service starts at boot and restarts on crash; `tailscale serve` persists, so
nothing else needs re-running after a reboot.
