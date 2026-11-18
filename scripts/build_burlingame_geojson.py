#!/usr/bin/env python3
"""
Build data/burlingame/StreetSweeping.geojson from the city's sweeping sections and the
county centerlines (both fetched by scripts/rebuild_city_data.py burlingame).

Usage (from repo root):  python scripts/build_burlingame_geojson.py
Input:  data/burlingame/sections.geojson     section polygons: weekday, hours, track
        data/burlingame/centerlines.geojson  San Mateo County centerlines in Burlingame
Output: data/burlingame/StreetSweeping.geojson

Residential sections (tracks A and B: weekly Oct–Mar, every other week Apr–Sep) are a
courtesy sweep (NT). Weekly sections (WK) and the Mon–Sat downtown sections keep the
normal code: the city does not say they go unticketed. Both sides alike.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT


def entry(row) -> tuple:
    """(code, desc, time) of a section."""
    day = normalize.clean_text(row["Day"]).replace("MON-SAT", "MON TUE WED THU FRI SAT")
    hours = normalize.clean_text(row["Time"]).replace("11m", "11am")
    track = normalize.clean_text(row["Track"]).upper()
    if track in ("A", "B"):
        words = citykit.code_from_text(day)[1].removeprefix("Every ")
        desc = f"{words}: weekly Oct–Mar, every other week Apr–Sep (track {track})"
        return citykit.COURTESY, f"{desc}, {citykit.time_text(hours)}, no tickets", None
    return citykit.side_entry(day, hours)


def build(zones: geopandas.GeoDataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks inside a section."""
    out = citykit.zone_blocks(lines, zones, entry)
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "burlingame"
    inputs = [data_dir / "sections.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(
            f"inputs missing in {data_dir}; run rebuild_city_data.py burlingame"
        )
    zones = geopandas.read_file(inputs[0])
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(build(zones, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
