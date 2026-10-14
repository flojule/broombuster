# BroomBuster

A map of street-sweeping schedules (Bay Area: Oakland, San Francisco, Berkeley,
Alameda; Chicago). Drop a car on the map to see whether its street is swept
today or tomorrow, and a home in Oakland to see its trash pickup days. Cars and
homes are kept in the browser; with Google sign-in they sync to the account.

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
  pickups. `api/auth.py` adds Google sign-in (`/auth/*`) and account data
  (`/me`, `/me/prefs`) in `data/users.sqlite`. It also serves `frontend/`.
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

## Sign-in (Google)

| Step | Action |
|---|---|
| 1 | Google Cloud Console > Credentials > OAuth client (Web). Authorized redirect URIs: `https://broombuster.com/auth/callback` (add `http://localhost:8000/auth/callback` for local runs). |
| 2 | Copy `.env.example` to `.env` (`install.sh` does this) and fill `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BB_BASE_URL`. All three empty = no sign-in. |
| 3 | Restart the service. `/me` reports `"enabled": true`. |

Sessions are 30-day cookies backed by SQLite. Unit changes need `./deploy/install.sh`; `update.sh` only restarts.

## Public access (Cloudflare Tunnel)

| Step | Action |
|---|---|
| 1 | Cloudflare Zero Trust > Networks > Tunnels > Create (Cloudflared), name `broombuster`. |
| 2 | Run the shown install command on the host (`cloudflared service install <token>`). |
| 3 | Public hostname: `broombuster.com`, service `HTTP`, URL `localhost:8000`. |
