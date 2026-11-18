#!/usr/bin/env python3
"""
Build data/milpitas/StreetSweeping.geojson from the city's residential sweeping routes and the
county's street centerlines (both fetched by scripts/rebuild_city_data.py milpitas).

Usage (from repo root):  python scripts/build_milpitas_geojson.py
Input:  data/milpitas/routes.geojson       route lines: ResSweepDay ("A- Monday 1st & 3rd Week")
        data/milpitas/centerlines.geojson  county centerlines in the city
Output: data/milpitas/StreetSweeping.geojson

Each county block along a route line takes its schedule on both sides. A boundary street
("B-Tuesday Left D-Tuesday Right") takes both routes' schedules on both sides. No hours
are published, so TIME is empty.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster.config import REPO_ROOT

_ROUTE_RE = re.compile(r"\b([A-J])\s*-")


def route_schedules(routes: pd.DataFrame) -> dict[str, str]:
    """{route letter: schedule words} from the single-route rows: "A" -> "Monday 1st & 3rd"."""
    out = {}
    for text in routes["ResSweepDay"].dropna().unique():
        letters = _ROUTE_RE.findall(text)
        if len(letters) == 1:
            out[letters[0]] = clean(text)
    return out


def clean(text: str) -> str:
    """Schedule words of one route label: "H- Wednsday 2nd & 4th Week" -> "Wednesday 2nd & 4th"."""
    return _ROUTE_RE.sub(" ", text).replace("Wednsday", "Wednesday").replace("Week", "").strip()


def build(routes, lines) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per county block and route schedule along it."""
    sched = route_schedules(routes)
    rows = routes.assign(_words=routes["ResSweepDay"].map(
        lambda t: [sched[x] for x in _ROUTE_RE.findall(t or "")]))  # fmt: skip
    rows = rows.explode("_words").dropna(subset=["_words"])
    along = citykit.lines_along(lines, rows[["_words", "STREETNAME", "geometry"]], "STREETNAME")
    along = along.drop_duplicates(subset=["_src", "_words"])
    entries = [citykit.side_entry(w) for w in along["_words"]]
    for side in ("EVEN", "ODD"):
        along[[f"DAY_{side}", f"DESC_{side}", f"TIME_{side}"]] = entries
    along["STREET_NAME"] = along["STREET_NAME"].map(pdf_city.street_label)
    keep = ["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]
    keep += [f"{p}_{s}" for p in ("DAY", "DESC", "TIME") for s in ("EVEN", "ODD")]
    return geopandas.GeoDataFrame(along[keep], crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "milpitas"
    paths = [data_dir / f"{n}.geojson" for n in ("routes", "centerlines")]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py milpitas")
    routes = geopandas.read_file(paths[0])
    lines = citykit.centerlines(paths[1], "santa_clara")
    print(f"  {len(routes)} route lines, {len(lines)} county blocks")
    pdf_city.write(build(routes, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
