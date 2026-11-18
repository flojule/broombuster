#!/usr/bin/env python3
"""
Build data/fairfield/StreetSweeping.geojson from the city's sweeping areas and routes and
Solano County's centerlines (all fetched by scripts/rebuild_city_data.py fairfield).

Usage (from repo root):  python scripts/build_fairfield_geojson.py
Input:  data/fairfield/zones.geojson        residential areas: regular and fall schedules
        data/fairfield/commercial.geojson   commercial routes (lines) and their schedule
        data/fairfield/centerlines.geojson  blocks with address ranges
Output: data/fairfield/StreetSweeping.geojson

A block along a commercial route takes the route's schedule; other blocks take their
residential area's. Residential areas are swept once a month Feb-Oct and twice a month
Nov-Jan (the layer gives this season's schedule per month), so they get DATES codes; a
sweep on a no-sweep holiday moves to the next weekday. Both sides share the schedule.
"""

from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import holidays, normalize
from broombuster.config import REPO_ROOT

_SEASON = 2026  # FallSweep_Nov/Dec/Jan cover Nov 2026 - Jan 2027 (layer edited 2026-11-01)
_ROUTE_M = 10.0  # a block within this distance of a commercial route line is on the route
_UTM = "EPSG:26910"


def _month_words(zone, year: int, month: int) -> str:
    """The zone's schedule words for one month."""
    fall = {11: "FallSweep_Nov", 12: "FallSweep_Dec", 1: "FallSweep_Jan"}.get(month)
    if fall is None:
        return zone["Regular_Schedule"]
    season = year if month > 1 else year - 1
    words = zone[fall] if season == _SEASON else None
    return words if normalize.is_text(words) else zone["Base_Fall_Schedule"]


def residential_entry(zone, years, off) -> tuple[str, str]:
    """(code, desc) of a residential area."""
    days = [
        d
        for y in years
        for m in range(1, 13)
        for d in citykit.month_dates(_month_words(zone, y, m), y, m)
    ]
    regular = citykit.code_from_text(zone["Regular_Schedule"])
    fall = citykit.code_from_text(zone["Base_Fall_Schedule"])
    if not (regular and fall):
        raise ValueError(f"unreadable schedule in area {zone['OBJECTID']}")
    return citykit.dates_code(days, off), f"{regular[1]}; Nov–Jan {fall[1]}"


def rows(lines, zones, routes, years, off) -> geopandas.GeoDataFrame:
    """Standard-schema rows: blocks on a commercial route, then blocks in a residential area."""
    lines = lines.assign(_id=range(len(lines)))
    buf = routes.to_crs(_UTM)
    buf["geometry"] = buf.buffer(_ROUTE_M)
    buf = buf.dissolve("Routes").reset_index()[["Routes", "geometry"]].to_crs("EPSG:4326")
    com = citykit.zones_to_blocks(lines, buf, min_share=0.6)
    res = citykit.zones_to_blocks(lines[~lines["_id"].isin(com["_id"])], zones)
    entries = [citykit.code_from_text(r) for r in com["Routes"]]
    by_zone = {z["OBJECTID"]: residential_entry(z, years, off) for _, z in zones.iterrows()}
    entries += [by_zone[o] for o in res["OBJECTID"]]
    out = geopandas.GeoDataFrame(pd.concat([com, res], ignore_index=True), crs="EPSG:4326")
    out = out[["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]]
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"] = [e[0] for e in entries]
        out[f"DESC_{side}"] = [e[1] for e in entries]
        out[f"TIME_{side}"] = None
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "fairfield"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "solano")
    zones = geopandas.read_file(data_dir / "zones.geojson")
    routes = geopandas.read_file(data_dir / "commercial.geojson")
    off = {d for d, h in holidays.for_city("fairfield").items() if "sweeping" in h["suspends"]}
    print(f"  {len(zones)} residential areas, {len(routes)} commercial route lines")
    out = rows(lines, zones, routes, citykit.sweep_years(), off)
    pdf_city.write(out, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
