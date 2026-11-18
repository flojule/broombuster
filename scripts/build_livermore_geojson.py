#!/usr/bin/env python3
"""
Build data/livermore/StreetSweeping.geojson from the city's sweeping route polygons and
the county's street centerlines (both fetched by scripts/rebuild_city_data.py livermore).

Usage (from repo root):  python scripts/build_livermore_geojson.py
Input:  data/livermore/routes.geojson       route polygons, "First Monday" per route
        data/livermore/centerlines.geojson  county blocks with address ranges
Output: data/livermore/StreetSweeping.geojson

Each block takes the route holding most of it, on both sides. Routes whose schedule
reads "Call 960-8020" get the check-the-sign code.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT


def main():
    data_dir = Path(REPO_ROOT) / "data" / "livermore"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "alameda")
    routes = geopandas.read_file(data_dir / "routes.geojson")
    blocks = citykit.zones_to_blocks(lines, routes[["RouteSchedule", "geometry"]])
    entries = blocks["RouteSchedule"].map(citykit.side_entry)
    for side in ("EVEN", "ODD"):
        for i, col in enumerate(("DAY", "DESC", "TIME")):
            blocks[f"{col}_{side}"] = entries.str[i]
    print(f"  {len(routes)} routes, {len(blocks)} of {len(lines)} blocks in a route")
    pdf_city.write(blocks.drop(columns=["RouteSchedule"]), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
