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
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from broombuster import car as car_module
from broombuster import data_loader, gps, maps, resolve
from broombuster.cities import CITIES, REGIONS
from broombuster.domains import for_city as plugins_for_city

from . import db
from .auth import init_rate_limiting
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


def _ensure_city_loaded(city_key: str) -> bool:
    """Load a city into the in-memory caches if absent; return availability.

    Thread-safe: both CRS frames are built before `_swap_lock` is taken, then
    assigned under it so a concurrent /check or hot-swap never sees mixed
    state. Used by the startup background loader and the /check full_region
    sync path so both share one locking discipline.
    """
    if city_key in _city_gdfs:
        return True
    try:
        gdf = data_loader.load_city_data(city_key)
        gdf = gdf.copy()
        gdf["_city"] = city_key
        new_4326 = gdf.to_crs("EPSG:4326")
        new_3857 = gdf.to_crs("EPSG:3857")
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("could not load city '%s': %s", city_key, exc)
        return False
    with _swap_lock:
        _city_gdfs[city_key] = new_4326
        _city_gdfs_3857[city_key] = new_3857
        _city_loaded_at[city_key] = time.time()
        for rk, rv in REGIONS.items():
            if city_key in rv["cities"]:
                _region_combined.pop(rk, None)
    ev = _city_events.get(city_key)
    if ev is None:
        ev = _city_events[city_key] = threading.Event()
    ev.set()
    return True


def _load_city_bg(city_key: str) -> None:
    """Background-thread city load; always signals completion via the event."""
    try:
        _ensure_city_loaded(city_key)
    finally:
        _city_events[city_key].set()


def _hot_swap_city(city_key: str) -> None:
    """Re-download and atomically replace a city's in-memory GDFs."""
    city = CITIES[city_key]
    logger.info("[freshness] refreshing %s", city["name"])
    try:
        gdf = data_loader.load_city_data(city_key, force_refresh=True)
        gdf = gdf.copy()
        gdf["_city"] = city_key
        # Build both projections BEFORE taking the lock, then swap them in
        # under the lock so a concurrent /check never sees mixed CRS versions.
        new_4326 = gdf.to_crs("EPSG:4326")
        new_3857 = gdf.to_crs("EPSG:3857")
        affected_regions = []
        with _swap_lock:
            _city_gdfs[city_key]      = new_4326
            _city_gdfs_3857[city_key] = new_3857
            _city_loaded_at[city_key] = time.time()
            # Invalidate the region combined-GDF cache so the next request rebuilds it.
            for rk, rv in REGIONS.items():
                if city_key in rv["cities"]:
                    _region_combined.pop(rk, None)
                    affected_regions.append(rk)
        logger.info("[freshness] %s refreshed successfully", city["name"])
        # Refreshed data must flow into the static tiles (PMTILES mode only).
        _rebuild_region_tiles(affected_regions)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("[freshness] could not refresh '%s': %s", city_key, exc)


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
                print(
                    f"[freshness] {city['name']} data is {age_days:.0f} days old "
                    f"(threshold {stale_after_days}d) — refreshing…",
                    flush=True,
                )
            # File missing or stale — refresh.
            _hot_swap_city(city_key)

        time.sleep(3600)  # re-check every hour (only downloads when actually stale)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Kick off all city loads in parallel — server is immediately ready.
    for rv in REGIONS.values():
        for ck in rv["cities"]:
            _city_events[ck] = threading.Event()
            threading.Thread(target=_load_city_bg, args=(ck,), daemon=True).start()
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


