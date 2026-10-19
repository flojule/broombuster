# Dependencies

What BroomBuster is built on. Python versions: `uv.lock`. Browser libraries: `frontend/vendor/` (pinned by file).

## 1. Whole system

```
   Browser (PWA, frontend/)                        Server (Python 3.12, src/broombuster/)
   ┌───────────────────────────┐   HTTPS          ┌──────────────────────────────────────┐
   │ vanilla JS, no build step │ ───────────────▶ │ FastAPI · Starlette · Pydantic       │
   │ MapLibre GL 6.14.0        │   /api, /auth    │ Uvicorn                              │
   │ PMTiles 3.0.0             │ ◀─────────────── │ geopandas · shapely · pyproj         │
   └────────────┬──────────────┘                  │ pandas · numpy · PyYAML · requests   │
                │                                 │ geopy                                │
                │ tiles, style                    │ SQLite (stdlib) · data/*.fgb         │
                ▼                                 └──┬───────────┬───────────┬───────────┘
        OpenFreeMap                                  │           │           │
        tiles.openfreemap.org                        ▼           ▼           ▼
                                             city open data   Google    Nominatim
                                             (see §4)         sign-in   (OpenStreetMap)
```

Ingress and hosting (Cloudflare Tunnel, Raspberry Pi, systemd) are in [architecture.md](architecture.md).

## 2. Python packages

```
pyproject.toml
  │
  ├─ core      geopandas 1.2 · shapely 2.2 · pyproj 3.8 · pandas 3.0 · numpy 2.5
  │            PyYAML 6.0 · requests 2.34 · geopy 2.5
  │
  ├─ api       fastapi 0.143  ──▶ starlette 1.8, pydantic 2.14
  │            uvicorn 0.54 (standard)
  │
  ├─ scripts   pdfplumber 0.11
  │
  ├─ dev       pytest 9.1 · ruff 0.16 · httpx2 2.13
  │
  └─ NOT DECLARED, imported anyway
               cairosvg     scripts/build_icons.py
               playwright   scripts/visual_scenarios.py (run via uv tool run --from playwright)
```

| Package | Used for | Where |
|---|---|---|
| geopandas, shapely, pyproj | Street geometry, nearest segment, CRS (EPSG:3857) | `data_loader.py`, `resolve.py`, `state.py` |
| pandas, numpy | Schedule tables, holidays | `analysis.py`, `holidays.py`, `feeds.py` |
| PyYAML | Manifests, holidays, places | `cities.py`, `layer_sources.py` |
| requests | Live feeds, layer builds | `feeds.py`, `around.py`, `alerts.py`, `trash.py`, `layer_sources.py`, `auth.py` |
| geopy (Nominatim) | Address ↔ coordinates, 1 request/s | `gps.py` |
| FastAPI, Starlette, Pydantic | HTTP API, validation, phone-report errors | `api/` |
| Uvicorn | ASGI server | `run.sh`, `broombuster.service` |
| SQLite (stdlib) | Accounts, hashed sessions, cars, phone tokens | `api/auth.py` |
| pdfplumber | Reading published PDFs | `scripts/pdf_city.py` |
| cairosvg | PNG icons from `frontend/icon.svg` | `scripts/build_icons.py` |

## 3. Browser

```
frontend/index.html
  ├─ /vendor/maplibre-gl.mjs    MapLibre GL 6.14.0   ESM, exposed as window.maplibregl
  ├─ /vendor/maplibre-gl.css
  ├─ /vendor/pmtiles.js         PMTiles 3.0.0        .pmtiles protocol
  ├─ /js/*.js                   classic scripts, shared globals, no framework
  ├─ /sw.js                     service worker, template filled by the server
  └─ styles.css, icons, manifest.json

Network allowed by CSP: 'self' and tiles.openfreemap.org. No CDN, font service or analytics.
```

Vendored libraries are BSD-3-Clause ([frontend/vendor/README.md](../frontend/vendor/README.md)).

## 4. External services

```
Server ──▶ city open data, on demand, results cached in memory
  │
  ├─ Socrata        data.cityofchicago.org (tows, layers) · data.sf.gov (closures, layers)
  ├─ ArcGIS         services.arcgis.com · services1.arcgis.com · gis.cityofberkeley.info
  ├─ Legistar       oakland / sfgov / alameda / webapi.legistar.com        council meetings
  ├─ Recollect      api.recollect.net                                     Oakland trash
  ├─ weather.gov    api.weather.gov · alerts.weather.gov                  Red Flag, snow
  ├─ BAAQMD         www.baaqmd.gov                                        Spare the Air
  ├─ 511.org        api.511.org  (BB_511_API_KEY)                         Bay Area work zones
  ├─ Nominatim      OpenStreetMap                                         address search, reverse
  ├─ ipapi.co       approximate position from IP (iplocate.py)
  └─ Google OIDC    accounts.google.com · oauth2.googleapis.com           sign-in

Failure: a source that fails answers "nothing known" (failures cached 5 min), never an error.
```

Build-time only, never reached by the server: NLR/NREL EV API (`developer.nlr.gov`, `BB_NREL_API_KEY`),
Overpass (`scripts/pdf_city.py`), and the portals above via `scripts/rebuild_city_data.py` and `scripts/build_layers.py`.

## 5. Build tools

```
data/manifests/*.yaml ──▶ scripts/rebuild_city_data.py ──▶ <fgb_path>.fgb        geopandas
<fgb_path>.fgb        ──▶ scripts/build_pmtiles.py     ──▶ frontend/tiles/*.pmtiles
                             └─ tippecanoe (system binary)
data/manifests + places ─▶ scripts/build_layers.py     ──▶ *.fgb + frontend/layers/*.geojson   requests, geopandas
frontend/icon.svg       ─▶ scripts/build_icons.py      ──▶ frontend/*.png                     cairosvg
```

Outputs are committed, so the server and deploy need none of these tools.

## 6. Host

```
Raspberry Pi OS
  ├─ broombuster.service        uvicorn, hardened: ProtectHome, ProtectSystem=strict
  ├─ broombuster-update.timer   every 2 min ─▶ deploy/auto-update.sh
  │     ├─ git                  fetch, reset --hard
  │     ├─ uv                   uv sync --locked --extra api   (expected in ~/.local/bin)
  │     ├─ gh                   CI status of the commit
  │     └─ sudo                 sudoers rule: systemctl restart broombuster, nothing else
  └─ cloudflared                Cloudflare Tunnel ─▶ 127.0.0.1:8000
```

Set up by `deploy/install.sh`.

## 7. Development and CI

```
GitHub Actions
  ├─ ci.yml       actions/checkout · astral-sh/setup-uv · node (preinstalled on runner)
  │               ruff · pytest · node --check frontend/js/*.js
  ├─ visual.yml   Playwright WebKit + Chromium · axe-core 4.10.2 (cdn.jsdelivr.net, scripts only)
  │               screenshots uploaded as run artifacts
  └─ Dependabot   weekly for Python, monthly for Actions
```

Local: `uv sync --locked --all-extras`, then `uv run pytest -q`. Frontend tests need `node`.

