import ipaddress
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, NamedTuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from broombuster import (
    alerts,
    analysis,
    around,
    calfeed,
    feeds,
    gps,
    holidays,
    iplocate,
    layers,
    resolve,
    trash,
)
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


app = FastAPI(
    title="BroomBuster", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)

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
    pending = not network and bool(resolved.street_name) and not gps.house_number_cached(lat, lon)
    hn = gps.maybe_house_number(lat, lon, resolved.street_name, network=network)
    return (f"{hn} " if hn else "") + f"{resolved.label}, {city}", pending


@app.get("/health")
def health():
    return {"status": "ok", "version": app_version()}


@api.get("/cities")
def cities():
    return {
        rk: {
            "name": rv["name"],
            "center": rv["center"],
            "tz": rv["tz"],
            "zoom": rv["zoom"],
            "holidays": holidays.for_region(rk),
            "cities": {ck: CITIES[ck]["name"].split(",")[0] for ck in rv["cities"]},
            "layers": layers.catalogue(rk),
        }
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
    even, odd = (
        analysis.schedules_for_all_matching_rows(loc.gdf_3857, loc.resolved)
        if loc.resolved
        else ([], [])
    )
    return {
        "region": loc.region,
        "city": loc.city_key,
        "address": address,
        "address_pending": pending,
        "car_side": loc.resolved.side if loc.resolved else None,
        "side_labels": list(
            analysis.side_labels(loc.resolved.segment)
            if loc.resolved
            else analysis.DEFAULT_SIDE_LABELS
        ),
        "schedule_even": even,
        "schedule_odd": odd,
    }


@api.get("/address")
def address(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    region: str | None = None,
):
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
    return {
        "region": region,
        "city": city,
        "address": address,
        "trash_kind": trash.kind(city),
        "pickups": trash.pickups(city, address, today, req.collection_day),
    }


@api.get("/rules")
def rules(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    region: str | None = None,
    kind: str = Query("car", pattern="^(car|home)$"),
    geocode: bool = True,
):
    """What applies at a car (permit area, meter, winter ban) or a home (fire and
    evacuation zones), from the cities' extra layers. Each rule: {theme, label, rule,
    asof, city}. geocode=false (the map's street card, asked at every stop): no new
    address lookups, so Chicago's permit zone only when the address is cached."""
    region = region if region in REGIONS else region_for_point(lat, lon)
    out = layers.rules_at(region, lat, lon, kind)
    if kind == "car":
        out += feeds.closures_near(region, lat, lon)
        if city_for_point(lat, lon, region) == "chicago_all":
            out += feeds.chicago_permit_zone(lat, lon, network=geocode)
    return {"region": region, "rules": out}


@api.get("/closures")
def closures(region: str = Query(..., pattern="^[a-z_]+$")):
    """Street closures on now or starting within two weeks (GeoJSON), for the map."""
    if region not in REGIONS:
        raise HTTPException(404, "Unknown region.")
    return {"type": "FeatureCollection", "features": feeds.closures(region)}


@api.get("/around")
def nearby(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    region: str | None = None,
):
    """Markets, events, street events and meetings near a point this week (Nearby tab)."""
    region = region if region in REGIONS else region_for_point(lat, lon)
    return {"region": region, **around.around(region, lat, lon)}


@api.get("/alerts")
def place_alerts(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    region: str | None = None,
):
    """Weather and air alerts in effect at a place (Red Flag, snow, Spare the Air, …)."""
    region = region if region in REGIONS else region_for_point(lat, lon)
    return {"region": region, "alerts": alerts.at(region, lat, lon)}


@api.get("/vehicle")
def vehicle(
    plate: str = Query(..., min_length=1, max_length=10, pattern=r"^[A-Za-z0-9 -]+$"),
    state: str = Query("IL", pattern="^[A-Za-z]{2}$"),
):
    """Chicago: was this plate towed or relocated (last 90 days)? 503 when the city's
    portal does not answer."""
    got = feeds.chicago_vehicle(plate, state)
    if got is None:
        raise HTTPException(503, "The City of Chicago's data portal did not answer.")
    return got


def _car_spot(lat: float, lon: float) -> calfeed.CarSpot | None:
    loc = _locate(lat, lon, None)
    if loc.resolved is None:
        return None
    even, odd = analysis.schedules_for_all_matching_rows(loc.gdf_3857, loc.resolved)
    address, _ = _address(loc, lat, lon, network=False)
    return calfeed.CarSpot(
        loc.region,
        loc.city_key,
        address,
        {"even": [list(e) for e in even], "odd": [list(o) for o in odd]},
        loc.resolved.side,
    )


@api.get("/calendar/{token}.ics", include_in_schema=False)
def calendar_feed(token: str):
    """An account's calendar subscription (the token from POST /api/me/calendar)."""
    prefs = auth.prefs_for_calendar_token(token)
    if prefs is None:
        raise HTTPException(404, "Unknown or revoked calendar link.")
    return Response(
        calfeed.build(prefs, _car_spot),
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=900"},
    )


# Phone automations send numbers as text, sometimes with a unit ("65 m", "213 ft",
# Shortcuts' measurements) or a decimal comma ("37,8044"). A comma before exactly three
# digits is a thousands separator ("1,234 ft"), never read as a decimal.
_NUMBER = re.compile(r"[±~]?\s*([-+]?\d+(?:[.,]\d+)?)\s*([a-z°]*)\.?", re.I)
_METRES = {"": 1.0, "m": 1.0, "km": 1000.0, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344}
_DEGREES = {"": 1.0, "°": 1.0}


def _number(units: dict):
    def parse(v):
        if not isinstance(v, str):
            return v
        v = re.sub(r",(\d{3})(?!\d)", r"\1", v.strip().replace("\u00a0", " "))
        m = _NUMBER.fullmatch(v)
        if not m or m[2].lower() not in units:
            return v  # pydantic refuses it
        return float(m[1].replace(",", ".")) * units[m[2].lower()]

    return parse


class CarLocation(BaseModel):
    lat: Annotated[float, BeforeValidator(_number(_DEGREES)), Field(ge=-90.0, le=90.0)]
    lon: Annotated[float, BeforeValidator(_number(_DEGREES)), Field(ge=-180.0, le=180.0)]
    # Metres (or with a unit), as the phone reports it.
    accuracy: Annotated[float | None, BeforeValidator(_number(_METRES)), Field(ge=0.0, le=1e6)] = (
        None
    )


_PHONE_PATH = "/api/cars/location"


async def _phone_refusal(
    request: Request, status: int, reason: str, headers: dict | None = None
) -> JSONResponse:
    """A refused phone report: the reason is also its `summary`, so the phone's
    notification says why; it is kept on the token for the car's page, and logged."""
    if token_hash := getattr(request.state, "car_token", None):
        await run_in_threadpool(auth.record_refusal, token_hash, reason)
    request.state.log_reason = reason
    return JSONResponse({"detail": reason, "summary": reason}, status, headers=headers)


def _unreadable(errors: list[dict]) -> str:
    """Why a phone's body was not understood, e.g. "Could not read accuracy ('65 metres')"."""
    parts = []
    for e in errors:
        loc = [str(p) for p in e.get("loc", ()) if p != "body"]
        if not loc or e["type"] in ("json_invalid", "model_attributes_type", "dict_type"):
            return "Could not read the request: send a JSON body with lat and lon."
        if e["type"] == "missing":
            parts.append(f"{loc[0]} missing")
        elif loc[0] == "accuracy":  # not private, and the most useful to see
            parts.append(f"accuracy ({str(e.get('input'))[:24]!r})")
        else:
            parts.append(loc[0])
    return f"Could not read {', '.join(parts)}; the car was not moved."


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path == _PHONE_PATH and isinstance(exc.detail, str):
        return await _phone_refusal(request, exc.status_code, exc.detail, exc.headers)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == _PHONE_PATH:
        return await _phone_refusal(request, 422, _unreadable(exc.errors()))
    return await request_validation_exception_handler(request, exc)


@api.post("/cars/location")
def car_location(req: CarLocation, who=Depends(auth.car_for_token)):
    """A phone reports where the car was parked (bearer token of that car).

    `summary` is short plain text for the phone to show as a notification.
    """
    if req.accuracy is not None and req.accuracy > resolve.CAR_MAX_DISTANCE_M:
        raise HTTPException(
            422, f"Position too rough (±{req.accuracy:.0f} m); the car was not moved."
        )
    if not auth.set_car_location(who["account_id"], who["car_id"], req.lat, req.lon):
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
