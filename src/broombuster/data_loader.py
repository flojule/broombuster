"""
Loads and normalises street-sweeping GeoDataFrames for any supported city.

After loading, every GeoDataFrame shares this standard column schema so that
analysis.py and maps.py work identically regardless of the data source:

  STREET_NAME    Full street name, upper-case, whitespace-normalised
  STREET_KEY     Canonical comparison key (normalize.street_name)
  STREET_DISPLAY Short readable name (normalize.street_display)
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
  _city          City key (added on load; not stored in the FGB)

Day codes follow the grammar in analysis.py (e.g. "ME" every Mon, "M13"
1st+3rd Mon, "T135" 1st+3rd+5th Tue, "MTHE" every Mon+Thu, "DATES:..."
explicit dates); analysis.NO_SWEEP_CODES lists the no-sweeping markers.

The runtime only reads the committed FlatGeobuf (`fgb_path`). Raw inputs are
fetched and normalised into it by scripts/rebuild_city_data.py, which calls
build_city_fgb().
"""

import functools
import os

import geopandas
import pandas as pd
from shapely.geometry import box as _shapely_box

from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT as _ROOT
from broombuster.schemas import SCHEMA_COLS, SCHEMA_PROFILES


@functools.lru_cache(maxsize=8)
def _read_fgb(path: str, mtime: float) -> geopandas.GeoDataFrame:
    """Read a FlatGeobuf once per (path, mtime); callers get copies."""
    return geopandas.read_file(path)


def load_city_data(city_key: str) -> geopandas.GeoDataFrame:
    """Normalised GeoDataFrame (EPSG:4326) for a city, tagged with `_city`."""
    city = CITIES[city_key]
    path = os.path.join(_ROOT, city["fgb_path"])
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No data found for {city['name']}: missing {path}.\n"
            f"Rebuild it with:  python scripts/rebuild_city_data.py {city_key}"
        )
    gdf = _read_fgb(path, os.path.getmtime(path)).copy()
    gdf["_city"] = city_key
    return gdf


def load_region_data(region_key: str) -> geopandas.GeoDataFrame:
    """Every loadable city of a region in one EPSG:4326 GeoDataFrame.

    Cities whose FGB is missing are skipped with a warning, so the rest of the
    region still loads.
    """
    gdfs = []
    for city_key in REGIONS[region_key]["cities"]:
        try:
            gdfs.append(load_city_data(city_key))
        except FileNotFoundError as exc:
            print(f"  ⚠  Skipping {CITIES[city_key]['name']}: {exc}")
    if not gdfs:
        raise RuntimeError(f"No city data could be loaded for region '{region_key}'.")
    return geopandas.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs="EPSG:4326")


def build_city_fgb(city_key: str) -> geopandas.GeoDataFrame:
    """Normalise a city's raw input (`source.local_path`) and write its FGB.

    The existing FGB is replaced only after the new one is written.
    """
    city = CITIES[city_key]
    src = os.path.join(_ROOT, city["source"]["local_path"])
    if not os.path.exists(src):
        raise FileNotFoundError(f"{city['name']}: raw input {src} is missing")
    gdf = geopandas.read_file(src)

    # Optional geographic clip; bbox coords are always degrees.
    if "bbox" in city:
        lat_min, lon_min, lat_max, lon_max = city["bbox"]
        clip = _shapely_box(lon_min, lat_min, lon_max, lat_max)
        gdf_4326 = gdf.to_crs("EPSG:4326") if gdf.crs and not gdf.crs.equals("EPSG:4326") else gdf
        gdf = gdf[gdf_4326.geometry.intersects(clip)].copy()

    fn = SCHEMA_PROFILES.get(city["schema"])
    if fn is None:
        raise ValueError(f"Unknown schema {city['schema']!r}; known: {sorted(SCHEMA_PROFILES)}")
    gdf = fn(gdf)
    if gdf.empty:
        raise ValueError(f"{city['name']}: normalised data is empty; keeping existing files")

    fgb_path = os.path.join(_ROOT, city["fgb_path"])
    out = gdf[[c for c in SCHEMA_COLS if c in gdf.columns] + ["geometry"]]
    if out.crs and not out.crs.equals("EPSG:4326"):
        out = out.to_crs("EPSG:4326")
    os.makedirs(os.path.dirname(fgb_path), exist_ok=True)
    # Write beside the target, then rename, so a failed write keeps the old file.
    tmp_path = os.path.splitext(fgb_path)[0] + ".tmp.fgb"
    try:
        out.to_file(tmp_path, driver="FlatGeobuf")
        os.replace(tmp_path, fgb_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return out
