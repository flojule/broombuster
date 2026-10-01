"""In-memory city/region GeoDataFrames (EPSG:3857) and their loading.

Each city loads once in its own background thread and signals a
threading.Event; a request waits only for the city (or cities) overlapping the
user. City data ships through git, so a new dataset arrives with a rollout
(restart) and is never swapped in at runtime.
"""

import functools
import logging
import os
import subprocess
import threading
import time

import geopandas
import pandas as pd

from broombuster import analysis, data_loader
from broombuster.cities import REGIONS, in_bbox, region_of
from broombuster.config import REPO_ROOT

logger = logging.getLogger("broombuster.api")

_city_gdfs: dict = {}        # city_key → GeoDataFrame (EPSG:3857)
_city_events: dict = {}      # city_key → threading.Event (set when done)
_region_combined: dict = {}  # region_key → (frozenset(loaded_keys), gdf_3857)
_lock = threading.Lock()     # guards _city_gdfs / _region_combined updates


def _load_city(city_key: str) -> bool:
    """Load a city into the cache (once); return availability.

    Always signals the city's event so waiters are released on failure too.
    """
    ev = _city_events.setdefault(city_key, threading.Event())
    if city_key in _city_gdfs:
        ev.set()
        return True
    region = region_of(city_key)
    try:
        gdf = data_loader.load_city_data(city_key).to_crs("EPSG:3857")
    except Exception:  # noqa: BLE001 — any failure must release waiters
        logger.exception("could not load city '%s'", city_key)
        ev.set()
        _warm_if_region_settled(region)
        return False
    with _lock:
        _city_gdfs[city_key] = gdf
        _region_combined.pop(region, None)
    ev.set()
    _warm_if_region_settled(region)
    return True


def _warm_if_region_settled(region: str | None) -> None:
    """Warm `region` once every city in it has finished loading (or failed).

    Runs on the loader thread of the last city to settle.
    """
    if region and all(
        _city_events.get(ck) is not None and _city_events[ck].is_set()
        for ck in REGIONS[region]["cities"]
    ):
        warm_region(region)


@functools.cache
def _git_commit_time(path: str) -> float | None:
    """Unix time of the last commit touching `path`, or None (untracked / no git).

    Cached per process: tracked data only changes through a rollout, which
    restarts the server.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return float(out) if out else None


def data_age_days(city: dict) -> float | None:
    """Age of a city's FGB in days, or None if missing.

    Measured from the file's last git commit, because a checkout or rollout
    rewrites mtimes and would make old data look fresh.
    """
    path = os.path.join(REPO_ROOT, city["fgb_path"])
    if not os.path.exists(path):
        return None
    changed_at = _git_commit_time(path) or os.path.getmtime(path)
    return (time.time() - changed_at) / 86400


def _priority_cities(lat: float, lon: float, region_key: str) -> list:
    """Cities whose bbox contains (lat, lon) first; rest after."""
    city_keys = REGIONS[region_key]["cities"]
    priority = [ck for ck in city_keys if in_bbox(ck, lat, lon)]
    return priority + [ck for ck in city_keys if ck not in priority]


def region_gdf(lat: float, lon: float, region_key: str):
    """The region's combined EPSG:3857 GDF, or None when nothing has loaded.

    Waits (up to 120 s) for the first city whose bbox contains the point, then
    combines whatever cities of the region are loaded.
    """
    for ck in _priority_cities(lat, lon, region_key):
        ev = _city_events.get(ck)
        if ev:
            ev.wait(timeout=120)
        if ck in _city_gdfs:
            break  # have data for the user's city; good enough to proceed
    return _combined_region(region_key)


def _combined_region(region_key: str):
    """EPSG:3857 GDF over the region's loaded cities, or None.

    Cached until the set of loaded cities changes, so the per-GDF indexes
    (spatial index, analysis.segment_index) are reused across requests.
    """
    with _lock:
        loaded = frozenset(ck for ck in REGIONS[region_key]["cities"] if ck in _city_gdfs)
        if not loaded:
            return None
        cached = _region_combined.get(region_key)
        if cached and cached[0] == loaded:
            return cached[1]
        frames = [_city_gdfs[ck] for ck in REGIONS[region_key]["cities"] if ck in loaded]
        combined = geopandas.GeoDataFrame(
            pd.concat(frames, ignore_index=True), crs="EPSG:3857")
        _region_combined[region_key] = (loaded, combined)
        return combined


def warm_region(region_key: str) -> None:
    """Build the region's combined frame and per-request indexes ahead of traffic.

    Without this the first request after a boot pays for the spatial index and
    analysis.segment_index (~1-2 s on the Pi) in-band.
    """
    try:
        gdf = _combined_region(region_key)
        if gdf is not None:
            gdf.sindex  # noqa: B018 — geopandas builds the STRtree lazily on access
            analysis.segment_index(gdf)
    except Exception:  # noqa: BLE001 — warming is best-effort; requests build lazily
        logger.exception("could not warm region '%s'", region_key)
