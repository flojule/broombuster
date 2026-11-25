import functools
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from broombuster import (
    alerts,
    analysis,
    calfeed,
    config,
    feeds,
    gps,
    holidays,
    layers,
    resolve,
    trash,
)
from broombuster.cities import CITIES, REGIONS, city_for_point, region_bbox, region_for_point
from broombuster.config import REPO_ROOT
from broombuster.version import app_version

from . import auth, push, web
from .state import load_all, region_gdf

web.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_all()
    for rk in REGIONS:
        web.tiles_version(rk)  # hashed once, before the first request
    _cities_json()
    web.precompress_static()
    stops = []
    if auth.check_config():
        auth.init_db()
        push.public_key()  # made now if missing, not in a request
        stops.append(push.start(_car_spot))
    if config.live_feeds_enabled():
        stops.append(feeds.start_refresh())
    yield
    for stop in stops:
        stop.set()


app = FastAPI(
    title="BroomBuster", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)

app.add_middleware(web.RequestMiddleware)
# /api and /auth only: static files and built pages are served precompressed (web.py).
app.add_middleware(web.ApiGZip)
app.add_middleware(web.BodyLimit)  # outermost: refuses before anything reads the body
api = APIRouter(prefix="/api")  # everything the frontend fetches, except /auth and /health
app.include_router(auth.router)


class Located(NamedTuple):
    region: str
    gdf_3857: object
    resolved: resolve.ResolvedCar | None
    city_key: str


def _region(region: str | None, lat: float, lon: float) -> str:
    """The region asked for when it is one, else the nearest to the point."""
    return region if region in REGIONS else region_for_point(lat, lon)


def _city_name(city_key: str) -> str:
    """A city's short name: "Oakland" for "Oakland, CA"."""
    return CITIES[city_key]["name"].split(",")[0]


def _locate(lat: float, lon: float, region: str | None) -> Located:
    region = _region(region, lat, lon)
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
    city = _city_name(loc.city_key)
    if resolved.is_polygon:
        return f"Zone: {resolved.label}, {city}", False
    pending = not network and bool(resolved.street_name) and not gps.house_number_cached(lat, lon)
    hn = gps.maybe_house_number(lat, lon, resolved.street_name, network=network)
    return (f"{hn} " if hn else "") + f"{resolved.label}, {city}", pending


@app.get("/health")
async def health():  # async: answers even when every worker thread is busy
    return {"status": "ok", "version": app_version()}


@functools.cache
def _cities_json() -> bytes:
    """The /api/cities body: fixed for the life of the process (a deploy restarts it)."""
    return json.dumps(
        {
            rk: {
                "name": rv["name"],
                "center": rv["center"],
                "tz": rv["tz"],
                "zoom": rv["zoom"],
                "holidays": holidays.for_region(rk),
                "cities": {ck: _city_name(ck) for ck in rv["cities"]},
                "updated": {ck: CITIES[ck].get("source_updated") for ck in rv["cities"]},
                "layers": layers.catalog(rk),
                "tiles_version": web.tiles_version(rk),
            }
            for rk, rv in REGIONS.items()
        },
        default=str,
        separators=(",", ":"),
    ).encode()


@api.get("/cities")
async def cities(request: Request):
    return web.body_response(request, _cities_json(), "application/json")


@api.get("/locate")
def locate(request: Request):
    """Approximate visitor position from Cloudflare's visitor-location headers (the IP
    never leaves Cloudflare), and the nearest area; nulls without them."""
    h = request.headers
    try:
        pos = (float(h["cf-iplatitude"]), float(h["cf-iplongitude"]))
    except (KeyError, ValueError):
        return {"lat": None, "lon": None, "region": None}
    return {"lat": pos[0], "lon": pos[1], "region": region_for_point(*pos)}


# JSON numbers only: not true, "37.8", NaN or Infinity.
_STRICT = {"strict": True, "allow_inf_nan": False}


class CheckRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0, **_STRICT)
    lon: float = Field(..., ge=-180.0, le=180.0, **_STRICT)
    region: str | None = None


