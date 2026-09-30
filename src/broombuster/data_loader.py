"""
Loads and normalises street-sweeping GeoDataFrames for any supported city.

After loading, every GeoDataFrame shares this standard column schema so that
analysis.py and maps.py work identically regardless of the data source:

  STREET_NAME    Full street name, upper-case, whitespace-normalised
  DAY_EVEN       Oakland-style sweep-day code for even-numbered addresses
                 (e.g. "M13", "FE", "WE").  None / NaN means no sweep.
  DAY_ODD        Same for odd-numbered addresses.
  DESC_EVEN      Human-readable schedule description – even side
  DESC_ODD       Human-readable schedule description – odd side
  TIME_EVEN      Sweep time window string – even side  (e.g. "8AM–10AM")
  TIME_ODD       Sweep time window string – odd side
  L_F_ADD        Left-side from-address number  (NaN if unavailable)
  L_T_ADD        Left-side to-address number
  R_F_ADD        Right-side from-address number
  R_T_ADD        Right-side to-address number
  SIDE_EVEN      Optional display label of the even bucket (SF: compass side,
  SIDE_ODD       e.g. "North"); absent / null means "Even" / "Odd"

Day codes follow the grammar in analysis.py (e.g. "ME" every Mon, "M13"
1st+3rd Mon, "T135" 1st+3rd+5th Tue, "MTHE" every Mon+Thu, "DATES:..."
explicit dates); analysis.NO_SWEEP_CODES lists the no-sweeping markers.
"""

import io
import os
import tempfile
import threading
import zipfile
from collections import OrderedDict

import geopandas
import pandas as pd
import requests
from shapely.geometry import box as _shapely_box

from broombuster.cities import CITIES
from broombuster.schemas import SCHEMA_PROFILES

# Repo root — resolves data paths regardless of working directory.
# This file is <repo>/src/broombuster/data_loader.py — walk up three levels.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# BroomBuster cache layers (each a distinct key/scope, not redundant):
#   1. _GDF_CACHE (here)         path -> (mtime, gdf)     skip FGB disk reads
#   2. app._city_gdfs[_3857]     city -> projected gdf    runtime serving cache
#   3. app._region_combined      region -> concat gdf     skip per-request concat
#   4. analysis._*_cache         id(gdf) -> name index    skip O(n) index rebuild
# Layer 1 serves the CLI and tests (repeat load_city_data); the API loads each
# city once into layer 2, so layer 1 is keep-warm, not on the request path.

# In-memory LRU cache for already-read FlatGeobuf files: path -> (mtime, gdf).
MAX_GDF_CACHE_ENTRIES = int(os.environ.get("MAX_GDF_CACHE_ENTRIES", "5"))
_GDF_CACHE: "OrderedDict[str, tuple[float, geopandas.GeoDataFrame]]" = OrderedDict()
_GDF_CACHE_LOCK = threading.Lock()


def _cache_put(path: str, mtime: float, gdf: geopandas.GeoDataFrame) -> None:
    """Insert into the LRU cache, evicting the oldest entries over capacity."""
    with _GDF_CACHE_LOCK:
        _GDF_CACHE[path] = (mtime, gdf)
        _GDF_CACHE.move_to_end(path)
        while len(_GDF_CACHE) > MAX_GDF_CACHE_ENTRIES:
            _GDF_CACHE.popitem(last=False)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SCHEMA_COLS = [
    "STREET_NAME",
    "STREET_KEY",
    "STREET_DISPLAY",
    "DAY_EVEN", "DAY_ODD",
    "DESC_EVEN", "DESC_ODD",
    "TIME_EVEN", "TIME_ODD",
    "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD",
    "SIDE_EVEN", "SIDE_ODD",
]


