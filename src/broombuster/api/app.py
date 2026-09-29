import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

import geopandas
import pandas as pd
import shapely.geometry as _shp_geom
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from broombuster import data_loader, gps, maps, resolve
from broombuster.cities import CITIES, REGIONS, city_for_point, in_bbox, region_for_point, region_of
from broombuster.domains import for_city as plugins_for_city

from . import db
from .auth import init_rate_limiting, rate_limit
from .auth import router as auth_router
from .deps import verify_jwt

_HERE = os.path.dirname(os.path.abspath(__file__))
# Repo root — three levels up: api/ → broombuster/ → src/ → repo/
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

_PRELOAD_REGION = os.environ.get("PRELOAD_REGION", "").strip()
_RESPONSE_SIZE_WARN_BYTES = 200_000

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("broombuster.api")

# ---------------------------------------------------------------------------
# City-level GDF cache — loaded in parallel background threads at startup.
# Each city gets its own threading.Event; a /check request waits only for
# the city (or cities) that overlap the user's location.
# ---------------------------------------------------------------------------

_city_gdfs: dict = {}        # city_key → GeoDataFrame (EPSG:4326)
_city_gdfs_3857: dict = {}   # city_key → GeoDataFrame (EPSG:3857)
_city_events: dict = {}      # city_key → threading.Event (set when done)
_region_combined: dict = {}  # region_key → (frozenset(loaded_keys), gdf_4326, gdf_3857)
_city_loaded_at: dict = {}   # city_key → float (time.time() when last loaded into memory)

# Protects the city GDF caches against torn reads during a hot-swap. Reads
# in /check that touch _city_gdfs and _city_gdfs_3857 must hold this lock so
# they never see one CRS updated and the other still on the previous version.
_swap_lock = threading.Lock()


def _load_city(city_key: str, force: bool = False) -> bool:
    """Load a city into the in-memory caches; return availability.

    force=True re-downloads (auto-download cities), hot-swaps the frames and
    rebuilds the region's tiles. Both CRS frames are built before `_swap_lock`
    is taken, then assigned under it, so a concurrent /check never sees mixed
    state. Always signals the city's event.
    """
    ev = _city_events.setdefault(city_key, threading.Event())
    if not force and city_key in _city_gdfs:
        ev.set()
        return True
    region = region_of(city_key)
    try:
        if force:
            logger.info("[freshness] refreshing %s", CITIES[city_key]["name"])
        gdf = data_loader.load_city_data(city_key, force_refresh=force).copy()
        gdf["_city"] = city_key
        new_4326 = gdf.to_crs("EPSG:4326")
        new_3857 = gdf.to_crs("EPSG:3857")
    except Exception:  # noqa: BLE001 — any failure must release waiters
        logger.exception("could not load city '%s'", city_key)
        ev.set()
        return False
    with _swap_lock:
        _city_gdfs[city_key] = new_4326
        _city_gdfs_3857[city_key] = new_3857
        _city_loaded_at[city_key] = time.time()
        _region_combined.pop(region, None)
    ev.set()
    if force:
        logger.info("[freshness] %s refreshed", CITIES[city_key]["name"])
        _rebuild_region_tiles([region])
    return True


