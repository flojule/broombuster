#!/usr/bin/env python3
"""
Build data/belvedere/StreetSweeping.geojson from the city's sweeping page and the county's
road centerlines (fetched by scripts/rebuild_city_data.py belvedere).

Usage (from repo root):  python scripts/build_belvedere_geojson.py
Input:  PAGE (four routes, route N swept in week N on Thursday or Friday)
        data/belvedere/centerlines.geojson
Output: data/belvedere/StreetSweeping.geojson

The page asks nothing of parked cars and no sign is posted: every swept block is COURTESY.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pdf_city
import requests

from broombuster.config import REPO_ROOT

PAGE = "https://cityofbelvedere.gov/street-sweeping/"
_ROUTE_RE = re.compile(r"Route (\d) \(week (\d)\)(?:</\w+>)?:\s*([^<]+)")
_ORD = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th"}
# The sweeping map splits Beach Rd: Lower (route 1, by the cove) below this house number.
_UPPER_BEACH_FROM = 120


def routes(html: str) -> dict[str, list[str]]:
    """{street as listed: [schedule words]} from the page's "Route N (week N): a, b, and c"."""
    out: dict[str, list[str]] = {}
    for _route, week, names in _ROUTE_RE.findall(html):
        for name in re.split(r",\s*(?:and\s+)?|\s+and\s+", names.strip().rstrip(".")):
            if name.strip():
                out.setdefault(name.strip(), []).append(f"{_ORD[week]} Thursday or Friday")
    return out


def join(listed: dict[str, list[str]], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: every block of a listed street (Lower / Upper Beach Rd by number)."""
    words: dict[int, list[str]] = {}
    for name, texts in listed.items():
        level = re.match(r"(Lower|Upper) (.*)", name)
        hit = citykit.blocks_named(lines, level[2] if level else name)
        if level:
            top = hit[["L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"]].max(axis=1).fillna(0)
            hit = hit[(top >= _UPPER_BEACH_FROM) == (level[1] == "Upper")]
        if hit.empty:
            print(f"  ⚠  {name}: no county centerline")
        for i in hit.index:
            words.setdefault(i, []).extend(texts)
    out = lines.loc[sorted(words)].copy()
    entries = [citykit.courtesy_entry(words[i]) for i in out.index]
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"], out[f"DESC_{side}"], out[f"TIME_{side}"] = zip(*entries, strict=True)
    return out.drop(columns=["STREET_KEY"]).reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "belvedere"
    resp = requests.get(PAGE, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    listed = routes(resp.text)
    if len(listed) < 20:
        raise SystemExit(f"{PAGE}: found {len(listed)} streets; has the page changed?")
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "marin")
    pdf_city.write(join(listed, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