def load_city_data(city_key: str, *, force_refresh: bool = False) -> geopandas.GeoDataFrame:
    """Return a normalised GeoDataFrame for the given city key (EPSG:4326).

    On first call, the raw source is normalised and saved as a FlatGeobuf file
    (``city['fgb_path']``). Subsequent calls read that file directly — no
    normalisation overhead at runtime.  Pass ``force_refresh=True`` to rebuild
    (re-downloading auto-download cities); existing files are replaced only
    after the rebuild succeeds.
    """
    city       = CITIES[city_key]
    local_path = os.path.join(_ROOT, city["local_path"])
    fgb_raw    = city.get("fgb_path", "")
    fgb_path   = os.path.join(_ROOT, fgb_raw) if fgb_raw else None

    # Fast path: FGB already built → read from disk or in-memory cache.
    if not force_refresh and fgb_path and os.path.exists(fgb_path):
        mtime = os.path.getmtime(fgb_path)
        with _GDF_CACHE_LOCK:
            cached = _GDF_CACHE.get(fgb_path)
            if cached and cached[0] == mtime:
                # Move to end (most-recently-used)
                _GDF_CACHE.move_to_end(fgb_path)
                return cached[1].copy()
        gdf = geopandas.read_file(fgb_path)
        # Post-process read GDF for in-memory consumption: for Chicago we
        # prefer a readable `STREET_NAME` (e.g. "Ward 05, Section 03"). The
        # on-disk FGB keeps `STREET_NAME` uppercase for storage consistency.
        if city.get("schema") == "chicago" and "STREET_DISPLAY" in gdf.columns:
            gdf = gdf.copy()
            gdf["STREET_NAME"] = gdf["STREET_DISPLAY"]
        _cache_put(fgb_path, mtime, gdf)
        return gdf.copy()

    # --- Slow path: build from raw source ---
    url = city.get("url")
    download = bool(url) and (force_refresh or not os.path.exists(local_path))
    if not download and not os.path.exists(local_path):
        raise FileNotFoundError(
            f"No data found for {city['name']}.\n"
            f"  Missing FGB:   {fgb_path}\n"
            f"  Missing raw:   {local_path}\n"
            f"To rebuild from the upstream source, run:\n"
            f"    python scripts/rebuild_city_data.py {city_key}\n"
            f"See data/sources.yaml for the source URL and any manual steps."
        )
    raw_dir = os.path.dirname(local_path)
    os.makedirs(raw_dir, exist_ok=True)
    # Stage downloads in a temp dir; promote them only after the FGB is written.
    with tempfile.TemporaryDirectory(dir=raw_dir) as tmp:
        src = local_path
        if download:
            print(f"Downloading {city['name']} data …")
            src = os.path.join(tmp, os.path.basename(local_path))
            _download(url, src)
            print("Download complete.")

        gdf = geopandas.read_file(src)

        # Optional geographic clip.  Reproject to EPSG:4326 for the intersection
        # test (bbox coords are always degrees), keep original CRS for normalisation.
        if "bbox" in city:
            lat_min, lon_min, lat_max, lon_max = city["bbox"]
            clip = _shapely_box(lon_min, lat_min, lon_max, lat_max)
            reproject = gdf.crs and not gdf.crs.equals("EPSG:4326")
            gdf_4326 = gdf.to_crs("EPSG:4326") if reproject else gdf
            gdf = gdf[gdf_4326.geometry.intersects(clip)].copy()

        gdf = _normalise(gdf, city["schema"])
        if gdf.empty:
            raise ValueError(f"{city['name']}: normalised data is empty; keeping existing files")

        # Persist as FGB for fast future loads.
        if fgb_path:
            _save_fgb(gdf, fgb_path)
        if download:
            for name in os.listdir(tmp):
                os.replace(os.path.join(tmp, name), os.path.join(raw_dir, name))

    return gdf


def load_region_data(region_key: str, *, force_refresh: bool = False) -> geopandas.GeoDataFrame:
    """
    Return a normalised GeoDataFrame covering all cities in the given region.

    Cities whose data files are missing (and have no auto-download URL) are
    skipped with a warning, so the rest of the region still loads.  Each row
    gets a ``_city`` column with the source city key.
    """

    from broombuster.cities import REGIONS

    region = REGIONS[region_key]
    print(f"Loading region '{region['name']}' …")
    gdfs = []
    for city_key in region["cities"]:
        try:
            gdf = load_city_data(city_key, force_refresh=force_refresh).copy()
            gdf["_city"] = city_key
            gdfs.append(gdf)
            print(f"  ✓ {CITIES[city_key]['name']} ({len(gdf)} segments)")
        except FileNotFoundError as exc:
            print(f"  ⚠  Skipping {CITIES[city_key]['name']}: {exc}")

    if not gdfs:
        raise RuntimeError(
            f"No city data could be loaded for region '{region_key}'.\n"
            "Place the required data files and retry (see cities.py for details)."
        )

    combined = geopandas.GeoDataFrame(
        pd.concat(
            [g.to_crs("EPSG:4326") for g in gdfs],
            ignore_index=True,
        ),
        crs="EPSG:4326",
    )
    print(f"Region ready — {len(combined)} total segments.")
    return combined


# ---------------------------------------------------------------------------
# FlatGeobuf helpers
# ---------------------------------------------------------------------------

def _save_fgb(gdf: geopandas.GeoDataFrame, fgb_path: str) -> None:
    """Write a normalised GDF to FlatGeobuf (schema columns + geometry, EPSG:4326)."""
    cols = [c for c in _SCHEMA_COLS if c in gdf.columns]
    out = gdf[cols + ["geometry"]].copy()
    # Reproject to EPSG:4326 so every FGB is in a consistent CRS.
    if out.crs and not out.crs.equals("EPSG:4326"):
        out = out.to_crs("EPSG:4326")
    os.makedirs(os.path.dirname(os.path.abspath(fgb_path)), exist_ok=True)
    # Persist a disk-friendly copy: ensure stored STREET_NAME is uppercase
    disk_out = out.copy()
    if "STREET_NAME" in disk_out.columns:
        try:
            disk_out["STREET_NAME"] = disk_out["STREET_NAME"].astype(str).str.upper()
        except Exception:
            pass
    # Write beside the target, then rename, so a failed write keeps the old file.
    tmp_path = os.path.splitext(fgb_path)[0] + ".tmp.fgb"
    try:
        disk_out.to_file(tmp_path, driver="FlatGeobuf")
        os.replace(tmp_path, fgb_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    mb = os.path.getsize(fgb_path) / 1_048_576
    print(f"  Saved FGB → {fgb_path}  ({mb:.1f} MB)")
    # Cache the readable in-memory copy (out); the file stores uppercase STREET_NAME.
    _cache_put(fgb_path, os.path.getmtime(fgb_path), out.copy())


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(url: str, local_path: str) -> None:
    """Fetch url to local_path; zip archives are extracted beside it."""
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "zip" in content_type or "Shapefile" in url or local_path.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        z.extractall(os.path.dirname(local_path))
    else:
        with open(local_path, "wb") as fh:
            fh.write(resp.content)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _normalise(gdf: geopandas.GeoDataFrame, schema: str) -> geopandas.GeoDataFrame:
    fn = SCHEMA_PROFILES.get(schema)
    if fn is None:
        raise ValueError(
            f"Unknown schema '{schema}'. Known profiles: {sorted(SCHEMA_PROFILES)}"
        )
    return fn(gdf)
