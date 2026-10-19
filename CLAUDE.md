# BroomBuster

Street-sweeping map: FastAPI backend (`src/broombuster/`), plain-JS PWA (`frontend/`),
committed per-city FlatGeobuf data (`data/`), deployed to a Raspberry Pi by a systemd timer.
README.md covers using and running; `docs/architecture.md` the architecture and deployment;
`docs/phone-setup.md` phone position updates.

## Commands

```bash
uv sync --locked --all-extras
uv run ruff check . && uv run ruff format .   # CI runs both (format with --check)
uv run pytest -q          # frontend (JS) tests need node and are skipped without it
./run.sh                  # http://localhost:8000
```

## Conventions

- **Every push to `main` is auto-deployed** once CI passes (`deploy/auto-update.sh`).
  Keep `main` releasable; the only health check is `/health`.
- **Python style** is `ruff format` (line length 100) plus `ruff check`, both in
  `pyproject.toml`. Fix findings; add `# noqa: CODE (reason)` only for a false positive, never
  a global ignore. `.git-blame-ignore-revs` hides the one-time format commit
  (`git config blame.ignoreRevsFile .git-blame-ignore-revs`).
- **Settings** come from `broombuster/config.py` only (ruff bans `os.environ` elsewhere).
- **Dates**: a car lands on its own city's day. Use timezone-aware datetimes in Python
  (ruff `DTZ`); in the frontend use the region clock (`tests/test_datetime.py` checks it).
- **Frontend**: classic `<script defer>` files that share globals, loaded in the order listed in
  `frontend/index.html`. No build step. Escape every interpolated value with `esc()` before
  `innerHTML`. `js/maplibre-global.js` is the one ES module. No inline scripts or
  event-handler attributes: the CSP forbids them (`tests/test_conventions.py` checks).
- **Assets are versioned by content hash** (`api/web.py`); `sw.js` is a template the server
  fills in (version, API paths, precache list). Never hard-code a cache version.
- **API**: everything the frontend fetches lives under `/api/`, except `/auth/*` (passkey
  sign-in) and `/health` (used by the deploy script). The list of such prefixes is `API_PATHS`
  in `api/web.py`.
- **Client IP** comes from `web.client_ip` (validated `CF-Connecting-IP`). The app must only
  be reachable through the Cloudflare tunnel.
- **Data**: change a city by editing its manifest/raw input and running
  `scripts/rebuild_city_data.py` then `scripts/build_pmtiles.py` (see `data/README.md`);
  commit the `.fgb` and `.pmtiles` outputs.
- **Tests** must pass alone and in any order; a test that needs city data takes `app_client`.
