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

from broombuster import domains, gps, ics, maps
from broombuster.cities import CITIES, REGIONS
from broombuster.config import ALLOW_REGISTRATION, DEV_MODE, REPO_ROOT

from . import db
from .auth import init_rate_limiting, rate_limit
from .auth import router as auth_router
from .deps import verify_jwt
from .helpers import _build_address, _locate
from .state import _city_events, _city_gdfs, _load_city, data_age_days, logger, warm_region

_PRELOAD_REGION = os.environ.get("PRELOAD_REGION", "").strip()

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
    # PRELOAD_REGION=<key> holds startup until that region is loaded and warm,
    # so the first request after boot never waits in-band.
    if _PRELOAD_REGION in REGIONS:
        logger.info("[preload] waiting for region '%s'…", _PRELOAD_REGION)
        for ck in REGIONS[_PRELOAD_REGION]["cities"]:
            _city_events[ck].wait(timeout=120)
        warm_region(_PRELOAD_REGION)
        logger.info("[preload] region '%s' ready", _PRELOAD_REGION)
    yield


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


# Compress responses over 1 KB — mostly the vendored MapLibre bundle and other
# static assets (PMTiles range responses are skipped as partial content).
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

    # Data age for cities that publish a staleness threshold.
    freshness = {}
    for ck, city in CITIES.items():
        stale_after = city.get("stale_after_days")
        age_days = data_age_days(city) if stale_after else None
        if age_days is not None:
            freshness[ck] = {
                "age_days":         round(age_days, 1),
                "stale_after_days": stale_after,
                "stale":            age_days >= stale_after,
            }

    return {"status": "ok", "loaded": loaded, "loading": loading, "failed": failed,
            "freshness": freshness}


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
        "cities": {k: {"name": v["name"], "center": v["center"]} for k, v in CITIES.items()},
    }


# ---------------------------------------------------------------------------
# Routes — public (guests use them too), rate-limited per IP
# ---------------------------------------------------------------------------

_CHECK_RATE = "120/minute"
# These call Nominatim (usage policy: <= 1 request/s) and, for homes, ReCollect.
_ADDRESS_RATE = "30/minute"
_CHECK_HOME_RATE = "20/minute"


class CheckRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: str | None = None


@app.post("/check")
@rate_limit(_CHECK_RATE)
def check(req: CheckRequest, request: Request):
    """Car flow: every car-subject domain (street sweeping, …) at a parked spot."""
    loc = _locate(req.lat, req.lon, req.region)
    # Street-level address now; a Nominatim house number (up to ~1 s) is left
    # to GET /address when not already cached.
    address, address_pending = _build_address(
        loc.resolved, loc.city_key, req.lat, req.lon, network=False)
    return {
        "region": loc.region,
        "city": loc.city_key,
        "address": address,
        # True when GET /address may return a fuller address (house number).
        "address_pending": address_pending,
        "domains": [
            p.format(p.resolve_for(loc.gdf_3857, req.lat, req.lon, loc.city_key),
                     loc.gdf_3857, loc.local_now).as_dict()
            for p in domains.for_city(loc.city_key) if p.subject == "car"
        ],
    }


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
    loc = _locate(lat, lon, region)
    return {"address": _build_address(loc.resolved, loc.city_key, lat, lon)[0]}


class CheckHomeRequest(CheckRequest):
    # Postal address for address-keyed lookups (e.g. ReCollect trash).
    address: str | None = Field(None, max_length=500)


@app.post("/check-home")
@rate_limit(_CHECK_HOME_RATE)
def check_home(req: CheckHomeRequest, request: Request):
    """Home flow: home-subject domains (trash) at a saved residence.

    A home is located by address, not by the parked-car coordinate; pins
    dropped by tap carry none, so the coordinate is reverse-geocoded.
    """
    loc = _locate(req.lat, req.lon, req.region)
    address = req.address or gps.reverse_address(req.lat, req.lon)
    return {
        "region": loc.region,
        "city": loc.city_key,
        "address": address,
        "domains": [
            p.format(p.resolve_for(None, req.lat, req.lon, loc.city_key, address=address),
                     None, loc.local_now).as_dict()
            for p in domains.for_city(loc.city_key) if p.subject == "home"
        ],
    }


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
    loc = _locate(lat, lon, region)
    if loc.resolved is None:
        raise HTTPException(404, "No mapped street near this location.")
    extras = domains.get("sweeping").format(loc.resolved, loc.gdf_3857, loc.local_now).extras
    if side == "auto":
        side = extras["car_side"] or "both"
    body = ics.build_calendar(
        extras["schedule_even"], extras["schedule_odd"], _CALENDAR_SIDES[side],
        tuple(extras["side_labels"]), loc.resolved.label, REGIONS[loc.region]["tz"],
        f"{lat:.5f},{lon:.5f},{side}", loc.local_now.date(),
    )
    return Response(content=body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="street-sweeping.ics"'})


@app.get("/zone/detail", include_in_schema=False)
@rate_limit(_CHECK_RATE)
def zone_detail(request: Request, code: list[str] = Query(default=[]), street: str = "",
                city: str = "", region: str = ""):
    """Full-year zone schedule HTML + PDF link for a clicked tile.

    `code` repeats once per schedule entry of the clicked feature.
    """
    tz = REGIONS.get(region, {}).get("tz", "UTC")
    entries = [(c, "", "") for c in code if c]
    html = maps.zone_detail_html(street, entries, [], city, datetime.now(ZoneInfo(tz)))
    return {"detail_html": html}


# ---------------------------------------------------------------------------
# Routes — authenticated
# ---------------------------------------------------------------------------

# Bounds on what one account can store (the shared account's login is handed out).
_PREFS_RATE = "60/minute"
_PREFS_MAX_BYTES = 64_000


class PrefsRequest(BaseModel):
    cars: list[dict] = Field(default_factory=list, max_length=50)
    homes: list[dict] = Field(default_factory=list, max_length=20)


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
# Runtime config, injected into the frontend as window globals
# ---------------------------------------------------------------------------

@app.get("/config.js", include_in_schema=False)
def config_js():
    flags = {
        "DEV_MODE": DEV_MODE,
        "ALLOW_REGISTRATION": ALLOW_REGISTRATION,
        "REGION_TZ": {rk: rv["tz"] for rk, rv in REGIONS.items()},
    }
    js = "".join(f"window.{k} = {json.dumps(v)};\n" for k, v in flags.items())
    return Response(content=js, media_type="application/javascript")


# ---------------------------------------------------------------------------
# Static frontend (mounted last so API routes take priority)
# ---------------------------------------------------------------------------

_frontend_dir = os.path.join(REPO_ROOT, "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
