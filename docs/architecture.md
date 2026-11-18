# Architecture

How BroomBuster works, runs and is changed. Each diagram names the code that does the work.

## 1. Overview

```
Browser (PWA: frontend/)
  │  HTTPS
  ▼
Cloudflare Tunnel ──────────── the only way in
  │  CF-Connecting-IP
  ▼
uvicorn 127.0.0.1:8000        src/broombuster/api/app.py
  │
  ├─ /api/*   schedule, rules, closures, alerts, calendar feed, phone reports
  ├─ /auth/*  passkey sign-in                      api/auth.py
  └─ /        frontend/ (index.html, js/, sw.js, tiles/, layers/, vendor/)
        │
        ├─ data/<city>.fgb        loaded at startup         data_loader.py, state.py
        ├─ users.sqlite           accounts, members, passkeys, sessions  auth.py  (BB_DB;
        │                         host: /var/lib/broombuster, local: data/)
        └─ live feeds             511, weather, trash, tows  cached in memory;
                                  a failed source = "nothing known", never an error
```

## 2. Request paths

### Check a car

```
browser: car's lat/lon ──POST /api/check──▶ resolve.py     nearest street segment
                                                │
                                                ▼
                                          analysis.py      sweep code per side + holidays.py
                                                │
                                                ▼
browser: js/urgency.js colors it, calendar.js draws it ◀── JSON schedule
```

### Phone reports a parked car

```
Shortcuts / Tasker on car disconnect
  │  POST /api/cars/location   Authorization: Bearer <token>   {lat, lon, accuracy}
  ▼
auth.car_for_token ── unknown or revoked ──▶ 401
  │
accuracy > 50 m ───────────────────────────▶ 422 "Position too rough…"
  │
set_car_location ── car removed ───────────▶ 404
  │
resolve → address
  │
200 {"summary": "Parked at …"}     refusals: reason kept on the token (shown in the app), logged
```

### Sign-in

```
browser ──/auth/passkey/*──▶ auth.py (WebAuthn, via `webauthn`)
                                │
       users.sqlite: sessions stored hashed
browser ◀── cookie bb_session (httponly, 30 days)
```

Enabled when `BB_BASE_URL` is set. No email, no password: "Create account" makes an account
from a name and a passkey; "Sign in" finds the account from the passkey itself. Passkeys
sync between a person's devices through their platform (iCloud Keychain, Google Password
Manager). There is no account recovery: lose the passkey and the account is gone.

Accounts are shared: an account (its cars, homes, phone and calendar links) has members,
each a person with their own sign-in. Under Settings > Account, "Invite someone" makes a
single-use link (valid 7 days) that adds the opener's account to whoever opens it, through
a passkey. Up to 10 members per account. Leaving removes only yourself; the
account goes when its last member leaves.

## 3. Data

### Build (run on a development machine, then commit)

```
data/manifests/<city>.yaml ──┐
raw inputs (manifest source:)┴─▶ scripts/rebuild_city_data.py ──▶ <fgb_path>.fgb
                                                                   │
                                  scripts/build_pmtiles.py (tippecanoe)
                                                                   ▼
                                                    frontend/tiles/<region>.pmtiles

data/holidays/<city>.yaml ──────▶ holidays.py  (sweeping, daytime sweeping, trash suspended per day)
data/places/markets.yaml ───┐
layer_sources.py (ArcGIS,   ┴─▶ scripts/build_layers.py ──┬─▶ <city dir>/<theme>.fgb ──▶ /api/rules
Socrata, NREL EV API)           (--theme parking …)       └─▶ frontend/layers/<region>/<theme>.geojson ──▶ map
                                                                   │  ≥ 100 kB
                                  scripts/build_pmtiles.py ─▶ frontend/tiles/<region>/<theme>.<GeoJSON hash>.pmtiles ──▶ map
```

The map loads a layer's `.pmtiles` (only the tiles in view) while its name matches the GeoJSON's hash
(`layers.catalog`), else the GeoJSON. A source that fails keeps its previous files. Commit every `.fgb` and `.pmtiles` output.
Format of the sweep codes and columns: [data/README.md](../data/README.md).

## 4. Deploy

### Pipeline

```
git push to main
  │
  ▼
GitHub Actions ci.yml     ruff · pytest · node --check frontend/js/*.js
  │  passes
  ▼
Pi: broombuster-update.timer (every 2 min) ──▶ deploy/auto-update.sh
  │   newest first-parent commit on origin/main with a finished CI run
  │   (no run, e.g. [skip ci], or a cancelled one: its parent)
  │   CI passed · not in .deploy-bad · signature verified (opt-in)
  ▼
deploy/update.sh <sha>    git reset --hard · uv sync (system Python)
  │   └─ fails  ──▶ reset + sync previous sha, no restart, no mark (retried next run)
  ▼
systemctl restart broombuster
  ▼
checks: /health within 3 min · then the new sha's deploy/smoke.sh:
        POST /api/check finds a street in each region · / is the app page
  ├─ all pass     ──▶ rm .deploy-bad                          done
  └─ any failure  ──▶ redeploy previous sha · write .deploy-bad (timer skips it)
```

