import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from broombuster import gps, ics, maps, resolve
from broombuster.cities import CITIES, REGIONS, city_for_point
from broombuster.config import (
    ALLOW_REGISTRATION,
    DATA_AUTO_REFRESH,
    DEV_MODE,
    PMTILES_MODE,
    REPO_ROOT,
)
from broombuster.domains import for_city as plugins_for_city

from . import db
from .auth import init_rate_limiting, rate_limit
from .auth import router as auth_router
from .deps import verify_jwt
from .helpers import _build_address, _clip_region_for_request, _resolve_region
from .state import (
    _city_events,
    _city_gdfs,
    _freshness_checker_bg,
    _get_region_gdfs,
    _load_city,
    data_age_days,
    logger,
    warm_region,
)

_PRELOAD_REGION = os.environ.get("PRELOAD_REGION", "").strip()
_RESPONSE_SIZE_WARN_BYTES = 200_000

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Kick off all city loads in parallel — server is immediately ready.
    for rv in REGIONS.values():
        for ck in rv["cities"]:
            _city_events[ck] = threading.Event()
            threading.Thread(target=_load_city, args=(ck,), daemon=True).start()
    # Synchronously wait for the preload region before accepting traffic.
    # Set PRELOAD_REGION=bay_area (or any region key) in the environment to
    # ensure the first /check after boot is instant rather than waiting in-band.
    if _PRELOAD_REGION and _PRELOAD_REGION in REGIONS:
        logger.info("[preload] waiting for region '%s'…", _PRELOAD_REGION)
        for ck in REGIONS[_PRELOAD_REGION]["cities"]:
            ev = _city_events.get(ck)
            if ev:
                ev.wait(timeout=120)
        warm_region(_PRELOAD_REGION)
        logger.info("[preload] region '%s' ready", _PRELOAD_REGION)
    # Background freshness checker — runs after startup, checks hourly. Opt-in:
    # deployments ship refreshed data through git instead (see config.py).
    if DATA_AUTO_REFRESH:
        threading.Thread(target=_freshness_checker_bg, daemon=True).start()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# The interactive API docs are a development aid; the production app is public
# (Tailscale Funnel), so don't advertise the schema there.
_docs = {} if DEV_MODE else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="BroomBuster API", lifespan=lifespan, **_docs)

# No CORS middleware: the frontend is served from this same origin.

