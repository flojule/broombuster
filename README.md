# BroomBuster

![GrimSweeper](frontend/grim_sweeper_rect.webp)

Know before the grim sweeper comes. An interactive map that shows your parked car and tells you whether street sweeping applies to that block — today, tomorrow, or not at all.

**Bay Area**

![Map screenshot — Bay Area](images/bay_area.webp)

**Chicago, IL**

![Map screenshot — Chicago](images/chicago.webp)

---

## Features

- **Multi-car tracking** — save multiple cars, each with its own name, color, and location.
- **GPS** — one tap to move a car to your phone's current GPS position.
- **Manual placement** — tap anywhere on the map to place a car.
- **Urgency color coding** — streets and car cards color-coded by sweeping urgency:
  - Red — sweeping today
  - Orange — sweeping tomorrow
  - Blue — no sweeping soon
- **Live status banner** — top bar shows which cars need to move.
- **Multi-city / multi-region** — Bay Area (Oakland, SF, Berkeley, Alameda) and Chicago.
- **Python CLI** — the original command-line tool still works independently.

---

## How to run

### Web app (local)

```bash
pip install -e '.[api]'
./run.sh
```

`./run.sh` starts the server (`DEV_MODE=true`, so no `JWT_SECRET` needed), opens
`http://localhost:8000`, and builds the map tiles on first run. Override with
`PORT=8080 ./run.sh` or `PYTHON=/path/to/python ./run.sh`.

The map renders from PMTiles vector tiles by default; if `tippecanoe` isn't
installed it falls back to the legacy GeoJSON renderer. Plain manual start:

```bash
DEV_MODE=true uvicorn broombuster.api.app:app --host 0.0.0.0 --port 8000
```

### Developer install (with tests, ruff, build scripts)

```bash
uv sync --locked --all-extras   # reproducible; or: pip install -e '.[api,scripts,dev]'
uv run pytest
```

Frontend map libraries (MapLibre GL, PMTiles) are vendored in `frontend/vendor/`; refresh them with `scripts/update_vendor.sh` (also bumps `CACHE` in `frontend/sw.js`).

The editable install puts `broombuster` on the import path, so `import broombuster.analysis`, `import broombuster.api.app`, etc. work from any working directory.

### Python CLI

```bash
pip install '.[scripts]'
python -m broombuster.cli.main
```

Opens a browser tab with the interactive map and prints the schedule to the console.

---

## Deployment

### Phone + laptop, no login (Tailscale)

```bash
sudo tailscale up      # once: connect this laptop to your tailnet
./deploy.sh
```

`./deploy.sh` runs the API on `127.0.0.1` in `DEV_MODE` (no account; phone and
laptop share one saved-car set) and fronts it with `tailscale serve` at
`https://<machine>.<tailnet>.ts.net`.

