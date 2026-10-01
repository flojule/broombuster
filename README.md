# BroomBuster

A map of street-sweeping schedules (Bay Area: Oakland, San Francisco, Berkeley,
Alameda; Chicago). Drop a car on the map to see whether its street is swept
today or tomorrow, and a home in Oakland to see its trash pickup days. Cars and
homes are kept in the browser.

## Run

```bash
uv sync --locked --extra api
./run.sh                                # http://localhost:8000
uv sync --locked --all-extras && uv run pytest && uv run ruff check .
```

Tests in `tests/test_urgency.py` need `node`.

## Layout

- `src/broombuster/api/`: FastAPI app. `POST /check` returns a car's street
  schedule, `GET /address` adds the house number, `POST /check-home` returns trash
  pickups. It also serves `frontend/`.
- `frontend/`: the PWA. Urgency is computed client-side in `js/urgency.js`, and
  the map draws the PMTiles in `tiles/`. MapLibre and PMTiles are vendored in
  `vendor/`.
- `data/`: one manifest and one FlatGeobuf per city (see `data/README.md`).
- `scripts/`: data rebuild and tile build.

## Deploy (Raspberry Pi, systemd, Tailscale Funnel)

```bash
./deploy/install.sh            # installs the service and the update timer
tailscale funnel --bg 8000
```

The timer runs `deploy/auto-update.sh` every 2 minutes. It deploys `origin/main`
once CI has passed on that commit (`gh` must be logged in). To deploy by hand,
run `./deploy/update.sh`. Logs: `journalctl -u broombuster -u broombuster-update`.