def _in_city_bbox(lat: float, lon: float, city_key: str) -> bool:
    bbox = CITIES[city_key].get("bbox")
    if not bbox:
        return False
    lat_min, lon_min, lat_max, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _priority_cities(lat: float, lon: float, region_key: str) -> list:
    """Cities whose bbox contains (lat, lon) first; rest after."""
    city_keys = REGIONS[region_key]["cities"]
    priority = [ck for ck in city_keys if _in_city_bbox(lat, lon, ck)]
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
    # concurrent _hot_swap_city cannot replace a city's GDF in the middle of
    # building the combined frame. Hot swaps are hourly and the concat is
    # only a few ms even for the full Bay Area, so contention is negligible.
    with _swap_lock:
        loaded = frozenset(ck for ck in REGIONS[region_key]["cities"] if ck in _city_gdfs)
        if not loaded:
            return None, None

        cached = _region_combined.get(region_key)
        if cached and cached[0] == loaded:
            return cached[1], cached[2]

        city_keys = [ck for ck in REGIONS[region_key]["cities"] if ck in _city_gdfs]
        gdfs_4326 = [_city_gdfs[ck] for ck in city_keys]

        # Prefer cached 3857 frames; fall back to on-the-fly conversion of
        # the 4326 copy if a city only has the 4326 entry populated.
        gdfs_3857 = []
        for ck, gdf4326 in zip(city_keys, gdfs_4326):
            gdf3857 = _city_gdfs_3857.get(ck)
            if gdf3857 is None:
                gdf3857 = gdf4326.to_crs("EPSG:3857")
            gdfs_3857.append(gdf3857)

        c4 = geopandas.GeoDataFrame(pd.concat(gdfs_4326, ignore_index=True), crs="EPSG:4326")
        c3 = geopandas.GeoDataFrame(pd.concat(gdfs_3857, ignore_index=True), crs="EPSG:3857")
        _region_combined[region_key] = (loaded, c4, c3)
        return c4, c3


def _nearest_city_key(lat: float, lon: float, region_key: str) -> str:
    city_keys = REGIONS[region_key]["cities"]
    best, best_d = city_keys[0], float("inf")
    for ck in city_keys:
        c = CITIES[ck]["center"]
        d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
        if d < best_d:
            best, best_d = ck, d
    return best


def _auto_region(lat: float, lon: float) -> str:
    """Pick the region whose center is closest to (lat, lon)."""
    best, best_d = "bay_area", float("inf")
    for rk, rv in REGIONS.items():
        c = rv["center"]
        d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
        if d < best_d:
            best, best_d = rk, d
    return best


