# Dependencies

What BroomBuster is built on. Versions: Python packages in `uv.lock`; browser libraries in
[frontend/vendor/README.md](../frontend/vendor/README.md) and the file headers.

## 1. Whole system

```
   Browser (PWA, frontend/)                        Server (Python 3.12, src/broombuster/)
   ┌───────────────────────────┐   HTTPS          ┌──────────────────────────────────────┐
   │ vanilla JS, no build step │ ───────────────▶ │ FastAPI · Starlette · Pydantic       │
   │ MapLibre GL               │   /api, /auth    │ Uvicorn · webauthn                   │
   │ PMTiles                   │ ◀─────────────── │ geopandas · shapely · pyproj         │
   └────────────┬──────────────┘                  │ pandas · numpy · PyYAML · requests   │
                │                                 │ geopy                                │
                │ tiles, style                    │ SQLite (stdlib) · data/*.fgb         │
                ▼                                 └──┬───────────┬──────────────────────┘
        OpenFreeMap                                  │           │
        tiles.openfreemap.org                        ▼           ▼
                                             live feeds       Nominatim
                                             (see §4)         (OpenStreetMap)
```

Ingress and hosting (Cloudflare Tunnel, Raspberry Pi, systemd) are in [architecture.md](architecture.md).

## 2. Python packages

```
pyproject.toml   (versions: uv.lock)
  │
  ├─ core      geopandas · shapely · pyproj · pandas · numpy · PyYAML · requests · geopy
  ├─ api       fastapi (starlette, pydantic) · uvicorn[standard] · webauthn
  ├─ scripts   cairosvg (needs the system cairo library) · pdfplumber
  ├─ visual    playwright (WebKit and Chromium: uv run playwright install)
  └─ dev       pytest · ruff · httpx2
```

| Package | Used for | Where |
|---|---|---|
| geopandas, shapely, pyproj | Street geometry, nearest segment, CRS (EPSG:3857) | `data_loader.py`, `resolve.py`, `state.py`, `analysis.py`, `tiles.py` |
| pandas | City tables, Chicago permit zones, layers | `data_loader.py`, `schemas.py`, `feeds.py`, `layers.py` |
| numpy | Segment geometry arrays | `analysis.py`, `resolve.py`, `schemas.py` |
| PyYAML | Manifests, holidays, corrections | `cities.py`, `holidays.py`, `data_loader.py` |
| requests | Live feeds; layer and city builds | `feeds.py`, `trash.py`; `layer_sources.py`, `scripts/` |
| geopy (Nominatim) | Address ↔ coordinates, 1 request/s | `gps.py` |
| FastAPI, Starlette, Pydantic | HTTP API, validation, phone-report errors | `api/` |
| Uvicorn | ASGI server | `run.sh`, `broombuster.service` |
| webauthn (py_webauthn) | Passkey sign-in and sign-up: challenge and signature checks | `api/auth.py` |
| SQLite (stdlib) | Accounts, members, passkeys, hashed sessions, cars, phone tokens | `api/auth.py` |
| pdfplumber | Reading published PDFs | `scripts/pdf_city.py` |
| cairosvg | PNG icons from `frontend/icon.svg` | `scripts/build_icons.py` |
| playwright | Layout screenshots and checks | `scripts/visual_scenarios.py` |

## 3. Browser

```
frontend/index.html
  ├─ /vendor/maplibre-gl.mjs    MapLibre GL   ESM, exposed as window.maplibregl
  ├─ /vendor/maplibre-gl.css
  ├─ /vendor/pmtiles.js         PMTiles       .pmtiles protocol
  ├─ /js/*.js                   classic scripts, shared globals, no framework
  ├─ /sw.js                     service worker, template filled by the server
  └─ styles.css, icons, manifest.json

Network allowed by CSP: 'self' and tiles.openfreemap.org. No CDN, font service or analytics.
```

Vendored libraries are BSD-3-Clause ([frontend/vendor/README.md](../frontend/vendor/README.md)).

## 4. External services

```
Server ──▶ live feeds, on demand, results cached in memory
  │
  ├─ Socrata        data.sf.gov (closures) · data.cityofchicago.org (tows, relocations)
  ├─ ArcGIS         services.arcgis.com                                   Oakland closures
  ├─ Recollect      api.recollect.net                                     Oakland trash
  ├─ weather.gov    api.weather.gov · alerts.weather.gov                  Red Flag, snow
  ├─ 511.org        api.511.org  (BB_511_API_KEY)                         Bay Area work zones
  ├─ Nominatim      OpenStreetMap                                         address search, reverse
  └─ ipapi.co       approximate position from IP (iplocate.py)

Failure: a source that fails answers "nothing known" (failures cached 5 min), never an error.
```

Build time only, never reached by the server:

| Host | Used by |
|---|---|
| Manifest `source:` URLs (DataSF, Chicago Data Portal, city ArcGIS layers, berkeleyca.gov PDFs) | `scripts/rebuild_city_data.py` |
| `developer.nlr.gov` (`BB_NREL_API_KEY`), `gis.cityofberkeley.info`, `services1.arcgis.com`, Socrata and ArcGIS layers | `scripts/build_layers.py` (`layer_sources.py`) |

## 5. Build tools

```
data/manifests/*.yaml ──▶ scripts/rebuild_city_data.py ──▶ <fgb_path>.fgb        geopandas, requests
<fgb_path>.fgb        ──▶ scripts/build_pmtiles.py     ──▶ frontend/tiles/*.pmtiles
                             └─ tippecanoe (system binary, release pinned in the script)
data/manifests + places ─▶ scripts/build_layers.py     ──▶ *.fgb + frontend/layers/*.geojson   requests, geopandas
frontend/icon.svg       ─▶ scripts/build_icons.py      ──▶ frontend/*.png                     cairosvg + system cairo
```

Outputs are committed, so the server and deploy need none of these tools. Commands: [data/README.md](../data/README.md#rebuild).

## 6. Host

```
Ubuntu Server 24.04 (Raspberry Pi)
  ├─ broombuster.service        uvicorn, hardened: ProtectHome, ProtectSystem=strict, syscall filter
  ├─ broombuster-update.timer   every 2 min ─▶ deploy/auto-update.sh (ProtectSystem=strict)
  │     ├─ git                  fetch, reset --hard, optional verify-commit
  │     ├─ uv                   uv sync --locked --extra api, system Python   (expected in ~/.local/bin)
  │     ├─ gh                   CI status of the commit
  │     └─ sudo                 sudoers rule: systemctl restart broombuster, nothing else
  └─ cloudflared                Cloudflare Tunnel ─▶ 127.0.0.1:8000
```

Set up by `deploy/install.sh`.

## 7. Development and CI

```
GitHub Actions
  ├─ ci.yml       actions/checkout · astral-sh/setup-uv · node (preinstalled on runner)
  │               ruff · pytest (api, dev and scripts extras) · node --check frontend/js/*.js
  └─ Dependabot   monthly for Python and for Actions
```

Local: `uv sync --locked --all-extras`, then `uv run pytest -q`. Frontend tests need `node`.
Layout check, local only: `uv run --extra visual python scripts/visual_scenarios.py` (Playwright WebKit +
Chromium, axe-core from cdn.jsdelivr.net, version in `AXE_URL`).
