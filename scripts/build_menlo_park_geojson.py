#!/usr/bin/env python3
"""
Build data/menlo_park/StreetSweeping.geojson from the city's sweeping districts and the
county centerlines (both fetched by scripts/rebuild_city_data.py menlo_park).

Usage (from repo root):  python scripts/build_menlo_park_geojson.py
Input:  data/menlo_park/districts.geojson    district polygons: weekday, seasonal frequency
        data/menlo_park/centerlines.geojson  San Mateo County centerlines in Menlo Park
Output: data/menlo_park/StreetSweeping.geojson

The city posts no hours and asks residents to park off the street: swept, nobody ticketed
(NT), with the weekday and the seasonal frequency in words. Both sides alike.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

_SEASONS = (("WinterSchedule", "Nov–Feb"), ("SpringSchedule", "Mar–Apr"),
            ("SummerSchedule", "May–Sep"), ("FallSchedule", "Oct"))  # fmt: skip
_FREQ = {"ONCE PER WEEK": "weekly", "TWICE PER WEEK": "twice a week",
         "EVERY OTHER WEEK": "every 2 weeks", "EVERY FOUR WEEKS": "every 4 weeks"}  # fmt: skip


def entry(row) -> tuple:
    """(NT, desc, None): "Thu: weekly Nov–Feb, every 2 weeks Mar–Apr, ..., no tickets"."""
    day = normalize.clean_text(row["DayOfWeek"])
    if not day:
        return None, None, None
    seasons = [(_FREQ.get(normalize.clean_text(row[c]).upper(), row[c]), months)
               for c, months in _SEASONS if normalize.clean_text(row[c])]  # fmt: skip
    freqs = (
        [f"{f} {m}" for f, m in seasons] if len({f for f, _ in seasons}) > 1 else [seasons[0][0]]
    )
    days = citykit.code_from_text(day)[1].removeprefix("Every ")
    return citykit.COURTESY, f"{days}: {', '.join(freqs)}, no tickets", None


def build(zones: geopandas.GeoDataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks inside a district."""
    out = citykit.zone_blocks(lines, zones, entry)
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "menlo_park"
    inputs = [data_dir / "districts.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(
            f"inputs missing in {data_dir}; run rebuild_city_data.py menlo_park"
        )
    zones = geopandas.read_file(inputs[0])
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(build(zones, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
