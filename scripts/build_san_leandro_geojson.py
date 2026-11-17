#!/usr/bin/env python3
"""
Build data/san_leandro/StreetSweeping.geojson from the city's sweeping layers and the
county's street centerlines (all fetched by scripts/rebuild_city_data.py san_leandro).

Usage (from repo root):  python scripts/build_san_leandro_geojson.py
Input:  data/san_leandro/zones.geojson        residential zones, "3rd Thursday" each
        data/san_leandro/commercial.geojson   commercial route lines, "Monday, Wednesday, Friday"
        data/san_leandro/posted.geojson       streets with posted alternate-side signs
        data/san_leandro/centerlines.geojson  county blocks with address ranges
Output: data/san_leandro/StreetSweeping.geojson

A block takes its commercial route's days, else (posted street) the check-the-sign code,
else its residential zone's day; both sides alike. "Every other" weeks are check-the-sign.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

_WEEKDAYS = "Monday Tuesday Wednesday Thursday Friday"


def schedule_words(text: str) -> str:
    """Schedule words with a weekday range spelled out: "Monday - Friday" -> every weekday."""
    return re.sub(r"\bmonday\s*-\s*friday\b", _WEEKDAYS, str(text), flags=re.I)


def build(lines, zones, commercial, posted) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per swept block."""
    lines = lines.reset_index(drop=True)
    entry = {}
    zoned = citykit.zones_to_blocks(lines.assign(_i=lines.index), zones[["SCHED", "geometry"]])
    for i, sched in zip(zoned["_i"], zoned["SCHED"], strict=True):
        entry[i] = citykit.side_entry(sched)
    for i, _ in citykit.blocks_along(lines, posted):
        entry[i] = (citykit.UNKNOWN, "Posted streets: check the sign", None)
    for i, r in citykit.blocks_along(lines, commercial):
        entry[i] = citykit.side_entry(schedule_words(commercial.loc[r, "SWEEPING_SCHEDULE"]))
    out = lines.loc[sorted(entry)].copy()
    for side in ("EVEN", "ODD"):
        for k, col in enumerate(("DAY", "DESC", "TIME")):
            out[f"{col}_{side}"] = [entry[i][k] for i in out.index]
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_leandro"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "alameda")
    zones, commercial, posted = (
        geopandas.read_file(data_dir / f"{n}.geojson") for n in ("zones", "commercial", "posted")
    )
    out = build(lines, zones, commercial, posted)
    print(f"  {len(out)} of {len(lines)} blocks swept")
    pdf_city.write(out, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
