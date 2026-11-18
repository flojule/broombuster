#!/usr/bin/env python3
"""
Build data/danville/StreetSweeping.geojson from the town's sweeping zones and the
county's TIGER/Line streets (both fetched by scripts/rebuild_city_data.py danville).

Usage (from repo root):  python scripts/build_danville_geojson.py
Input:  data/danville/zones.geojson               zone polygons, SCHEDULE2 "2nd Thursday of Month"
        data/danville/tl_2026_06013_edges.zip     Contra Costa County streets
Output: data/danville/StreetSweeping.geojson

Each street block takes the zone holding most of it, on both sides.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

_DIR = Path(REPO_ROOT) / "data" / "danville"


def build(zones, lines):
    """Standard-schema rows: the zone's monthly day on both sides."""
    blocks = citykit.zones_to_blocks(lines, zones[["SCHEDULE2", "geometry"]])
    return citykit.both_sides(blocks, [citykit.side_entry(s) for s in blocks["SCHEDULE2"]])


def main():
    zones_path, edges = _DIR / "zones.geojson", _DIR / "tl_2026_06013_edges.zip"
    if not (zones_path.exists() and edges.exists()):
        raise FileNotFoundError(f"inputs missing in {_DIR}; run rebuild_city_data.py danville")
    zones = geopandas.read_file(zones_path)
    print(f"  {len(zones)} zones")
    out = build(zones, citykit.centerlines(edges, "contra_costa"))
    pdf_city.write(out, _DIR / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
