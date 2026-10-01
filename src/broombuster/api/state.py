"""In-memory city/region GeoDataFrame caches, loading and hot-swap.

Each city loads in its own background thread and signals a threading.Event; a
/check request waits only for the city (or cities) overlapping the user.
"""

import functools
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

import geopandas
import pandas as pd

from broombuster import analysis, data_loader
from broombuster.cities import CITIES, REGIONS, in_bbox, region_of
from broombuster.config import DATA_AUTO_REFRESH, PMTILES_MODE, REPO_ROOT

logger = logging.getLogger("broombuster.api")

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
        _warm_if_region_settled(region)
        return False
    with _swap_lock:
        _city_gdfs[city_key] = new_4326
        _city_gdfs_3857[city_key] = new_3857
        _city_loaded_at[city_key] = time.time()
        _region_combined.pop(region, None)
    ev.set()
    _warm_if_region_settled(region)
    if force:
        logger.info("[freshness] %s refreshed", CITIES[city_key]["name"])
        _rebuild_region_tiles([region])
    return True


def _warm_if_region_settled(region: str | None) -> None:
    """Warm `region` once every city in it has finished loading (or failed).

    Runs on the loader thread of the last city to settle, and after a hot-swap.
    """
    if region and all(
        _city_events.get(ck) is not None and _city_events[ck].is_set()
        for ck in REGIONS[region]["cities"]
    ):
        warm_region(region)


def _rebuild_region_tiles(region_keys) -> None:
    """Kick off a detached PMTiles rebuild per region (PMTILES mode only)."""
    if not PMTILES_MODE or not region_keys:
        return
    if not shutil.which("tippecanoe"):
        logger.warning("[tiles] tippecanoe not on PATH; skipping tile rebuild")
        return
    script = os.path.join(REPO_ROOT, "scripts", "build_pmtiles.py")
    for rk in region_keys:
        try:
            subprocess.Popen(
                [sys.executable, script, "--region", rk, "--force"],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.info("[tiles] rebuild started for region %s", rk)
        except OSError as exc:
            logger.warning("[tiles] could not start rebuild for %s: %s", rk, exc)


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
    """Age of a city's FGB (else raw) file in days, or None if missing.

    Measured from the file's last git commit, because a checkout or rollout
    rewrites mtimes and would make old data look fresh. With DATA_AUTO_REFRESH
    the file is re-downloaded in place, so its mtime is the true age.
    """
    path = os.path.join(REPO_ROOT, city.get("fgb_path") or city["local_path"])
    if not os.path.exists(path):
        return None
    changed_at = None if DATA_AUTO_REFRESH else _git_commit_time(path)
    return (time.time() - (changed_at or os.path.getmtime(path))) / 86400


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

            age_days = data_age_days(city)
            if age_days is not None:
                if age_days < stale_after_days:
                    continue
                logger.info("[freshness] %s data is %.0f days old (threshold %sd)",
                            city["name"], age_days, stale_after_days)
            # File missing or stale — refresh.
            _load_city(city_key, force=True)

        time.sleep(3600)  # re-check every hour (only downloads when actually stale)


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

    return _combined_region(region_key)


def _combined_region(region_key: str):
    """(gdf_4326, gdf_3857) over the region's loaded cities, or (None, None).

    Cached until the set of loaded cities changes, so the per-GDF indexes
    (spatial index, analysis.segment_index) are reused across requests.
    """
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


def warm_region(region_key: str) -> None:
    """Build the region's combined frames and per-request indexes ahead of traffic.

    Without this the first /check after a boot or hot-swap pays for the
    spatial index and analysis.segment_index (~1-2 s on the Pi) in-band.
    """
    try:
        _, c3 = _combined_region(region_key)
        if c3 is not None:
            c3.sindex  # noqa: B018 — geopandas builds the STRtree lazily on access
            analysis.segment_index(c3)
    except Exception:  # noqa: BLE001 — warming is best-effort; requests build lazily
        logger.exception("could not warm region '%s'", region_key)