def _rebuild_region_tiles(region_keys) -> None:
    """Kick off a detached PMTiles rebuild per region (PMTILES mode only)."""
    if not _PMTILES_MODE or not region_keys:
        return
    if not shutil.which("tippecanoe"):
        logger.warning("[tiles] tippecanoe not on PATH; skipping tile rebuild")
        return
    script = os.path.join(_REPO_ROOT, "scripts", "build_pmtiles.py")
    for rk in region_keys:
        try:
            subprocess.Popen(
                [sys.executable, script, "--region", rk, "--force"],
                cwd=_REPO_ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.info("[tiles] rebuild started for region %s", rk)
        except OSError as exc:
            logger.warning("[tiles] could not start rebuild for %s: %s", rk, exc)


def _freshness_checker_bg() -> None:
    """
    Background thread: after all cities have loaded, periodically check whether
    auto-downloadable cities have stale data files and refresh them.

    Runs lazily — waits for initial loading to complete, then checks every hour.
    Hot-swaps the in-memory GDF without restarting the server.
    """
    # Wait until every city has finished loading (or failed), up to 10 min.
    deadline = time.time() + 600
    while time.time() < deadline:
        if all(ev.is_set() for ev in _city_events.values()):
            break
        time.sleep(5)

    # Give the server a moment to start serving traffic before any re-download.
    time.sleep(60)

    while True:
        for city_key, city in CITIES.items():
            url             = city.get("url")
            stale_after_days = city.get("stale_after_days")
            if not url or not stale_after_days:
                continue

            # Prefer the FGB mtime (reflects last normalisation); fall back to raw.
            check_rel = city.get("fgb_path") or city["local_path"]
            local_path = os.path.join(_REPO_ROOT, check_rel)
            if os.path.exists(local_path):
                age_days = (time.time() - os.path.getmtime(local_path)) / 86400
                if age_days < stale_after_days:
                    continue
                logger.info("[freshness] %s data is %.0f days old (threshold %sd)",
                            city["name"], age_days, stale_after_days)
            # File missing or stale — refresh.
            _load_city(city_key, force=True)

        time.sleep(3600)  # re-check every hour (only downloads when actually stale)


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
        logger.info("[preload] region '%s' ready", _PRELOAD_REGION)
    # Background freshness checker — runs after startup, checks hourly.
    threading.Thread(target=_freshness_checker_bg, daemon=True).start()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="BroomBuster API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress responses over 1 KB. The /check GeoJSON is verbose text and gzips
# ~5-8x; this is the interim win for SF before PMTiles removes the payload.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(auth_router)
init_rate_limiting(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip_with_sindex(gdf, clip_geom):
    """Clip a GeoDataFrame to features intersecting `clip_geom` via the
    spatial index. Falls back to the linear .intersects() scan if the
    sindex query is unavailable (older shapely or empty index).

    Indices are sorted to preserve the original GeoDataFrame row order — the
    GeoJSON builder's segment dedup relies on insertion order, and several
    tests assert behavior that depends on that order matching `.intersects()`.
    """
    try:
        import numpy as _np
        idx = gdf.sindex.query(clip_geom, predicate="intersects")
    except (AttributeError, TypeError, ValueError, ImportError):
        return gdf[gdf.geometry.intersects(clip_geom)]
    if len(idx) == 0:
        return gdf.iloc[0:0]
    return gdf.iloc[_np.sort(idx)]


def _priority_cities(lat: float, lon: float, region_key: str) -> list:
    """Cities whose bbox contains (lat, lon) first; rest after."""
    city_keys = REGIONS[region_key]["cities"]
    priority = [ck for ck in city_keys if in_bbox(ck, lat, lon)]
    rest     = [ck for ck in city_keys if ck not in priority]
    return priority + rest


def _get_region_gdfs(lat: float, lon: float, region_key: str):
    """
    Wait for the priority city (the one whose bbox contains lat/lon) to load,
    then return combined GDFs from all cities that are already in cache.
    The combined GDF is cached until the set of loaded cities changes, so the
    analysis.py name-index cache (keyed by id(gdf)) is reused across requests.
    """
    ordered = _priority_cities(lat, lon, region_key)

    # Wait for at least the first priority city (up to 120 s).
    for ck in ordered:
        ev = _city_events.get(ck)
        if ev:
            ev.wait(timeout=120)
        if ck in _city_gdfs:
            break  # have data for the user's city; good enough to proceed

    # Hold _swap_lock for the entire snapshot + combine + cache step so a
    # concurrent _load_city(force=True) cannot replace a city's GDF in the middle of
    # building the combined frame. Hot swaps are hourly and the concat is
    # only a few ms even for the full Bay Area, so contention is negligible.
    with _swap_lock:
        loaded = frozenset(ck for ck in REGIONS[region_key]["cities"] if ck in _city_gdfs)
        if not loaded:
            return None, None

        cached = _region_combined.get(region_key)
        if cached and cached[0] == loaded:
            return cached[1], cached[2]

        city_keys = [ck for ck in REGIONS[region_key]["cities"] if ck in loaded]
        c4 = geopandas.GeoDataFrame(
            pd.concat([_city_gdfs[ck] for ck in city_keys], ignore_index=True), crs="EPSG:4326")
        c3 = geopandas.GeoDataFrame(
            pd.concat([_city_gdfs_3857[ck] for ck in city_keys], ignore_index=True),
            crs="EPSG:3857")
        _region_combined[region_key] = (loaded, c4, c3)
        return c4, c3


def _resolve_region(req):
    """Return (region_key, local_now) for a request (explicit region or auto)."""
    region = req.region if req.region in REGIONS else region_for_point(req.lat, req.lon)
    local_now = datetime.now(ZoneInfo(REGIONS[region]["tz"]))
    return region, local_now


def _build_address(resolved, city_key: str, lat: float, lon: float) -> str:
    """Canonical address string from the resolved segment.

    Polygon zones → "Zone: <name>, <city>". Line segments → optional
    Nominatim house number (gated to the resolved street) + display name.
    Falls back to raw lat/lon when nothing resolves.
    """
    if resolved is None:
        return f"{lat:.4f}, {lon:.4f}"
    display = resolved.street_display or resolved.street_name
    city_short = CITIES[city_key]["name"].split(",")[0]
    if resolved.is_polygon:
        return f"Zone: {display}, {city_short}" if display else f"{lat:.4f}, {lon:.4f}"
    hn = gps.maybe_house_number(lat, lon, resolved.street_name)
    if hn:
        return f"{hn} {display}, {city_short}"
    if display:
        return f"{display}, {city_short}"
    return f"{lat:.4f}, {lon:.4f}"


def _tiles_to_geom(tiles):
    """Union of XYZ tile boxes ('z/x/y' strings) → clip geometry, or None."""
    import math
    try:
        from shapely.ops import unary_union
    except ImportError:
        unary_union = None

    def _tile_lat(yy: int, zz: int) -> float:
        n2 = 2 ** zz
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n2))))

    boxes = []
    for t in tiles:
        if not isinstance(t, str):
            continue
        parts = t.split('/')
        if len(parts) != 3:
            continue
        try:
            z, x, y = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        n = 2 ** z
        lon_min = x / n * 360.0 - 180.0
        lon_max = (x + 1) / n * 360.0 - 180.0
        boxes.append(_shp_geom.box(lon_min, _tile_lat(y + 1, z), lon_max, _tile_lat(y, z)))

    if not boxes:
        return None
    if unary_union:
        return unary_union(boxes)
    minx = min(b.bounds[0] for b in boxes)
    miny = min(b.bounds[1] for b in boxes)
    maxx = max(b.bounds[2] for b in boxes)
    maxy = max(b.bounds[3] for b in boxes)
    return _shp_geom.box(minx, miny, maxx, maxy)


