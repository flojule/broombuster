#!/usr/bin/env python3
"""
Build data/pittsburg/StreetSweeping.geojson from the city's sweeping layer and the
county's TIGER/Line streets (all fetched by scripts/rebuild_city_data.py pittsburg).

Usage (from repo root):  python scripts/build_pittsburg_geojson.py
Input:  data/pittsburg/residential.geojson        zones: Schedule, SweepingTime, Code_Enforcement
        data/pittsburg/commercial.geojson         route lines: Schedule "C - Every Wednesday"
        data/pittsburg/tl_2026_06013_edges.zip    Contra Costa County streets
Output: data/pittsburg/StreetSweeping.geojson

Each street block takes the commercial route it runs along, else the residential zone
holding most of it, on both sides. A zone without code enforcement is NT.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster.config import REPO_ROOT

_DIR = Path(REPO_ROOT) / "data" / "pittsburg"


def residential_entry(schedule, time, enforcement):
    """(code, desc, time) of a residential zone; "Thrusday" is a typo in the source."""
    text = re.sub(r"\bthrusday\b", "Thursday", str(schedule or ""), flags=re.I)
    return citykit.side_entry(text, time, enforced=str(enforcement).strip().lower() != "no")


def commercial_entry(schedule):
    """(code, desc, time) of a commercial route: "A - Every Tuesday & Thursday"."""
    return citykit.side_entry(re.sub(r"^\s*[A-Z]\s*-\s*", "", str(schedule or "")))


def build(residential, commercial, lines):
    """Standard-schema rows: commercial route blocks, then residential zone blocks."""
    lines = lines.assign(_id=range(len(lines)))
    routes = commercial[commercial["Schedule"].notna()]
    com = citykit.routes_to_blocks(lines, routes[["Schedule", "geometry"]])
    res = citykit.zones_to_blocks(
        lines[~lines["_id"].isin(com["_id"])],
        residential[["Schedule", "SweepingTime", "Code_Enforcement", "geometry"]],
    )
    entries = [commercial_entry(s) for s in com["Schedule"]] + [
        residential_entry(s, t, e)
        for s, t, e in zip(
            res["Schedule"], res["SweepingTime"], res["Code_Enforcement"], strict=True
        )
    ]
    blocks = geopandas.GeoDataFrame(pd.concat([com, res], ignore_index=True), crs="EPSG:4326")
    return citykit.both_sides(blocks, entries)


def main():
    paths = [
        _DIR / f for f in ("residential.geojson", "commercial.geojson", "tl_2026_06013_edges.zip")
    ]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {_DIR}; run rebuild_city_data.py pittsburg")
    residential, commercial = (geopandas.read_file(p) for p in paths[:2])
    print(f"  {len(residential)} residential zones, {len(commercial)} commercial route lines")
    out = build(residential, commercial, citykit.centerlines(paths[2], "contra_costa"))
    pdf_city.write(out, _DIR / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
