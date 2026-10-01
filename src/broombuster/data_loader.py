"""City FlatGeobufs: load at runtime, build from raw input (schema: data/README.md)."""

import os

import geopandas
import pandas as pd
from shapely.geometry import box as _shapely_box

from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT as _ROOT
from broombuster.schemas import SCHEMA_COLS, SCHEMA_PROFILES


def load_city_data(city_key: str) -> geopandas.GeoDataFrame:
    """A city's normalised GeoDataFrame (EPSG:4326), tagged with `_city`."""
    gdf = geopandas.read_file(os.path.join(_ROOT, CITIES[city_key]["fgb_path"]))
    gdf["_city"] = city_key
    return gdf


def load_region_data(region_key: str) -> geopandas.GeoDataFrame:
    """Every city of a region in one EPSG:4326 GeoDataFrame."""
    return geopandas.GeoDataFrame(
        pd.concat([load_city_data(ck) for ck in REGIONS[region_key]["cities"]],
                  ignore_index=True),
        crs="EPSG:4326")


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
