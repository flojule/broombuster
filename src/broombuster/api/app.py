import json
import logging
import os
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import NamedTuple
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from broombuster import analysis, gps, resolve, trash
from broombuster.cities import CITIES, REGIONS, city_for_point, region_for_point
from broombuster.config import REPO_ROOT

from . import auth
from .state import load_all, region_gdf

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_all()
    if auth.check_config():
        auth.init_db()
    yield


app = FastAPI(title="BroomBuster", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

_SECURITY_HEADERS = {
    "X-Content-Type-Options":  "nosniff",
    "Referrer-Policy":         "strict-origin-when-cross-origin",
    "X-Frame-Options":         "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
}


# Cloudflare caches .js/.css/.png at the edge for hours when the origin sends no
# Cache-Control, serving stale scripts against fresh HTML. The shell must always
# revalidate (ETag makes that a cheap 304); tiles and vendored libs change rarely.
_API_PATHS = ("/check", "/address", "/cities", "/health", "/auth", "/me", "/locate")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    if "cache-control" not in response.headers:
        path = request.url.path
        if path.startswith(_API_PATHS):
            response.headers["Cache-Control"] = "no-store"
        elif path.startswith(("/tiles/", "/vendor/")):
            response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(auth.router)


class Located(NamedTuple):
    region: str
    local_now: datetime
    gdf_3857: object
    resolved: resolve.ResolvedCar | None
    city_key: str


def _locate(lat: float, lon: float, region: str | None) -> Located:
    region = region if region in REGIONS else region_for_point(lat, lon)
    gdf = region_gdf(region)
    resolved, city_key = resolve.locate(gdf, lat, lon, region)
    return Located(region, datetime.now(ZoneInfo(REGIONS[region]["tz"])), gdf, resolved, city_key)


def _address(loc: Located, lat: float, lon: float, *, network: bool) -> tuple[str, bool]:
    """(address, house_number_pending) for a located car.

    Zones read "Zone: <name>, <city>"; streets get a Nominatim house number when
    it matches the resolved street. network=False uses cached house numbers only,
    and `pending` says whether GET /address could still add one.
    """
    resolved = loc.resolved
    if resolved is None or not resolved.label:
        return f"{lat:.4f}, {lon:.4f}", False
    city = CITIES[loc.city_key]["name"].split(",")[0]
    if resolved.is_polygon:
        return f"Zone: {resolved.label}, {city}", False
    pending = (not network and bool(resolved.street_name)
               and not gps.house_number_cached(lat, lon))
    hn = gps.maybe_house_number(lat, lon, resolved.street_name, network=network)
    return (f"{hn} " if hn else "") + f"{resolved.label}, {city}", pending


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cities")
def cities():
    return {
        rk: {"name": rv["name"], "center": rv["center"], "tz": rv["tz"],
             "zoom": rv["zoom"]}
        for rk, rv in REGIONS.items()
    }


@lru_cache(maxsize=512)
def _ip_lookup(ip: str) -> tuple[float, float] | None:
    try:
        req = urllib.request.Request(f"https://ipapi.co/{ip}/json/",
                                     headers={"User-Agent": "broombuster"})
        with urllib.request.urlopen(req, timeout=3.0) as r:
            d = json.load(r)
        return (float(d["latitude"]), float(d["longitude"]))
    except Exception:
        return None


@app.get("/locate")
def locate(request: Request):
    """Approximate visitor position from the IP, and the nearest area.

    Uses Cloudflare's visitor-location headers when enabled, else a lookup of the
    client IP. Done server-side so ad blockers and CORS can't break it.
    """
    h = request.headers
    pos = None
    try:
        pos = (float(h["cf-iplatitude"]), float(h["cf-iplongitude"]))
    except (KeyError, ValueError):
        ip = h.get("cf-connecting-ip") or (request.client.host if request.client else "")
        if ip and not ip.startswith(("127.", "10.", "192.168.", "::1")):
            pos = _ip_lookup(ip)
    if pos is None:
        return {"lat": None, "lon": None, "region": None}
    return {"lat": pos[0], "lon": pos[1], "region": region_for_point(*pos)}


class CheckRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: str | None = None


@app.post("/check")
def check(req: CheckRequest):
    """Sweeping schedule of the street (or zone) a car is parked on.

    Urgency is computed by the client from the raw schedules, so it follows
    the live clock.
    """
    loc = _locate(req.lat, req.lon, req.region)
    address, pending = _address(loc, req.lat, req.lon, network=False)
    even, odd = (analysis.schedules_for_all_matching_rows(loc.gdf_3857, loc.resolved)
                 if loc.resolved else ([], []))
    return {
        "region": loc.region,
        "address": address,
        "address_pending": pending,
        "car_side": loc.resolved.side if loc.resolved else None,
        "side_labels": list(analysis.side_labels(loc.resolved.segment)
                            if loc.resolved else analysis.DEFAULT_SIDE_LABELS),
        "schedule_even": even,
        "schedule_odd": odd,
    }


@app.get("/address")
def address(lat: float = Query(..., ge=-90.0, le=90.0),
            lon: float = Query(..., ge=-180.0, le=180.0),
            region: str | None = None):
    """The /check address with the Nominatim house number (may take ~1 s)."""
    return {"address": _address(_locate(lat, lon, region), lat, lon, network=True)[0]}


class CheckHomeRequest(CheckRequest):
    address: str | None = Field(None, max_length=500)


@app.post("/check-home")
def check_home(req: CheckHomeRequest):
    """Trash pickup dates at a home; tap-placed homes are reverse-geocoded."""
    region = req.region if req.region in REGIONS else region_for_point(req.lat, req.lon)
    address = req.address or gps.reverse_address(req.lat, req.lon) or ""
    today = datetime.now(ZoneInfo(REGIONS[region]["tz"])).date()
    city = city_for_point(req.lat, req.lon, region)
    return {"region": region, "address": address,
            "pickups": trash.pickups(city, address, today)}


_frontend_dir = os.path.join(REPO_ROOT, "frontend")
app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
