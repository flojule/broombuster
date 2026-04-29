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
pip install '.[api]'
DEV_MODE=true uvicorn broombuster.api.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a browser. `DEV_MODE=true` skips JWT verification so you can use the app without setting `JWT_SECRET`.

### Developer install (with tests, ruff, build scripts)

```bash
pip install -e '.[api,scripts,dev]'
pytest
```

The editable install puts `broombuster` on the import path, so `import broombuster.analysis`, `import broombuster.api.app`, etc. work from any working directory.

### Python CLI

```bash
pip install '.[scripts]'
python -m broombuster.cli.main
```

Opens a browser tab with the interactive map and prints the schedule to the console.

---

## Deployment

The app is containerised. For HTTPS termination + auto-cert, use Caddy via
`docker-compose.yml`:

```bash
JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))") \
  docker compose up -d
```

See [`Caddyfile.example`](Caddyfile.example) for public-domain and Tailscale
variants. Or run the API alone (no TLS):

```
uvicorn api.api:app --host 0.0.0.0 --port $PORT
```

---

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
│   ├── resolve.py                Authoritative car → segment resolver (used by /check)
│   ├── api/                      HTTP server sub-package
│   │   ├── app.py                FastAPI app: routes, startup loading, static mount
│   │   ├── auth.py               Local HS256 JWT issuance and verification
│   │   ├── db.py                 SQLite layer for user accounts & prefs
│   │   └── deps.py               JWT verify dependency (DEV_MODE bypass)
│   ├── cli/                      Command-line entry point
│   │   └── main.py               `python -m broombuster.cli.main` — interactive map + email alert
│   └── domains/                  City-data domain plugins (Step 3+)
│       └── sweeping.py           Street-sweeping output formatter (compose_message)
├── frontend/
│   ├── index.html       Single-page PWA
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
│   ├── architecture.md            How the app works and its dependencies
│   ├── performance_plan.md        Current bottlenecks and free optimisation options
│   ├── user_database_options.md   Options for persisting user data
│   └── web_hosting_options.md     Options for hosting the backend
├── Dockerfile
└── .env.example
```

## Refreshing city data

City `.fgb` files are what the server reads at runtime. To regenerate one
from its upstream source (e.g. after Chicago publishes a new annual dataset):

```bash
python scripts/rebuild_city_data.py <city_key>     # one city
python scripts/rebuild_city_data.py                # all cities
```

See [`data/README.md`](data/README.md) and [`data/sources.yaml`](data/sources.yaml)
for per-city source details and manual-download steps.