`/health` answers only after every city is loaded; a revision without `deploy/smoke.sh` is checked by `/health`
alone. `tests/test_deploy.py` runs the scripts against stubs and checks the smoke points. Logs:
`journalctl -u broombuster -u broombuster-update`.

### One-time host setup

1. `./deploy/install.sh`: writes the units from `deploy/`, creates `.env` (secrets, mode 600), and a sudoers rule
   that lets the update timer restart the service and nothing else. Unit changes need this again. It pauses
   the update timer while it runs and moves an existing `data/users.sqlite` to `/var/lib/broombuster/` (old
   copy: `data/users.sqlite.bak`); it stops if both exist, or if `.env` sets `BB_DB` outside that directory.
2. `gh auth login` with a fine-grained token: this repository only, Actions: read (and Contents: read for a
   private repository). The update timer only reads CI results.
3. Cloudflare Zero Trust > Networks > Tunnels: create `broombuster`, run its install command on the host,
   public hostname `broombuster.com` → `HTTP localhost:8000`.
4. Set `BB_BASE_URL=https://broombuster.com` in `.env` and restart: this turns sign-in on. It is also
   the passkey domain, so changing it orphans every passkey.
5. GitHub: branch protection on `main` requires a PR and passing CI, since every commit on `main` is deployed.
6. Cloudflare: SSL/TLS > Edge Certificates > enable HSTS.

### Signed commits (opt-in)

With `git config broombuster.verifyCommits true` in the host checkout, auto-update deploys only commits that
`git verify-commit` accepts; any other commit stops the deploy, and the running version stays.

| Commit signed by | Host setup |
|---|---|
| GitHub (`web-flow`): PRs merged on github.com | `curl -fsSL https://github.com/web-flow.gpg \| gpg --import` |
| An SSH key (pushed by hand) | `git config gpg.ssh.allowedSignersFile <file>` (format: `ssh-keygen(1)` ALLOWED SIGNERS) |

Check once by hand: `sudo systemctl start broombuster-update`, then `journalctl -u broombuster-update -n 20`.

## 5. Security boundary

```
Internet ──▶ Cloudflare ──▶ tunnel ──▶ 127.0.0.1:8000
                                       (no other route: port 8000 is never exposed)

deploy/broombuster.service (the app)
  ProtectHome=tmpfs    home is empty except:
    repo         read-only (data/ included)
    .claude      hidden
  /var/lib/broombuster  writable: users.sqlite (BB_DB)
  no capabilities · no new privileges · PrivateDevices · RestrictAddressFamilies
  SystemCallFilter=@system-service · MemoryMax=2G · TasksMax=256

deploy/broombuster-update.service (deploys, runs the deployed commit's scripts)
  ProtectSystem=strict · ProtectHome=read-only · PrivateTmp
  writable: repo (and its .venv) · ~/.cache/uv · ~/.config/gh · ~/.local/state/gh · ~/.gnupg
  no NoNewPrivileges: it restarts the app through sudo
```

The app cannot read the host user's `gh` token, SSH keys or other credentials. After changing a unit,
run `./deploy/install.sh` and check `sudo systemd-analyze security broombuster`.

Content-Security-Policy is enforced. `BB_CSP=report-only` in `.env` (and a restart) sends it as
`Content-Security-Policy-Report-Only` instead, for debugging (`CSP` in `api/web.py`).

## 6. Caching

```
/js/app.js?v=<content hash>  ──▶ public, 1 year, immutable   (hash computed at startup)
/, sw.js, unversioned files  ──▶ no-cache                    (ETag → cheap 304)
/api/*, /auth/*, /health     ──▶ no-store
```

Every deploy changes the versioned URLs, so nothing stale is served. Cloudflare follows these headers.
The service worker (`frontend/sw.js`) answers `?v=` files from its cache first, the page and other
files from the network first (its cache offline or on a 5xx); `/api/*` and `/tiles/*` bypass it.
Purge the Cloudflare cache once after changing this.

## 7. Rate limits

Per client: an IPv4 address or an IPv6 /64 (`LIMITS` in `api/web.py`). The IP is `CF-Connecting-IP`, validated. `BB_RATE_LIMIT=0` turns them off.

