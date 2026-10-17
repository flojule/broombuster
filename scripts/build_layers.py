#!/usr/bin/env python3
"""Build cities' extra map layers (permit areas, meters, EV charging, …) from the
sources in broombuster/layer_sources.py, for the themes in each manifest's `layers:`.

    python scripts/build_layers.py [city ...] [--theme parking ...]

Writes data/<city>/<theme>.fgb (the server's lookups: /api/rules) and
frontend/layers/<region>/<theme>.geojson (the map's copy, every city of the region).
A source that fails keeps its previous files. Commit both outputs.
"""

import argparse
import os
import sys
from pathlib import Path

import geopandas
import pandas as pd
import shapely

from broombuster import layer_sources, layers
from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)


def _write_fgb(gdf, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.fgb")
    try:
        gdf.to_file(tmp, driver="FlatGeobuf")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_city(city_key: str, themes: list[str] | None) -> list[str]:
    """Rebuild a city's layers; returns the themes that failed."""
    failed = []
    for theme in CITIES[city_key].get("layers") or []:
        if themes and theme not in themes:
            continue
        src = layer_sources.SOURCES.get(theme, {}).get(city_key)
        if src is None:
            print(f"    {theme}: no source for {city_key}")
            failed.append(theme)
            continue
        try:
            gdf = src(CITIES[city_key])
        except Exception as e:  # one broken source must not stop the others
            print(f"    {theme}: FAILED ({e}); keeping the previous file")
            failed.append(theme)
            continue
        if gdf.empty:
            print(f"    {theme}: no features; keeping the previous file")
            failed.append(theme)
            continue
        # Only the city: some sources are county- or statewide.
        lat0, lon0, lat1, lon1 = CITIES[city_key]["bbox"]
        gdf = gdf.clip(shapely.box(lon0, lat0, lon1, lat1))
        gdf = gdf[~gdf.geometry.is_empty][[*layer_sources.COLUMNS, "geometry"]]
        _write_fgb(gdf, layers.fgb_path(city_key, theme))
        print(f"    {theme}: {len(gdf):,} features")
    return failed


def build_region_geojson(region_key: str) -> None:
    """frontend/layers/<region>/<theme>.geojson from the cities' FGBs."""
    out_dir = _ROOT / "frontend" / "layers" / region_key
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme in layers.THEMES:
        parts = []
        for ck in REGIONS[region_key]["cities"]:
            p = layers.fgb_path(ck, theme)
            if p.exists():
                g = geopandas.read_file(p)
                g["city"] = ck
                parts.append(g)
        target = out_dir / f"{theme}.geojson"
        if not parts or not layers.THEMES[theme]["map"]:
            target.unlink(missing_ok=True)
            continue
        gdf = geopandas.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
        target.write_text(layer_sources.dump_json(gdf))
        print(f"  {region_key}/{theme}.geojson: {len(gdf):,} features, "
              f"{target.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cities", nargs="*", choices=list(CITIES), help="cities (default: all)")
    ap.add_argument("--theme", action="append", choices=list(layers.THEMES))
    a = ap.parse_args()
    failed = []
    for ck in a.cities or CITIES:
        print(CITIES[ck]["name"])
        failed += [f"{ck}/{t}" for t in build_city(ck, a.theme)]
    for rk in REGIONS:
        build_region_geojson(rk)
    if failed:
        print("\nFailed (previous files kept): " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
