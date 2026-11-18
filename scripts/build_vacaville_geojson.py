#!/usr/bin/env python3
"""
Build data/vacaville/StreetSweeping.geojson from Recology's 2026 sweeping layer
(fetched by scripts/rebuild_city_data.py vacaville).

Usage (from repo root):  python scripts/build_vacaville_geojson.py
Input:  data/vacaville/schedule.geojson  Solano County centerlines with SweepDay, SweepFreq
Output: data/vacaville/StreetSweeping.geojson

"Weeks 1 & 3" / "Weeks 2 & 4" are Recology's green / yellow areas, swept every other
week (recology.com calendar), so they become DATES codes; "Weekly" streets keep a weekday
code. Both sides share the schedule.
"""

import datetime
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster.config import REPO_ROOT

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
# Monday of a sweep week of each area: the recology.com street sweeping calendar.
_FIRST_WEEK = {
    "Weeks 1 & 3": ("green", datetime.date(2022, 1, 10)),
    "Weeks 2 & 4": ("yellow", datetime.date(2022, 1, 3)),
}
_RANGES = ("fromaddr_l", "toaddr_l", "fromaddr_r", "toaddr_r")


def entry(day: str, freq: str, years) -> tuple[str, str]:
    """(code, desc) of a segment's SweepDay and SweepFreq."""
    if day not in _WEEKDAYS:
        raise ValueError(f"unknown SweepDay {day!r}")
    if freq == "Weekly":
        return citykit.code_from_text(f"{day} weekly")
    if freq not in _FIRST_WEEK:
        raise ValueError(f"unknown SweepFreq {freq!r}")
    color, monday = _FIRST_WEEK[freq]
    first = monday + datetime.timedelta(_WEEKDAYS.index(day))
    code = citykit.dates_code(citykit.cycle_dates(first, 2, years))
    return code, f"Every other {day[:3]}, {color} weeks"


def rows(src: geopandas.GeoDataFrame, years) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept segment (freeways and ramps left out)."""
    src = src[~src["roadclass"].isin(("PRIMARY", "RAMP"))]
    entries = [entry(d, f, years) for d, f in zip(src["SweepDay"], src["SweepFreq"], strict=True)]
    out = geopandas.GeoDataFrame(
        {"STREET_NAME": src["fulladdr_l"].fillna("").str.split().str.join(" ").str.upper()},
        geometry=src.geometry,
        crs="EPSG:4326",
    )
    for col, raw in zip(("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"), _RANGES, strict=True):
        n = pd.to_numeric(src[raw], errors="coerce")
        out[col] = n.where(n > 0)
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"] = [e[0] for e in entries]
        out[f"DESC_{side}"] = [e[1] for e in entries]
        out[f"TIME_{side}"] = None
    return out[out["STREET_NAME"] != ""].reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "vacaville"
    src = geopandas.read_file(data_dir / "schedule.geojson")
    print(f"  {len(src)} segments in Recology's layer")
    pdf_city.write(rows(src, citykit.sweep_years()), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
