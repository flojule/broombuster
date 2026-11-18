#!/usr/bin/env python3
"""
Build data/napa/StreetSweeping.geojson from the city's sweeper routes and its swept
street segments (both fetched by scripts/rebuild_city_data.py napa).

Usage (from repo root):  python scripts/build_napa_geojson.py
Input:  data/napa/zones.geojson        route areas: StandardRt (Feb-Sep), Leaf_Rte (Oct-Jan)
        data/napa/centerlines.geojson  segments the sweeping contract covers, address ranges
Output: data/napa/StreetSweeping.geojson

Routes are swept once a month Feb-Sep and twice a month Oct-Jan, so they get DATES codes;
a sweep on a no-sweep holiday moves to the next weekday. Downtown route "MF" lists Mondays
and Fridays: both are kept all year. Both sides share the schedule.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import holidays
from broombuster.config import REPO_ROOT

_LEAF_MONTHS = {10, 11, 12, 1}
_DOWNTOWN = "MF"


def entry(route, years, off) -> tuple[str, str]:
    """(code, desc) of a route area."""
    if route["RouteName"] == _DOWNTOWN:
        return citykit.code_from_text("Every Monday and Friday")
    standard = citykit.code_from_text(route["StandardRt"])
    leaf = citykit.code_from_text(route["Leaf_Rte"])
    if not (standard and leaf):
        raise ValueError(f"unreadable schedule in route {route['RouteName']}")
    days = [
        d
        for y in years
        for m in range(1, 13)
        for d in citykit.month_dates(route["Leaf_Rte" if m in _LEAF_MONTHS else "StandardRt"], y, m)
    ]
    return citykit.dates_code(days, off), f"{standard[1]}; Oct–Jan {leaf[1]}"


def rows(lines, zones, years, off) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept segment inside a route area."""
    blocks = citykit.zones_to_blocks(lines, zones)
    by_route = {r["RouteName"]: entry(r, years, off) for _, r in zones.iterrows()}
    entries = [by_route[r] for r in blocks["RouteName"]]
    out = blocks[["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]].copy()
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"] = [e[0] for e in entries]
        out[f"DESC_{side}"] = [e[1] for e in entries]
        out[f"TIME_{side}"] = None
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "napa"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "napa")
    zones = geopandas.read_file(data_dir / "zones.geojson")
    off = {d for d, h in holidays.for_city("napa").items() if "sweeping" in h["suspends"]}
    print(f"  {len(zones)} route areas, {len(lines)} swept segments")
    pdf_city.write(
        rows(lines, zones, citykit.sweep_years(), off), data_dir / "StreetSweeping.geojson"
    )


if __name__ == "__main__":
    main()
