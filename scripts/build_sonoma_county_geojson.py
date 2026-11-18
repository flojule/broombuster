#!/usr/bin/env python3
"""
Build data/sonoma_county/StreetSweeping.geojson from Recology's 2026 sweeping layer for
unincorporated Sonoma County (fetched by scripts/rebuild_city_data.py sonoma_county).

Usage (from repo root):  python scripts/build_sonoma_county_geojson.py
Input:  data/sonoma_county/schedule.geojson  street lines with Day_Week, Week
Output: data/sonoma_county/StreetSweeping.geojson

Week 1-4 is a slot of a 4-week rotation (Recology's 2026 calendar), not a week of the
month, so those streets get DATES codes; "1234" streets are swept every week. The source
has no address ranges; both sides share the schedule.
"""

import datetime
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

# First 2026 sweep day of each (weekday, week) slot: Recology's 2026 calendar colors
# matched to the layer's renderer.
_FIRST = {
    ("Monday", "1"): datetime.date(2026, 1, 19),
    ("Monday", "2"): datetime.date(2026, 1, 26),
    ("Monday", "3"): datetime.date(2026, 1, 5),
    ("Monday", "4"): datetime.date(2026, 1, 12),
    ("Tuesday", "1"): datetime.date(2026, 1, 13),
    ("Tuesday", "2"): datetime.date(2026, 1, 20),
    ("Tuesday", "3"): datetime.date(2026, 1, 27),
    ("Tuesday", "4"): datetime.date(2026, 1, 6),
    ("Wednesday", "1"): datetime.date(2026, 1, 21),
    ("Wednesday", "2"): datetime.date(2026, 1, 28),
    ("Wednesday", "3"): datetime.date(2026, 1, 7),
    ("Wednesday", "4"): datetime.date(2026, 1, 14),
}


def entry(day: str, week: str, years) -> tuple[str, str]:
    """(code, desc) of a line's Day_Week and Week."""
    if week == "1234":
        return citykit.code_from_text(f"{day} weekly")
    if (day, week) not in _FIRST:
        raise ValueError(f"unknown slot {day!r} week {week!r}")
    code = citykit.dates_code(citykit.cycle_dates(_FIRST[day, week], 4, years))
    return code, f"{day[:3]} every 4 weeks"


def rows(src: geopandas.GeoDataFrame, years) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept line."""
    entries = [entry(d, w, years) for d, w in zip(src["Day_Week"], src["Week"], strict=True)]
    out = geopandas.GeoDataFrame(
        {"STREET_NAME": src["st_name"].fillna("").str.split().str.join(" ").str.upper()},
        geometry=src.geometry,
        crs="EPSG:4326",
    )
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"] = [e[0] for e in entries]
        out[f"DESC_{side}"] = [e[1] for e in entries]
        out[f"TIME_{side}"] = None
    return out[out["STREET_NAME"] != ""].reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "sonoma_county"
    src = geopandas.read_file(data_dir / "schedule.geojson")
    print(f"  {len(src)} lines in Recology's layer")
    pdf_city.write(rows(src, citykit.sweep_years()), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
