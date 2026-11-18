#!/usr/bin/env python3
"""
Build data/san_ramon/StreetSweeping.geojson from the city's sweeping zones and the
county's TIGER/Line streets (both fetched by scripts/rebuild_city_data.py san_ramon).

Usage (from repo root):  python scripts/build_san_ramon_geojson.py
Input:  data/san_ramon/zones.geojson               zone polygons named by schedule
        data/san_ramon/tl_2026_06013_edges.zip     Contra Costa County streets
Output: data/san_ramon/StreetSweeping.geojson

Each street block takes the zone holding most of it, on both sides. The "Commercial" zone
has no published day (POSTED).
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

_DIR = Path(REPO_ROOT) / "data" / "san_ramon"


def build(zones, lines):
    """Standard-schema rows: the zone schedule ("First and Third Monday") on both sides."""
    blocks = citykit.zones_to_blocks(lines, zones[["Name", "geometry"]])
    return citykit.both_sides(blocks, [citykit.side_entry(n) for n in blocks["Name"]])


def main():
    zones_path, edges = _DIR / "zones.geojson", _DIR / "tl_2026_06013_edges.zip"
    if not (zones_path.exists() and edges.exists()):
        raise FileNotFoundError(f"inputs missing in {_DIR}; run rebuild_city_data.py san_ramon")
    zones = geopandas.read_file(zones_path)
    print(f"  {len(zones)} zones")
    out = build(zones, citykit.centerlines(edges, "contra_costa"))
    pdf_city.write(out, _DIR / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
