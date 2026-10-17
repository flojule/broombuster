import ipaddress
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from broombuster import analysis, calfeed, gps, holidays, iplocate, layers, resolve, trash
from broombuster.cities import CITIES, REGIONS, city_for_point, region_bbox, region_for_point
from broombuster.config import REPO_ROOT
from broombuster.version import app_version

from . import auth, web
from .state import load_all, region_gdf

web.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_all()
    if auth.check_config():
        auth.init_db()
    yield


app = FastAPI(title="BroomBuster", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

app.middleware("http")(web.http_middleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)
api = APIRouter(prefix="/api")  # everything the frontend fetches, except /auth and /health
app.include_router(auth.router)


class Located(NamedTuple):
    region: str
    gdf_3857: object
    resolved: resolve.ResolvedCar | None
    city_key: str


def _locate(lat: float, lon: float, region: str | None) -> Located:
    region = region if region in REGIONS else region_for_point(lat, lon)
    gdf = region_gdf(region)
    resolved, city_key = resolve.locate(gdf, lat, lon, region)
    return Located(region, gdf, resolved, city_key)


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
    return {"status": "ok", "version": app_version()}


@api.get("/cities")
def cities():
    return {
        rk: {"name": rv["name"], "center": rv["center"], "tz": rv["tz"],
             "zoom": rv["zoom"], "holidays": holidays.for_region(rk),
             "cities": {ck: CITIES[ck]["name"].split(",")[0] for ck in rv["cities"]},
             "layers": layers.catalogue(rk)}
        for rk, rv in REGIONS.items()
    }


@api.get("/locate")
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
        try:
            ip = ipaddress.ip_address(web.client_ip(request))
        except ValueError:
            ip = None
        if ip is not None and ip.is_global:
            pos = iplocate.lookup(str(ip))
    if pos is None:
        return {"lat": None, "lon": None, "region": None}
    return {"lat": pos[0], "lon": pos[1], "region": region_for_point(*pos)}


class CheckRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: str | None = None


@api.post("/check")
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
        "city": loc.city_key,
        "address": address,
        "address_pending": pending,
        "car_side": loc.resolved.side if loc.resolved else None,
        "side_labels": list(analysis.side_labels(loc.resolved.segment)
                            if loc.resolved else analysis.DEFAULT_SIDE_LABELS),
        "schedule_even": even,
        "schedule_odd": odd,
    }


@api.get("/address")
def address(lat: float = Query(..., ge=-90.0, le=90.0),
            lon: float = Query(..., ge=-180.0, le=180.0),
            region: str | None = None):
    """The /check address with the Nominatim house number (may take ~1 s)."""
    return {"address": _address(_locate(lat, lon, region), lat, lon, network=True)[0]}


@api.get("/geocode")
def geocode(q: str = Query(..., min_length=1, max_length=200), region: str | None = None):
    """Position of a typed address, searched inside the region's cities when one is
    given; lat/lon are null when nothing matches. 503 when the geocoder is unavailable."""
    hit = gps.search(q, region_bbox(region) if region in REGIONS else None)
    if hit is None:
        raise HTTPException(503, "Address lookup unavailable")
    return {"lat": hit.get("lat"), "lon": hit.get("lon")}


class CheckHomeRequest(CheckRequest):
    address: str | None = Field(None, max_length=500)
    collection_day: int | None = Field(None, ge=0, le=6)  # Mon=0, for trash kind "weekly"


@api.post("/check-home")
def check_home(req: CheckHomeRequest):
    """Trash pickup dates at a home; tap-placed homes are reverse-geocoded.

    trash_kind tells the client whether the city looks pickups up ("recollect") or
    needs the home's collection day ("weekly"); null where nothing is known."""
    region = req.region if req.region in REGIONS else region_for_point(req.lat, req.lon)
    address = req.address or gps.reverse_address(req.lat, req.lon) or ""
    today = datetime.now(ZoneInfo(REGIONS[region]["tz"])).date()
    city = city_for_point(req.lat, req.lon, region)
    return {"region": region, "city": city, "address": address,
            "trash_kind": trash.kind(city),
            "pickups": trash.pickups(city, address, today, req.collection_day)}


@api.get("/rules")
def rules(lat: float = Query(..., ge=-90.0, le=90.0),
          lon: float = Query(..., ge=-180.0, le=180.0),
          region: str | None = None,
          kind: str = Query("car", pattern="^(car|home)$")):
    """What applies at a car (permit area, meter, winter ban) or a home (fire and
    evacuation zones), from the cities' extra layers. Each rule: {theme, label, rule,
    asof, city}."""
    region = region if region in REGIONS else region_for_point(lat, lon)
    return {"region": region, "rules": layers.rules_at(region, lat, lon, kind)}


def _car_spot(lat: float, lon: float) -> calfeed.CarSpot | None:
    loc = _locate(lat, lon, None)
    if loc.resolved is None:
        return None
    even, odd = analysis.schedules_for_all_matching_rows(loc.gdf_3857, loc.resolved)
    address, _ = _address(loc, lat, lon, network=False)
    return calfeed.CarSpot(loc.region, loc.city_key, address,
                           {"even": [list(e) for e in even], "odd": [list(o) for o in odd]},
                           loc.resolved.side)


@api.get("/calendar/{token}.ics", include_in_schema=False)
def calendar_feed(token: str):
    """An account's calendar subscription (the token from POST /api/me/calendar)."""
    prefs = auth.prefs_for_calendar_token(token)
    if prefs is None:
        raise HTTPException(404, "Unknown or revoked calendar link.")
    return Response(calfeed.build(prefs, _car_spot), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=900"})


class CarLocation(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    accuracy: float | None = Field(None, ge=0.0, le=1e6)  # metres, as the phone reports it


@api.post("/cars/location")
def car_location(req: CarLocation, who=Depends(auth.car_for_token)):
    """A phone reports where the car was parked (bearer token of that car).

    `summary` is short plain text for the phone to show as a notification.
    """
    if req.accuracy is not None and req.accuracy > resolve.CAR_MAX_DISTANCE_M:
        raise HTTPException(
            422, f"Position too rough (±{req.accuracy:.0f} m); the car was not moved.")
    if not auth.set_car_location(who["sub"], who["car_id"], req.lat, req.lon):
        raise HTTPException(404, "That car was removed from your account.")
    loc = _locate(req.lat, req.lon, None)
    address, _ = _address(loc, req.lat, req.lon, network=False)
    return {"summary": f"Parked at {address}."}


app.include_router(api)  # after every route above is registered on it


@app.get("/", include_in_schema=False)
def index():
    return web.index_response()


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return web.service_worker_response()


_frontend_dir = os.path.join(REPO_ROOT, "frontend")
app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