def _resolve_region(req):
    """Return (region_key, local_now) for a request (explicit region or auto)."""
    region = req.region if req.region in REGIONS else _auto_region(req.lat, req.lon)
    local_now = datetime.now(ZoneInfo(REGIONS[region].get("tz", "UTC")))
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
# Routes — authenticated
# ---------------------------------------------------------------------------


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
def check(req: CheckRequest):
    region, local_now = _resolve_region(req)

    # If client explicitly requested the full region, synchronously load
    # any missing city data first so the combined region GDF is complete.
    if req.full_region:
        for ck in REGIONS[region]["cities"]:
            _ensure_city_loaded(ck)

    myCity_4326, myCity_3857 = _get_region_gdfs(req.lat, req.lon, region)
    if myCity_4326 is None:
        raise HTTPException(
            status_code=503,
            detail=f"No data available for region '{region}' yet — try again shortly.",
        )

    city_key = _nearest_city_key(req.lat, req.lon, region)
    myCar = car_module.Car(lat=req.lat, lon=req.lon)
    myCar._city = city_key

    # Tile-only requests (map background rendering) don't need geocoding or
    # street-sweeping analysis — skipping them cuts response time by ~2-3 s.
    is_tile_only = bool(req.tiles) and not req.full_region

    # Default response values for the tile-only path
    schedule_even: list = []
    schedule_odd:  list = []
    message:  str = ""
    urgency: object = False
    car_side: str = "odd"
    address:  str = ""
    snap: Optional[dict] = None
    detail_html: str = ""

    # Per-domain results, populated below. The /check response carries
    # `domains[]` for forward compatibility (Step 3+) AND the legacy
    # top-level fields so the existing frontend keeps working.
    domain_results: list[dict] = []

    if not is_tile_only:
        # SINGLE SOURCE OF TRUTH — resolve the car to one authoritative segment.
        # Every downstream field (street name, side, schedule, urgency, map
        # highlight) is derived from this one resolved row. This is the fix
        # for the cross-field inconsistency bug where Nominatim, the spatial
        # join, and the address-parity could all disagree silently.
        try:
            resolved = resolve.resolve_car_segment(
                myCity_3857, req.lat, req.lon,
                city_key=city_key, max_distance_m=50.0,
            )
        except resolve.NoSegmentNearby:
            resolved = None

        if resolved is not None:
            myCar.street_name = resolved.street_name
            display = resolved.street_display or resolved.street_name
            snap = {
                "street_name": display,
                "distance_m":  round(resolved.distance_m, 1),
                "is_polygon":  resolved.is_polygon,
            }

            # Canonical address derived from the resolved segment (see
            # _build_address; the house-number gate prevents Nominatim bleed).
            address = _build_address(resolved, city_key, req.lat, req.lon)
        else:
            # Car is not near any mapped street — be explicit rather than
            # silently guessing.
            address = f"{req.lat:.4f}, {req.lon:.4f}"
            message = "Car not near a mapped street."

        # Run every plugin that supports this city. Each plugin gets the
        # resolved segment (or None) and produces its own DomainResult.
        # The sweeping plugin's output is also mirrored into the legacy
        # top-level response fields below.
        for plugin in plugins_for_city(city_key):
            # /check is the car flow — only car-subject domains (sweeping).
            # Home-subject domains (trash) are served by /check-home.
            if getattr(plugin, "subject", "car") != "car":
                continue
            # Plugins may re-resolve with their own parameters in the future;
            # for now sweeping shares the same resolved segment we already
            # computed. Calling resolve_for ensures plugins that DO need a
            # different shape can produce one without breaking the contract.
            plugin_resolved = (
                resolved
                if plugin.domain_id == "sweeping"
                else plugin.resolve_for(myCity_3857, req.lat, req.lon, city_key)
            )
            result = plugin.format(plugin_resolved, myCity_3857, local_now)
            domain_results.append({
                "id":             result.domain_id,
                "label":          result.label,
                "urgency":        result.urgency,
                "schedule_lines": list(result.schedule_lines),
                "extras":         dict(result.extras),
            })

            # Mirror the sweeping plugin's output back into the legacy
            # top-level fields so the existing frontend doesn't notice
            # anything changed.
            if plugin.domain_id == "sweeping":
                schedule_even = result.extras.get("schedule_even", []) or []
                schedule_odd  = result.extras.get("schedule_odd", []) or []
                car_side      = result.extras.get("car_side") or "odd"
                # Legacy `urgency` field uses False (not "safe") for the
                # no-urgency state — preserve that to keep the wire format
                # byte-identical to pre-Step-3.
                urgency       = result.urgency if result.urgency in ("today", "tomorrow") else False
                if resolved is not None:
                    message = result.extras.get("message") or ""
                    # Full-year detail HTML for the resolved segment so the car
                    # card can open the same window a street/ward click shows.
                    # Same row shape the /zone/detail route builds; picks the
                    # car-side's first entry (code, desc, time), else the other.
                    primary = schedule_even if car_side == "even" else schedule_odd
                    other   = schedule_odd  if car_side == "even" else schedule_even
                    entry = (primary[0] if primary else (other[0] if other else None))
                    if entry:
                        detail_html = maps._zone_detail(
                            {
                                "STREET_DISPLAY": resolved.street_display or resolved.street_name,
                                "STREET_NAME":    resolved.street_name,
                                "DAY_EVEN":       entry[0],
                                "DESC_EVEN":      entry[1] if len(entry) > 1 else "",
                                "_city":          city_key,
                            },
                            local_now,
                        )

    # In PMTILES mode the map renders from static vector tiles, so /check skips
    # the per-request clip + GeoJSON build entirely and returns no `geojson`.
    if _PMTILES_MODE:
        geojson = None
    else:
        # Clip the region to the requested view (tiles / full_region / bbox /
        # radius) and derive the sub-pixel simplify tolerance. See
        # _clip_region_for_request.
        myCity_display, simplify_tolerance = _clip_region_for_request(req, myCity_4326)

        geojson = maps.build_map_geojson(
            myCar, myCity_display,
            schedule_even=schedule_even,
            schedule_odd=schedule_odd,
            message=message,
            local_now=local_now,
            simplify_tolerance=simplify_tolerance,
        )

        geojson_size = len(json.dumps(geojson).encode())
        if geojson_size > _RESPONSE_SIZE_WARN_BYTES:
            logger.warning(
                "/check geojson exceeds 200 KB (%d B) — region=%s lat=%.4f lon=%.4f clip=%s",
                geojson_size, region, req.lat, req.lon,
                "tiles" if req.tiles else ("full" if req.full_region else "radius"),
            )

    return {
        "message": message,
        "urgency": urgency,
        "schedule_even": schedule_even,
        "schedule_odd": schedule_odd,
        "car_side": car_side,
        "address": address,
        "detail_html": detail_html,
        "geojson": geojson,
        # `snap` tells the frontend which segment the resolver chose and how
        # far the car is from it — powers the "snapped to 5th St — 12 m"
        # indicator and ensures the map highlight matches the alarm.
        "snap": snap,
        # `domains` is the new (Step 3) per-plugin payload. The legacy
        # fields above continue to mirror the sweeping plugin so existing
        # clients keep working; new clients can iterate `domains[]` and
        # render one card per domain.
        "domains": domain_results,
    }


