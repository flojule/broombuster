#!/usr/bin/env python3
"""
Build data/emeryville/StreetSweeping.geojson from the city's sweeping route lines and the
county's street centerlines (both fetched by scripts/rebuild_city_data.py emeryville).

Usage (from repo root):  python scripts/build_emeryville_geojson.py
Input:  data/emeryville/routes.geojson       route lines: DayOfMonth, DayOfWeek, TimeOfDay
        data/emeryville/centerlines.geojson  county blocks with address ranges
Output: data/emeryville/StreetSweeping.geojson

A route line drawn along a curb ("1st" or "2nd" Thursday) gives that side; one on the
centerline gives the sides left without a curb line. "1st or 2nd" Thursday on the
centerline alone (which side not drawn) warns on both Thursdays.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

# The layer's TimeOfDay domain: 8pm-5am (stored up to midnight, the end in words), 8am-noon.
_TIME = {1: ("8PM-12AM", ", until 5AM next day"), 2: ("8AM-12PM", "")}
_UTM = "EPSG:26910"
_CURB_M = 2.5  # a route line farther than this from the centerline is one side's


def _side(block, route) -> str | None:
    """ "L" or "R": the side of `block` a curb route line runs along; None on the centerline."""
    d = block.project(route.interpolate(0.5, normalized=True))
    m = block.interpolate(d)
    q = route.interpolate(route.project(m))
    if m.distance(q) < _CURB_M:
        return None
    a, b = block.interpolate(max(d - 1, 0)), block.interpolate(min(d + 1, block.length))
    cross = (b.x - a.x) * (q.y - m.y) - (b.y - a.y) * (q.x - m.x)
    return "L" if cross > 0 else "R"


def join(routes: geopandas.GeoDataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: each county block with the route lines running along it."""
    routes = routes[routes["DayOfWeek"].notna() & routes["DayOfMonth"].notna()].to_crs(_UTM)
    blocks = lines.to_crs(_UTM).reset_index(drop=True)
    by_block = {}  # block index: [(side or None, schedule words, time)]
    for i, r in citykit.blocks_along(blocks, routes):
        route = routes.loc[r]
        week = route["DayOfMonth"].replace(" or ", " & ")
        entry = (_side(blocks.geometry[i], route.geometry), f"{week} {route['DayOfWeek']}")
        by_block.setdefault(i, []).append((*entry, _TIME[route["TimeOfDay"]]))
    rows = []
    for i, along in sorted(by_block.items()):
        got = {}  # curb lines first
        for side, words, time in sorted(along, key=lambda a: a[0] is None):
            for s in [side] if side else ["L", "R"]:
                got.setdefault(s, (words, time))
        rec = blocks.iloc[i].to_dict()
        parity = citykit.parity_sides(rec)
        if len(parity) < 2 and len(set(got.values())) == 1:  # one schedule: both sides
            parity = {"L": "EVEN", "R": "ODD"}
            got = dict.fromkeys(parity, next(iter(got.values())))
        for s, par in parity.items():
            if s in got:
                words, (time, more) = got[s]
                code, desc, time = citykit.side_entry(words, time)
                entry = code, desc + more, time
                rec[f"DAY_{par}"], rec[f"DESC_{par}"], rec[f"TIME_{par}"] = entry
        rows.append(rec)
    out = geopandas.GeoDataFrame(rows, geometry="geometry", crs=_UTM).to_crs("EPSG:4326")
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "emeryville"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "alameda")
    routes = geopandas.read_file(data_dir / "routes.geojson")
    out = join(routes, lines)
    print(f"  {len(routes)} route lines, {len(out)} of {len(lines)} blocks swept")
    pdf_city.write(out, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
