#!/usr/bin/env python3
"""
Build data/oakley/StreetSweeping.geojson from the city's alphabetical sweeping list, its
sweeping route lines and the county's TIGER/Line streets (all fetched by
scripts/rebuild_city_data.py oakley).

Usage (from repo root):  python scripts/build_oakley_geojson.py
Input:  data/oakley/Street-Sweeping-List.pdf      street, route, "2nd & 4th Monday of each month"
        data/oakley/routes.geojson                route lines (which streets are swept)
        data/oakley/tl_2026_06013_edges.zip       Contra Costa County streets
Output: data/oakley/StreetSweeping.geojson

The swept blocks are the streets along a route line; each takes its street's schedule from
the list (the route lines' own labels disagree with it). A street the list gives several
schedules, or does not name, takes the schedule most of its route line's streets have;
none fits -> POSTED. Both sides alike.
"""

import collections
import difflib
import re
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

_DIR = Path(REPO_ROOT) / "data" / "oakley"
_ROW = re.compile(r"^(.+?) (?:[1-5]|art) ((?:1st|2nd)\b.*?of each month) [RA]\b", re.I)


def schedules(lines) -> dict[str, set[str]]:
    """{street key: {schedule words}} from the list's text lines."""
    out = collections.defaultdict(set)
    for ln in lines:
        m = _ROW.match(ln)
        if m:
            out[normalize.street_name(m[1])].add(re.sub(r" of each month$", "", m[2]))
    return dict(out)


def _listed(key, by_name) -> set[str]:
    """The list's schedules for a street key, else for its closest spelling (MAILCOAT)."""
    if key in by_name:
        return by_name[key]
    close = difflib.get_close_matches(key, list(by_name), n=1, cutoff=0.85)
    return by_name[close[0]] if close else set()


def build(by_name, routes, lines):
    """Standard-schema rows: blocks along a route line with their street's schedule."""
    routes = routes.reset_index(drop=True).assign(_route=lambda d: d.index)
    blocks = citykit.routes_to_blocks(lines, routes[["_route", "geometry"]], 30, 0.5)
    listed = [_listed(k, by_name) for k in blocks["STREET_KEY"]]
    votes = collections.defaultdict(collections.Counter)
    for route, opts in zip(blocks["_route"], listed, strict=True):
        if len(opts) == 1:
            votes[route][next(iter(opts))] += 1
    words = []
    for route, opts in zip(blocks["_route"], listed, strict=True):
        fit = [s for s, _ in votes[route].most_common() if not opts or s in opts]
        words.append(next(iter(opts)) if len(opts) == 1 else fit[0] if fit else "not listed")
    return citykit.both_sides(blocks, [citykit.side_entry(w) for w in words])


def main():
    paths = [
        _DIR / f for f in ("Street-Sweeping-List.pdf", "routes.geojson", "tl_2026_06013_edges.zip")
    ]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {_DIR}; run rebuild_city_data.py oakley")
    by_name = schedules(pdf_city.pdf_lines(paths[:1]))
    print(f"  {len(by_name)} streets in the city's list")
    out = build(
        by_name, geopandas.read_file(paths[1]), citykit.centerlines(paths[2], "contra_costa")
    )
    pdf_city.write(out, _DIR / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