def _clip_region_for_request(req, gdf_4326):
    """Clip the region GDF to the requested view; return (gdf, simplify_tol).

    Priority: tiles → union of tile boxes; full_region → whole region (no
    simplify, so it stays a superset of later bbox requests); bbox → explicit
    box; otherwise → ~1.5 km radius around the car. The simplify tolerance is
    sub-pixel for a ~1000 px viewport (span / 2000).
    """
    bbox_span_deg = 0.03  # default radius mode below: ±0.015°
    if req.tiles and isinstance(req.tiles, list) and not req.full_region:
        clip_geom = _tiles_to_geom(req.tiles)
        if clip_geom is not None:
            gdf_display = _clip_with_sindex(gdf_4326, clip_geom)
            minx, miny, maxx, maxy = clip_geom.bounds
        else:
            gdf_display = gdf_4326
            minx, miny, maxx, maxy = gdf_4326.total_bounds
        bbox_span_deg = max(maxx - minx, maxy - miny)
    elif req.full_region:
        gdf_display = gdf_4326
        bbox_span_deg = 0.0  # skip simplify so full-region stays a superset
    elif req.bbox and isinstance(req.bbox, list) and len(req.bbox) == 4:
        min_lat, min_lon, max_lat, max_lon = req.bbox
        _clip = _shp_geom.box(min_lon, min_lat, max_lon, max_lat)
        gdf_display = _clip_with_sindex(gdf_4326, _clip)
        bbox_span_deg = max(max_lon - min_lon, max_lat - min_lat)
    else:
        _CLIP_DEG = 0.015  # ≈ 1.5 km
        _clip = _shp_geom.box(
            req.lon - _CLIP_DEG, req.lat - _CLIP_DEG,
            req.lon + _CLIP_DEG, req.lat + _CLIP_DEG,
        )
        gdf_display = _clip_with_sindex(gdf_4326, _clip)
        bbox_span_deg = 2 * _CLIP_DEG

    simplify_tolerance = bbox_span_deg / 2000.0 if bbox_span_deg > 0 else None
    return gdf_display, simplify_tolerance


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
        check_rel = city.get("fgb_path") or city["local_path"]
        local_path = os.path.join(_REPO_ROOT, check_rel)
        if os.path.exists(local_path):
            age_days = (time.time() - os.path.getmtime(local_path)) / 86400
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
    region: Optional[str] = None
    full_region: Optional[bool] = False
    # bbox as [min_lat, min_lon, max_lat, max_lon] — exactly four entries
    bbox: Optional[List[float]] = Field(None, min_length=4, max_length=4)
    # Up to 64 tiles per request; each entry validated as "z/x/y" at use site
    tiles: Optional[List[str]] = Field(None, max_length=64)


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
    snap: Optional[dict] = None
    detail_html: str = ""
    domain_results: list[dict] = []
    city_key = city_for_point(req.lat, req.lon, region)

    if not is_tile_only:
        # SINGLE SOURCE OF TRUTH — one authoritative segment drives street
        # name, side, schedule, urgency, map highlight and the city itself.
        resolved, city_key = resolve.locate(myCity_3857, req.lat, req.lon, region)
        address = _build_address(resolved, city_key, req.lat, req.lon)
        if resolved is not None:
            snap = {
                "street_name": resolved.street_display or resolved.street_name,
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
                    resolved.street_display or resolved.street_name,
                    sweep_extras["schedule_even"], sweep_extras["schedule_odd"],
                    city_key, local_now, sweep_extras["car_side"],
                    tuple(sweep_extras["side_labels"]),
                )

    # In PMTILES mode the map renders from static vector tiles, so /check skips
    # the per-request clip + GeoJSON build entirely and returns no `geojson`.
    if _PMTILES_MODE:
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
        "detail_html": detail_html,
        "geojson": geojson,
        # Which segment the resolver chose and how far the car is from it.
        "snap": snap,
        "domains": domain_results,
    }


class CheckHomeRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: Optional[str] = None
    # Postal address for address-keyed lookups (e.g. ReCollect trash).
    address: Optional[str] = None


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


# ---------------------------------------------------------------------------
# Routes — authenticated
# ---------------------------------------------------------------------------


class PrefsRequest(BaseModel):
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    home_address: Optional[str] = None
    homes: Optional[list] = []
    preferred_region: Optional[str] = "bay_area"
    notify_email: Optional[bool] = False
    cars: Optional[list] = []


@app.get("/prefs")
def get_prefs(user_id: str = Depends(verify_jwt)):
    return db.get_prefs(user_id)


@app.post("/prefs")
def save_prefs(req: PrefsRequest, user_id: str = Depends(verify_jwt)):
    db.save_prefs(user_id, {
        "home_lat":          req.home_lat,
        "home_lon":          req.home_lon,
        "home_address":      req.home_address,
        "homes":             req.homes,
        "preferred_region":  req.preferred_region,
        "notify_email":      req.notify_email,
        "cars":              req.cars,
    })
    return {"saved": True}


# ---------------------------------------------------------------------------
# Runtime config endpoint — injected into the frontend as window globals
# ---------------------------------------------------------------------------

_DEV_MODE_API = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")
# PMTILES_MODE (default ON): render the map from static vector tiles
# (frontend/tiles/*.pmtiles) and slim /check to resolver fields. Set
# PMTILES_MODE=0 to fall back to the legacy server-built GeoJSON path.
_PMTILES_MODE = os.environ.get("PMTILES_MODE", "1").lower() in ("1", "true", "yes")
# Whether the frontend should show the "Create account" button. Mirrors the
# server-side ALLOW_REGISTRATION gate in auth.py so a disabled signup hides the
# button rather than letting it 403 on click.
_ALLOW_REGISTRATION_API = os.environ.get("ALLOW_REGISTRATION", "true").lower() in (
    "1", "true", "yes"
)


@app.get("/config.js", include_in_schema=False)
def config_js():
    """Serve runtime config as a JS snippet so the frontend knows runtime flags."""
    js = (
        f"window.DEV_MODE = {'true' if _DEV_MODE_API else 'false'};\n"
        f"window.PMTILES_MODE = {'true' if _PMTILES_MODE else 'false'};\n"
        f"window.ALLOW_REGISTRATION = {'true' if _ALLOW_REGISTRATION_API else 'false'};\n"
        "window.REGION_TZ = " + json.dumps(
            {rk: rv.get("tz", "UTC") for rk, rv in REGIONS.items()}
        ) + ";\n"
    )
    return Response(content=js, media_type="application/javascript")


@app.get("/zone/detail", include_in_schema=False)
@rate_limit(_CHECK_RATE)
def zone_detail(request: Request, code: List[str] = Query(default=[]), street: str = "",
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

_frontend_dir = os.path.join(_REPO_ROOT, "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