class CheckHomeRequest(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    region: Optional[str] = None
    # Postal address for address-keyed lookups (e.g. ReCollect trash).
    address: Optional[str] = None


@app.post("/check-home")
def check_home(req: CheckHomeRequest):
    """Home flow: run home-subject domains (trash) at a saved residence.

    Separate from /check (the car flow) because a home is located by address,
    not by the parked-car coordinate, and uses a different plugin subject.
    """
    region, local_now = _resolve_region(req)
    city_key = _nearest_city_key(req.lat, req.lon, region)

    domain_results: list[dict] = []
    for plugin in plugins_for_city(city_key):
        if getattr(plugin, "subject", "car") != "home":
            continue
        resolved = plugin.resolve_for(None, req.lat, req.lon, city_key,
                                      address=req.address)
        result = plugin.format(resolved, None, local_now)
        domain_results.append({
            "id":             result.domain_id,
            "label":          result.label,
            "urgency":        result.urgency,
            "schedule_lines": list(result.schedule_lines),
            "extras":         dict(result.extras),
        })

    return {"city": city_key, "region": region, "domains": domain_results}


class PrefsRequest(BaseModel):
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    home_address: Optional[str] = None
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


@app.get("/config.js", include_in_schema=False)
def config_js():
    """Serve runtime config as a JS snippet so the frontend knows runtime flags."""
    js = (
        f"window.DEV_MODE = {'true' if _DEV_MODE_API else 'false'};\n"
        f"window.PMTILES_MODE = {'true' if _PMTILES_MODE else 'false'};\n"
        "window.REGION_TZ = " + json.dumps(
            {rk: rv.get("tz", "UTC") for rk, rv in REGIONS.items()}
        ) + ";\n"
    )
    return Response(content=js, media_type="application/javascript")


@app.get("/zone/detail", include_in_schema=False)
def zone_detail(code: str = "", street: str = "", city: str = "", region: str = ""):
    """Full-year zone schedule HTML + PDF link for a clicked tile (PMTILES mode).

    Reuses maps._zone_detail so the popup matches the legacy server-rendered one.
    """
    tz = REGIONS.get(region, {}).get("tz", "UTC")
    local_now = datetime.now(ZoneInfo(tz))
    row = {"STREET_DISPLAY": street, "STREET_NAME": street,
           "DAY_EVEN": code, "DESC_EVEN": "", "_city": city}
    return {"detail_html": maps._zone_detail(row, local_now)}


# ---------------------------------------------------------------------------
# Static frontend (mounted last so API routes take priority)
# ---------------------------------------------------------------------------

_frontend_dir = os.path.join(_REPO_ROOT, "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