# Baseline hardening headers on every response. The CSP covers framing, plugins
# and <base> only; a script-src policy would have to allow the inline module in
# index.html and MapLibre's blob: workers, so it is left out until it can be
# verified in a browser.
_SECURITY_HEADERS = {
    "X-Content-Type-Options":  "nosniff",
    "Referrer-Policy":         "strict-origin-when-cross-origin",
    "X-Frame-Options":         "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    return response


# Compress responses over 1 KB. The /check GeoJSON is verbose text and gzips
# ~5-8x; this is the interim win for SF before PMTiles removes the payload.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(auth_router)
init_rate_limiting(app)

# ---------------------------------------------------------------------------
# Routes — public
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    loaded  = [ck for ck, ev in _city_events.items() if ev.is_set() and ck in _city_gdfs]
    loading = [ck for ck, ev in _city_events.items() if not ev.is_set()]
    failed  = [ck for ck, ev in _city_events.items() if ev.is_set() and ck not in _city_gdfs]

    # Per-city data freshness info for cities with auto-download configured.
    freshness = {}
    for ck, city in CITIES.items():
        stale_after = city.get("stale_after_days")
        if not stale_after:
            continue
        age_days = data_age_days(city)
        if age_days is not None:
            freshness[ck] = {
                "age_days":        round(age_days, 1),
                "stale_after_days": stale_after,
                "stale":           age_days >= stale_after,
            }

    return {
        "status":    "ok",
        "loaded":    loaded,
        "loading":   loading,
        "failed":    failed,
        "freshness": freshness,
    }


@app.get("/cities")
def cities():
    return {
        "regions": {
            k: {
                "name": v["name"],
                "cities": v["cities"],
                "center": v["center"],
                "overview_zoom": v.get("overview_zoom", 11),
            }
            for k, v in REGIONS.items()
        },
        "cities": {
            k: {"name": v["name"], "center": v["center"]}
            for k, v in CITIES.items()
        },
    }


# ---------------------------------------------------------------------------
# Routes — public (guests use them too), rate-limited per IP
# ---------------------------------------------------------------------------

# /check also serves legacy tile-only map requests on every pan; /check-home
# calls Nominatim and ReCollect.
_CHECK_RATE = "120/minute"
_CHECK_HOME_RATE = "20/minute"


def _domain_dict(result) -> dict:
    return {
        "id":             result.domain_id,
        "label":          result.label,
        "urgency":        result.urgency,
        "schedule_lines": list(result.schedule_lines),
        "extras":         dict(result.extras),
    }


class CheckRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: str | None = None
    full_region: bool | None = False
    # bbox as [min_lat, min_lon, max_lat, max_lon] — exactly four entries
    bbox: list[float] | None = Field(None, min_length=4, max_length=4)
    # Up to 64 tiles per request; each entry validated as "z/x/y" at use site
    tiles: list[str] | None = Field(None, max_length=64)


@app.post("/check")
@rate_limit(_CHECK_RATE)
def check(req: CheckRequest, request: Request):
    region, local_now = _resolve_region(req)

    # If client explicitly requested the full region, synchronously load
    # any missing city data first so the combined region GDF is complete.
    if req.full_region:
        for ck in REGIONS[region]["cities"]:
            _load_city(ck)

    myCity_4326, myCity_3857 = _get_region_gdfs(req.lat, req.lon, region)
    if myCity_4326 is None:
        raise HTTPException(
            status_code=503,
            detail=f"No data available for region '{region}' yet — try again shortly.",
        )

    # Tile-only requests (map background rendering) don't need geocoding or
    # street-sweeping analysis — skipping them cuts response time by ~2-3 s.
    is_tile_only = bool(req.tiles) and not req.full_region

    # Legacy top-level fields mirror the sweeping plugin; `domains[]` carries
    # every car-subject plugin's result.
    sweep_extras: dict = {}
    urgency: object = False
    message:  str = ""
    address:  str = ""
    address_pending = False
    snap: dict | None = None
    detail_html: str = ""
    domain_results: list[dict] = []
    city_key = city_for_point(req.lat, req.lon, region)

    if not is_tile_only:
        # SINGLE SOURCE OF TRUTH — one authoritative segment drives street
        # name, side, schedule, urgency, map highlight and the city itself.
        resolved, city_key = resolve.locate(myCity_3857, req.lat, req.lon, region)
        # Street-level address now; a Nominatim house number (up to ~1 s)
        # is left to GET /address when not already cached.
        address, address_pending = _build_address(
            resolved, city_key, req.lat, req.lon, network=False)
        if resolved is not None:
            snap = {
                "street_name": resolved.label,
                "distance_m":  round(resolved.distance_m, 1),
                "is_polygon":  resolved.is_polygon,
            }
        else:
            message = "Car not near a mapped street."

        for plugin in plugins_for_city(city_key):
            # /check is the car flow; home-subject domains are served by /check-home.
            if plugin.subject != "car":
                continue
            plugin_resolved = (
                resolved if plugin.domain_id == "sweeping"
                else plugin.resolve_for(myCity_3857, req.lat, req.lon, city_key)
            )
            result = plugin.format(plugin_resolved, myCity_3857, local_now)
            domain_results.append(_domain_dict(result))
            if plugin.domain_id != "sweeping":
                continue
            sweep_extras = result.extras
            # Legacy `urgency` uses False (not "safe") for the no-urgency state.
            urgency = result.urgency if result.urgency in ("today", "tomorrow") else False
            if resolved is None:
                continue
            message = sweep_extras.get("message") or ""
            if sweep_extras["schedule_even"] or sweep_extras["schedule_odd"]:
                # Same window a street/ward click shows, for every entry.
                detail_html = maps.zone_detail_html(
                    resolved.label,
                    sweep_extras["schedule_even"], sweep_extras["schedule_odd"],
                    city_key, local_now, sweep_extras["car_side"],
                    tuple(sweep_extras["side_labels"]),
                )

    # In PMTILES mode the map renders from static vector tiles, so /check skips
    # the per-request clip + GeoJSON build entirely and returns no `geojson`.
    if PMTILES_MODE:
        geojson = None
    else:
        myCity_display, simplify_tolerance = _clip_region_for_request(req, myCity_4326)
        geojson = maps.build_map_geojson(
            myCity_display, local_now=local_now, simplify_tolerance=simplify_tolerance,
        )
        geojson_size = len(json.dumps(geojson).encode())
        if geojson_size > _RESPONSE_SIZE_WARN_BYTES:
            logger.warning(
                "/check geojson exceeds 200 KB (%d B) — region=%s lat=%.4f lon=%.4f clip=%s",
                geojson_size, region, req.lat, req.lon,
                "tiles" if req.tiles else ("full" if req.full_region else "radius"),
            )

    return {
        "region": region,
        "message": message,
        "urgency": urgency,
        "schedule_even": sweep_extras.get("schedule_even", []),
        "schedule_odd": sweep_extras.get("schedule_odd", []),
        # "even" | "odd" | None (unknown side).
        "car_side": sweep_extras.get("car_side"),
        # Display labels for the even / odd buckets (e.g. SF ["North", "South"]).
        "side_labels": sweep_extras.get("side_labels", ["Even", "Odd"]),
        "address": address,
        # True when GET /address may return a fuller address (house number).
        "address_pending": address_pending,
        "detail_html": detail_html,
        "geojson": geojson,
        # Which segment the resolver chose and how far the car is from it.
        "snap": snap,
        "domains": domain_results,
    }


# Each uncached call costs one Nominatim request (usage policy: <= 1/s).
_ADDRESS_RATE = "30/minute"


@app.get("/address")
@rate_limit(_ADDRESS_RATE)
def address(request: Request,
            lat: float = Query(..., ge=-90.0, le=90.0),
            lon: float = Query(..., ge=-180.0, le=180.0),
            region: str | None = None):
    """Canonical car address including the Nominatim house number.

    /check answers with the street-level form and `address_pending`; the
    client calls this afterwards so /check itself never waits on Nominatim.
    """
    region, _ = _resolve_region(CheckRequest(lat=lat, lon=lon, region=region))
    _, gdf_3857 = _get_region_gdfs(lat, lon, region)
    if gdf_3857 is None:
        raise HTTPException(503, f"No data available for region '{region}' yet.")
    resolved, city_key = resolve.locate(gdf_3857, lat, lon, region)
    addr, _ = _build_address(resolved, city_key, lat, lon)
    return {"address": addr}


class CheckHomeRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: str | None = None
    # Postal address for address-keyed lookups (e.g. ReCollect trash).
    address: str | None = None


@app.post("/check-home")
@rate_limit(_CHECK_HOME_RATE)
def check_home(req: CheckHomeRequest, request: Request):
    """Home flow: run home-subject domains (trash) at a saved residence.

    Separate from /check (the car flow) because a home is located by address,
    not by the parked-car coordinate, and uses a different plugin subject.
    """
    region, local_now = _resolve_region(req)
    _, region_3857 = _get_region_gdfs(req.lat, req.lon, region)
    _, city_key = resolve.locate(region_3857, req.lat, req.lon, region)

    # Home pins dropped by tap/right-click carry no address; reverse-geocode the
    # coordinate so the card can show one (and address-based plugins can match).
    address = req.address or gps.reverse_address(req.lat, req.lon)

    domain_results = [
        _domain_dict(plugin.format(
            plugin.resolve_for(None, req.lat, req.lon, city_key, address=address),
            None, local_now,
        ))
        for plugin in plugins_for_city(city_key) if plugin.subject == "home"
    ]
    return {"city": city_key, "region": region, "address": address,
            "domains": domain_results}


_CALENDAR_SIDES = {"both": ("even", "odd"), "even": ("even",), "odd": ("odd",)}


@app.get("/calendar.ics")
@rate_limit(_CHECK_RATE)
def calendar_ics(request: Request,
                 lat: float = Query(..., ge=-90.0, le=90.0),
                 lon: float = Query(..., ge=-180.0, le=180.0),
                 side: str = Query("auto", pattern="^(auto|both|even|odd)$"),
                 region: str | None = None):
    """Subscribable iCalendar feed of the sweeping windows at a parked spot.

    side=auto publishes the car's side when known, else both sides.
    """
    req = CheckRequest(lat=lat, lon=lon, region=region)
    region, local_now = _resolve_region(req)
    _, gdf_3857 = _get_region_gdfs(lat, lon, region)
    if gdf_3857 is None:
        raise HTTPException(503, f"No data available for region '{region}' yet.")
    resolved, city_key = resolve.locate(gdf_3857, lat, lon, region)
    if resolved is None:
        raise HTTPException(404, "No mapped street near this location.")
    plugin = next(p for p in plugins_for_city(city_key) if p.domain_id == "sweeping")
    extras = plugin.format(resolved, gdf_3857, local_now).extras
    if side == "auto":
        side = extras["car_side"] or "both"
    body = ics.build_calendar(
        extras["schedule_even"], extras["schedule_odd"], _CALENDAR_SIDES[side],
        tuple(extras["side_labels"]), resolved.label,
        REGIONS[region].get("tz", "UTC"), f"{lat:.5f},{lon:.5f},{side}", local_now.date(),
    )
    return Response(content=body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="street-sweeping.ics"'})


# ---------------------------------------------------------------------------
# Routes — authenticated
# ---------------------------------------------------------------------------


# Bounds on what one account can store (the shared account's login is handed out).
_PREFS_RATE = "60/minute"
_PREFS_MAX_BYTES = 64_000


class PrefsRequest(BaseModel):
    home_lat: float | None = None
    home_lon: float | None = None
    home_address: str | None = Field(None, max_length=500)
    homes: list[dict] | None = Field(default_factory=list, max_length=20)
    preferred_region: str | None = Field("bay_area", max_length=64)
    notify_email: bool | None = False
    cars: list[dict] | None = Field(default_factory=list, max_length=50)


@app.get("/prefs")
@rate_limit(_PREFS_RATE)
def get_prefs(request: Request, user_id: str = Depends(verify_jwt)):
    return db.get_prefs(user_id)


@app.post("/prefs")
@rate_limit(_PREFS_RATE)
def save_prefs(req: PrefsRequest, request: Request, user_id: str = Depends(verify_jwt)):
    prefs = req.model_dump()
    if len(json.dumps(prefs)) > _PREFS_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Preferences too large")
    db.save_prefs(user_id, prefs)
    return {"saved": True}


# ---------------------------------------------------------------------------
# Runtime config endpoint — injected into the frontend as window globals
# ---------------------------------------------------------------------------

@app.get("/config.js", include_in_schema=False)
def config_js():
    """Serve runtime config as a JS snippet so the frontend knows runtime flags."""
    flags = {
        "DEV_MODE": DEV_MODE,
        "PMTILES_MODE": PMTILES_MODE,
        "ALLOW_REGISTRATION": ALLOW_REGISTRATION,
        "REGION_TZ": {rk: rv.get("tz", "UTC") for rk, rv in REGIONS.items()},
    }
    js = "".join(f"window.{k} = {json.dumps(v)};\n" for k, v in flags.items())
    return Response(content=js, media_type="application/javascript")


@app.get("/zone/detail", include_in_schema=False)
@rate_limit(_CHECK_RATE)
def zone_detail(request: Request, code: list[str] = Query(default=[]), street: str = "",
                city: str = "", region: str = ""):
    """Full-year zone schedule HTML + PDF link for a clicked tile (PMTILES mode).

    `code` repeats once per schedule entry of the clicked feature.
    """
    tz = REGIONS.get(region, {}).get("tz", "UTC")
    entries = [(c, "", "") for c in code if c]
    html = maps.zone_detail_html(street, entries, [], city, datetime.now(ZoneInfo(tz)))
    return {"detail_html": html}


# ---------------------------------------------------------------------------
# Static frontend (mounted last so API routes take priority)
# ---------------------------------------------------------------------------

_frontend_dir = os.path.join(REPO_ROOT, "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