| Item | Detail |
|------|--------|
| Mobile setup | Install Tailscale app, log into same tailnet, open the URL, "Add to Home Screen". |
| Why HTTPS | One-tap GPS and PWA install require a secure (HTTPS) origin. |
| Access control | App binds localhost only; tailnet device auth is the gate. |
| Prereq | HTTPS + MagicDNS enabled in the [admin console](https://login.tailscale.com/admin/dns). |

### Public web address (Tailscale Funnel)

```bash
sudo tailscale up         # once: connect this machine to your tailnet
./funnel.sh               # public HTTPS at https://<machine>.<tailnet>.ts.net
```

`./funnel.sh` publishes the app to the **public internet** via `tailscale
funnel`, so anyone with the URL can use it — no Tailscale account required on
their end. Unlike `./deploy.sh` it runs with real auth (not `DEV_MODE`):

| Aspect | Behaviour |
|--------|-----------|
| Guests (no login) | Anyone can browse the map and add cars/houses. Guest data stays in that browser, per device (cleared when the tab closes). |
| Shared account | One seeded login you can hand out "if needed". Everyone signed into it shares one server-saved set of cars/homes. |
| Self-registration | **Disabled** (the default; only `ALLOW_REGISTRATION=true` enables it) so random visitors can't create accounts. |
| Secret | A random `JWT_SECRET` is generated once and saved to `.env` (gitignored). |

**Seed / reset the shared account** (run on the server, against the same DB):

```bash
SEED_EMAIL=share@broombuster SEED_PASSWORD='a-strong-passphrase' \
  python scripts/seed_account.py     # omit SEED_PASSWORD to auto-generate one
```

`funnel.sh` runs this for you on startup. Funnel requires HTTPS + MagicDNS and
the `funnel` node attribute in your tailnet ACL —
see [Tailscale Funnel docs](https://tailscale.com/kb/1223/funnel).

### Always-on Raspberry Pi 5 (Ubuntu 24.04)

Same no-login Tailscale-HTTPS model under `systemd`, surviving reboots. Setup,
`update.sh`, and `install-service.sh`: [`deploy/README.md`](deploy/README.md).

### API only (behind your own TLS)

If you front the app with your own reverse proxy / TLS, run the API directly:

```
JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))") \
  uvicorn broombuster.api.app:app --host 0.0.0.0 --port $PORT
```

---

## Continuous integration

One GitHub Actions workflow lives in [`.github/workflows/`](.github/workflows):

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `ci.yml` | every PR + push to `main` | `ruff check` + full `pytest` on Python 3.12 |

Deployment is pull-based: the Pi's auto-update timer rolls out a new `main`
commit only after `ci.yml` has passed on it (see
[`deploy/README.md`](deploy/README.md)). Runtime secrets stay on the host, never
in the repo: `JWT_SECRET` and the shared-account `SEED_PASSWORD`.

## Project layout

```
BroomBuster/
├── src/broombuster/              Single importable package
│   ├── __init__.py               Exposes __version__
│   ├── analysis.py               Sweep-day parsing; urgency; legacy CLI resolver
│   ├── car.py                    Car object used by the CLI
│   ├── cities.py                 City and region definitions (URLs, schemas, bboxes)
│   ├── config.py                 Credentials from environment variables
│   ├── data_loader.py            Loads and normalises city datasets to FGB
│   ├── email_alerts.py           Gmail-SMTP alert helper (CLI only)
│   ├── gps.py                    Nominatim helpers (server-side house-number lookup)
│   ├── maps.py                   GeoJSON builder for the MapLibre frontend
│   ├── normalize.py              Single source of truth for street/time normalization
│   ├── schemas.py                Per-city source-format normalisers (SCHEMA_PROFILES)
│   ├── resolve.py                Authoritative car → segment resolver (used by /check)
│   ├── api/                      HTTP server sub-package
│   │   ├── app.py                FastAPI app: routes, lifespan, static mount
│   │   ├── state.py              City/region GeoDataFrame caches, loading, hot-swap
│   │   ├── helpers.py            /check request helpers (region, address, clipping)
│   │   ├── auth.py               Local HS256 JWT issuance and verification
│   │   ├── db.py                 SQLite layer for user accounts & prefs
│   │   └── deps.py               JWT verify dependency (DEV_MODE bypass)
│   ├── cli/                      Command-line entry point
│   │   └── main.py               `python -m broombuster.cli.main` — interactive map + email alert
│   └── domains/                  City-data domain plugins (Step 3+)
│       ├── base.py               DomainPlugin protocol + DomainResult
│       ├── registry.py           Active plugin list; for_city() lookup
│       └── sweeping.py           Street-sweeping plugin + compose_message
├── frontend/
│   ├── index.html       PWA shell (markup only)
│   ├── styles.css       Extracted styles
│   ├── js/*.js          Application logic, split by feature (load order in index.html)
│   ├── manifest.json    PWA manifest
│   ├── sw.js            Service worker
│   └── icon-*.png/svg   App icons
├── data/
│   ├── README.md           Data directory documentation
│   ├── sources.yaml        Origin URLs + SHA256s for each city's raw input
│   └── <city>/StreetSweeping.fgb   Runtime artifact only (raw inputs not committed)
├── scripts/
│   ├── rebuild_city_data.py        Orchestrator — rebuilds .fgb from upstream
│   ├── build_berkeley_geojson.py   Per-city PDF→GeoJSON (called by orchestrator)
│   └── build_alameda_geojson.py    Per-city PDF→GeoJSON (called by orchestrator)
├── tests/
├── documentation/
│   ├── performance_plan.md        Shipped map-speed optimisations + remaining map-speed work
│   └── feature_plan.md            Remaining feature work (trash domain, frontend modules, manifests)
└── .env.example
```

## Refreshing city data

City `.fgb` files are what the server reads at runtime. To regenerate one
from its upstream source (e.g. after Chicago publishes a new annual dataset):

```bash
python scripts/rebuild_city_data.py <city_key>     # one city
python scripts/rebuild_city_data.py                # all cities
```

Commit the rebuilt `.fgb` (and tiles, `scripts/build_pmtiles.py`); deployments
ship data through git. `/health` reports each auto-download city's age (from its
last commit) and flags it `stale` past `stale_after_days`. See
[`data/README.md`](data/README.md) and [`data/sources.yaml`](data/sources.yaml)
for per-city source details and manual-download steps.