@api.post("/check")
def check(req: CheckRequest):
    """Sweeping schedule of the street (or zone) a car is parked on.

    Urgency is computed by the client from the raw schedules, so it follows
    the live clock. `covered` is false when no street or zone of the city's data
    is there: the empty schedules then mean "unknown", not "no sweeping".
    """
    loc = _locate(req.lat, req.lon, req.region)
    address, pending = _address(loc, req.lat, req.lon, network=False)
    even, odd = (
        analysis.schedules_for_all_matching_rows(loc.gdf_3857, loc.resolved)
        if loc.resolved
        else ([], [])
    )
    inside = loc.resolved is not None or resolve.in_region(loc.region, req.lat, req.lon)
    city = loc.city_key if inside else None
    days = holidays.for_city_iso(city) if city else None
    near = None
    if loc.resolved is None and inside and loc.gdf_3857 is not None:
        near = resolve.nearby_street(loc.gdf_3857, req.lat, req.lon)
    return {
        "region": loc.region,
        "city": city,
        # The region's clock and the city's no-sweep days, so the verdict never falls back
        # to the browser's zone when /api/cities failed.
        "tz": REGIONS[loc.region]["tz"],
        "holidays": days,
        "address": address,
        "address_pending": pending,
        "car_side": loc.resolved.side if loc.resolved else None,
        "side_labels": list(resolve.side_labels(loc.resolved)),
        "schedule_even": even,
        "schedule_odd": odd,
        "covered": loc.resolved is not None,
        "nearby_street": {"name": near[0], "m": round(near[1])} if near else None,
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
    region = _region(req.region, req.lat, req.lon)
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
    region = _region(region, lat, lon)
    if not resolve.in_region(region, lat, lon):
        return {"region": region, "rules": []}
    out = layers.rules_at(region, lat, lon, kind)
    if kind == "car":
        out += feeds.closures_near(region, lat, lon)
        if city_for_point(lat, lon, region) == "chicago_all":
            out += feeds.chicago_permit_zone(lat, lon, network=geocode)
    return {"region": region, "rules": out}


@api.get("/closures")
def closures(request: Request, region: str = Query(..., pattern="^[a-z_]+$")):
    """Street closures on now or starting within two weeks (GeoJSON), for the map; they
    change at most every feeds.REFRESH_S."""
    if region not in REGIONS:
        raise HTTPException(404, "Unknown region.")
    body = feeds.closures_json(region)
    return web.body_response(request, body, "application/json", "public, max-age=60")


@api.get("/alerts")
def place_alerts(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    region: str | None = None,
):
    """Weather alerts in effect at a place that change where to park (Red Flag, snow, …)."""
    return {"region": _region(region, lat, lon), "alerts": alerts.at(lat, lon)}


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
        resolve.side_labels(loc.resolved),
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


@api.get("/next-sweep.ics", include_in_schema=False)
def next_sweep(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    name: str = Query("Car", max_length=200),
    side: str | None = Query(None, pattern="^(even|odd)$"),
    car_id: str = Query("car", alias="id", pattern=r"^[\w-]{1,64}$"),
):
    """A car's next sweep as a calendar file with its reminders (no account needed)."""
    car = {"id": car_id, "name": name, "lat": lat, "lon": lon, "side": side}
    return Response(
        calfeed.build({"cars": [car], "homes": []}, _car_spot, first_only=True),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="sweeping.ics"',
        },
    )


# ── Notifications (api/push.py) ───────────────────────────────────────────────
_B64URL = r"^[A-Za-z0-9_-]+=*$"


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200, pattern=_B64URL)
    auth: str = Field(max_length=100, pattern=_B64URL)


class PushSubscription(BaseModel):
    """A browser's PushSubscription.toJSON() (expirationTime is ignored)."""

    endpoint: str = Field(max_length=1000)
    keys: PushKeys


class PushEndpoint(BaseModel):
    endpoint: str = Field(max_length=1000)


@api.get("/push/key")
def push_key():
    """The server's VAPID public key, for PushManager.subscribe; 404 when sign-in is off."""
    if not config.base_url():
        raise HTTPException(404, "Sign-in is not configured.")
    return {"key": push.public_key()}


@api.post("/push", status_code=204)
def push_subscribe(sub: PushSubscription, user=Depends(auth.signed_in_mutation)):
    """This device gets its person's reminders (auth.Notify); a first notice confirms it."""
    if not push.allowed_endpoint(sub.endpoint):
        raise HTTPException(422, "Unknown push service.")
    if not push.valid_keys(sub.keys.p256dh, sub.keys.auth):
        raise HTTPException(422, "Bad subscription keys.")
    auth.save_push_sub(user["id"], sub.endpoint, sub.keys.p256dh, sub.keys.auth)
    row = {"endpoint": sub.endpoint, "p256dh": sub.keys.p256dh, "auth": sub.keys.auth}
    push.deliver(
        row, {"title": "Notifications on", "body": "Reminders before sweeping and trash days"}, 600
    )


@api.delete("/push", status_code=204)
def push_unsubscribe(body: PushEndpoint, user=Depends(auth.signed_in_mutation)):
    auth.delete_push_sub(body.endpoint, user["id"])


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    detail = jsonable_encoder(exc.errors())
    for e in detail:  # NaN and Infinity are not JSON: echoed as text
        try:
            json.dumps(e.get("input"), allow_nan=False)
        except ValueError:
            e["input"] = str(e["input"])
    return JSONResponse({"detail": detail}, 422)


app.include_router(api)  # after every route above is registered on it


@app.get("/", include_in_schema=False)
async def index(request: Request):
    return web.index_response(request)


@app.get("/js/maplibre-global.js", include_in_schema=False)
async def maplibre_module(request: Request):
    return web.module_response(request)


@app.get("/sw.js", include_in_schema=False)
async def service_worker(request: Request):
    return web.service_worker_response(request)


_frontend_dir = os.path.join(REPO_ROOT, "frontend")
app.mount("/", web.Static(directory=_frontend_dir, html=True), name="frontend")
