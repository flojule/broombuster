#!/usr/bin/env python3
"""
Build data/sausalito/StreetSweeping.geojson from the city's sweeping table and the county's
road centerlines (fetched by scripts/rebuild_city_data.py sausalito).

Usage (from repo root):  python scripts/build_sausalito_geojson.py
Input:  PAGE (table: street, or street - limits | "Monday - Week 1" or "Tuesday - Weekly")
        data/sausalito/centerlines.geojson
Output: data/sausalito/StreetSweeping.geojson

The city asks residents to "please try to minimize on-street parking" on their day: every
swept block is COURTESY. A street listed in several rows gets all of them.
"""

import html
import re
from pathlib import Path

import citykit
import geopandas
import pdf_city
import requests

from broombuster import normalize
from broombuster.config import REPO_ROOT

PAGE = "https://www.sausalito.gov/services/streets_and_roads/street_sweeping.php"
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_WHOLE = re.compile(r"^(start to end|both sides)?\s*(\(all\))?$", re.I)
_SIDE_OF = re.compile(r"^(east|west) of (.+)$", re.I)
_BLOCKS = re.compile(r"(\d+)00 and (\d+)00 blocks", re.I)
# Table spellings the county writes differently.
_ALIASES = {"Kendall Ct": "Kendell Ct", "Eden Rock": "Eden Roc", "Williams Ct": "William Ct"}


def _text(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())


def rows(page: str) -> list[tuple[str, str, str]]:
    """(street, limits, schedule) per table row; "A and B" gives one row per street."""
    out = []
    for tr in _ROW_RE.findall(page):
        cells = [_text(c) for c in _CELL_RE.findall(tr)]
        if len(cells) != 2 or not re.search(r"week", cells[1], re.I):
            continue
        name, sched = cells
        limits = ""
        if m := re.match(r"(.*?)\s+-\s+(.*)$", name) or re.match(r"(.*?)\s*\((.*)\)$", name):
            name, limits = m[1], m[2]
        if re.search(r"parking lot", name, re.I):
            continue
        first, _, second = name.partition(" and ")
        names = [first]
        if second:  # "Buchanan Dr. and Ct." -> Buchanan Dr, Buchanan Ct
            bare_type = normalize.street_name(f"X {second}") == "X"
            names.append(f"{first.rsplit(' ', 1)[0]} {second}" if bare_type else second)
        out += [(_ALIASES.get(n.strip(" ."), n.strip(" .")), limits, sched) for n in names]
    return out


def _blocks(lines, street, limits) -> list:
    """Index of the street's blocks the limits name; all of them when unreadable."""
    hit = citykit.blocks_named(lines, street)
    if _WHOLE.match(limits):
        return list(hit.index)
    if m := _BLOCKS.search(limits):
        return list(hit.index[citykit.in_range(hit, int(m[1]) * 100, int(m[2]) * 100 + 99)])
    if m := _SIDE_OF.match(limits):
        x0 = _crossing_x(lines, street, m[2])
        if x0 is not None:
            mid_x = [g.interpolate(0.5, normalized=True).x for g in hit.geometry]
            east = m[1].lower() == "east"
            return [i for i, x in zip(hit.index, mid_x, strict=True) if (x > x0) == east]
    elif m := re.match(r"(.+?) to (.+)$", limits):
        part = citykit.between(lines, street, m[1], m[2])
        if len(part):
            return list(part)
    print(f"  ⚠  {street} ({limits}): limits not found, whole street")
    return list(hit.index)


def _crossing_x(lines, street, cross) -> float | None:
    """Longitude where `cross` meets `street`."""
    ends = {e for g in citykit.blocks_named(lines, cross).geometry for e in citykit.end_points(g)}
    for g in citykit.blocks_named(lines, street).geometry:
        for e in citykit.end_points(g):
            if e in ends:
                return e[0]
    return None


def join(table: list[tuple], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: every block a table row names, with all its rows' schedules."""
    words: dict[int, list[str]] = {}
    for street, limits, sched in table:
        idx = _blocks(lines, street, limits)
        if not idx:
            print(f"  ⚠  {street}: no county centerline")
        for i in idx:
            words.setdefault(i, []).append(sched)
    out = lines.loc[sorted(words)].copy()
    entries = [citykit.courtesy_entry(words[i]) for i in out.index]
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"], out[f"DESC_{side}"], out[f"TIME_{side}"] = zip(*entries, strict=True)
    return out.drop(columns=["STREET_KEY"]).reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "sausalito"
    resp = requests.get(PAGE, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    table = rows(resp.text)
    if len(table) < 100:
        raise SystemExit(f"{PAGE}: found {len(table)} rows; has the page changed?")
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "marin")
    pdf_city.write(join(table, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
