"""Region GeoDataFrames (EPSG:3857), loaded and indexed once at startup."""

import hashlib
import logging
import os
import pickle
import sys
from importlib.metadata import version
from pathlib import Path

from broombuster import analysis, config, data_loader, layers
from broombuster.cities import CITIES, REGIONS

log = logging.getLogger(__name__)
_regions: dict = {}  # region_key -> GeoDataFrame (EPSG:3857)


def load_all() -> None:
    """Load every region and build its request indexes; raises on bad data."""
    for rk in REGIONS:
        gdf = _load_region(rk)
        gdf.sindex  # noqa: B018 — builds the STRtree
        analysis.arrays(gdf)  # the schedule union's columns, before the first request
        _regions[rk] = gdf
    layers.load_all()


def _cache_key(region_key: str) -> str:
    """Changes with any of the region's FGBs (size, mtime), the loader or the libraries."""
    h = hashlib.sha256(sys.version.encode())
    for lib in ("pandas", "geopandas", "shapely", "pyproj", "numpy"):
        h.update(version(lib).encode())
    h.update(Path(data_loader.__file__).read_bytes())
    for ck in REGIONS[region_key]["cities"]:
        st = os.stat(os.path.join(config.REPO_ROOT, CITIES[ck]["fgb_path"]))
        h.update(f"{ck}:{st.st_size}:{st.st_mtime_ns};".encode())
    return h.hexdigest()[:16]


def _load_region(region_key: str):
    """The region's frame, from the pickle of the last start when its key still matches
    (the cache directory is the service's own state directory: trusted)."""
    cache = config.cache_dir()
    if not cache:
        return data_loader.load_region_data(region_key, crs="EPSG:3857", compact=True)
    path = os.path.join(cache, f"region-{region_key}-{_cache_key(region_key)}.pickle")
    try:
        with open(path, "rb") as f:
            return pickle.load(f)  # noqa: S301 (written by this service, in its own directory)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("unreadable region cache %s; rebuilding it", path)
    gdf = data_loader.load_region_data(region_key, crs="EPSG:3857", compact=True)
    try:
        os.makedirs(cache, exist_ok=True)
        for old in os.listdir(cache):
            if old.startswith(f"region-{region_key}-"):
                os.remove(os.path.join(cache, old))
        with open(path + ".tmp", "wb") as f:
            pickle.dump(gdf, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(path + ".tmp", path)
    except OSError:
        log.exception("could not write the region cache %s", path)
    return gdf


def region_gdf(region_key: str):
    return _regions[region_key]
