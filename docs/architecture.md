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
  ├─ /api/*   schedule, rules, closures, nearby, alerts, calendar feed, phone reports
  ├─ /auth/*  passkey sign-in                      api/auth.py
  └─ /        frontend/ (index.html, js/, sw.js, tiles/, layers/, vendor/)
        │
        ├─ data/<city>.fgb        loaded at startup         data_loader.py, state.py
        ├─ data/users.sqlite      accounts, members, passkeys, sessions  auth.py  (BB_DB)
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
browser: js/urgency.js colours it, calendar.js draws it ◀── JSON schedule
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

data/holidays/<city>.yaml ──────▶ holidays.py  (sweeping and trash suspended per day)
data/places/markets.yaml ───┐
layer_sources.py (ArcGIS,   ┴─▶ scripts/build_layers.py ──┬─▶ <city dir>/<theme>.fgb ──▶ /api/rules
Socrata, NREL EV API)           (--theme parking …)       └─▶ frontend/layers/<region>/<theme>.geojson ──▶ map
```

A source that fails keeps its previous files. Commit every `.fgb` and `.pmtiles` output.
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
  │   origin/main has a passing CI run, and is not in .deploy-bad
  ▼
deploy/update.sh <sha>    git reset --hard · uv sync · systemctl restart broombuster
  │
  ▼
poll /health for 3 min
  ├─ healthy      ──▶ rm .deploy-bad                          done
  └─ not healthy  ──▶ reset to previous sha · write .deploy-bad (timer skips it)
```

`/health` answers only after every city is loaded. Logs: `journalctl -u broombuster -u broombuster-update`.

### One-time host setup

1. `./deploy/install.sh`: writes the units from `deploy/`, creates `.env` (secrets, mode 600), and a sudoers rule
   that lets the update timer restart the service and nothing else. Unit changes need this again.
2. Cloudflare Zero Trust > Networks > Tunnels: create `broombuster`, run its install command on the host,
   public hostname `broombuster.com` → `HTTP localhost:8000`.
3. Set `BB_BASE_URL=https://broombuster.com` in `.env` and restart: this turns sign-in on. It is also
   the passkey domain, so changing it orphans every passkey.
4. GitHub: branch protection on `main` requires a PR and passing CI, since every commit on `main` is deployed.
5. Cloudflare: SSL/TLS > Edge Certificates > enable HSTS.

## 5. Security boundary

```
Internet ──▶ Cloudflare ──▶ tunnel ──▶ 127.0.0.1:8000
                                       (no other route: port 8000 is never exposed)

systemd unit (deploy/broombuster.service)
  ProtectHome=tmpfs    home is empty except:
    repo         read-only
    repo/data    writable
    .claude      hidden
  no capabilities · no new privileges · PrivateDevices · RestrictAddressFamilies
```

The app cannot read the host user's `gh` token, SSH keys or other credentials. After changing the unit,
run `./deploy/install.sh` and check `sudo systemd-analyze security broombuster`.

Content-Security-Policy is sent report-only. Set `BB_CSP=enforce` in `.env` and restart once the browser
console shows no `Content-Security-Policy-Report-Only` message (`CSP` in `api/web.py`).

## 6. Caching

```
/js/app.js?v=<content hash>  ──▶ public, 1 year, immutable   (hash computed at startup)
/, sw.js, unversioned files  ──▶ no-cache                    (ETag → cheap 304)
/api/*, /auth/*, /health     ──▶ no-store
```

Every deploy changes the versioned URLs, so nothing stale is served. Cloudflare follows these headers.
Purge the Cloudflare cache once after changing this.

## 7. Rate limits

Per client IP (`LIMITS` in `api/web.py`). The IP is `CF-Connecting-IP`, validated. `BB_RATE_LIMIT=0` turns them off.

