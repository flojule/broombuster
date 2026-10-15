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

The frontend tests (`tests/test_urgency.py` and most of `tests/test_datetime.py`) need `node`; without it they are skipped.

## Layout

- `src/broombuster/api/`: FastAPI app. `POST /api/check` returns a car's street
  schedule, `GET /api/address` adds the house number, `POST /api/check-home` returns
  trash pickups. `api/auth.py` adds Google sign-in (`/auth/*`) and account data
  (`/api/me`, `/api/me/prefs`) in `data/users.sqlite`. It also serves `frontend/`.
  `config.py` holds every setting read from the environment.
- `frontend/`: the PWA. Urgency is computed client-side in `js/urgency.js`, and
  the map draws the PMTiles in `tiles/`. MapLibre and PMTiles are vendored in
  `vendor/`.
- `data/`: one manifest and one FlatGeobuf per city (see `data/README.md`).
- `scripts/`: data rebuild and tile build.

## Deploy (Raspberry Pi, systemd, Cloudflare Tunnel)

```bash
./deploy/install.sh            # installs the service and the update timer
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

The tunnel is the only way in: the app trusts `CF-Connecting-IP` for rate limits, so do not
expose port 8000 any other way (Tailscale Funnel, port forwarding).

| Step | Action |
|---|---|
| 1 | Cloudflare Zero Trust > Networks > Tunnels > Create (Cloudflared), name `broombuster`. |
| 2 | Run the shown install command on the host (`cloudflared service install <token>`). |
| 3 | Public hostname: `broombuster.com`, service `HTTP`, URL `localhost:8000`. |

### Cloudflare cache

The app sends its own `Cache-Control` (`api/web.py`); Cloudflare follows it.
Scripts and styles are requested as `/js/app.js?v=<content hash>` (the hash is
computed at startup and written into the page), so a deploy changes every URL
and nothing stale can be served. The page itself is `no-cache`; API paths are `no-store`.

| Step | Action |
|---|---|
| Once, after the first deploy of this | Caching > Configuration > Purge Everything. |
| Optional | Rules > Cache Rules: `/api*`, `/auth*` > Bypass cache (belt and braces). |
| Optional | Rules > Transform Rules > Managed Transforms > "Add visitor location headers" (exact `/locate` without the fallback lookup). |

## Security notes

- The tunnel is the only way in (see above); GitHub branch protection on `main` should
  require a PR and passing CI, because every commit on `main` is deployed.
- Cloudflare: SSL/TLS > Edge Certificates > enable HSTS.
- Content-Security-Policy is sent as `Content-Security-Policy-Report-Only` until you set
  `BB_CSP=enforce` in `.env` and restart. Load the site with DevTools open first; any
  `Content-Security-Policy-Report-Only` message in the console is a request the policy
  would block (`CSP` in `api/web.py`).

## Operations

- Logs are JSON lines (`journalctl -u broombuster -o cat | jq 'select(.status>=500)'`);
  every response carries `X-Request-ID`, which matches the log's `rid`.
- Rate limits are per client IP (`LIMITS` in `api/web.py`; `/api/check` 60/min, `/api/address`,
  `/api/locate` and `/api/check-home` 20/min). `BB_RATE_LIMIT=0` turns them off.
- `update.sh` rolls back to the previous revision if the new one is not healthy within
  3 minutes and records it in `.deploy-bad`, so the timer skips it until a newer commit lands.
  To roll back by hand: `./deploy/update.sh <sha>`.
- Dependabot opens weekly PRs for Python deps and monthly for Actions.

## License

The code is MIT-licensed (`LICENSE`). Vendored libraries in `frontend/vendor/` keep their own
licences (see `frontend/vendor/README.md`), and the city street-sweeping data in `data/` comes
from each city's open-data portal under that city's terms.
