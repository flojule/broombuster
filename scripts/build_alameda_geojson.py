#!/usr/bin/env python3
"""
Build data/alameda/StreetSweeping.geojson from Alameda's PDF schedule.

Usage (from repo root):  python scripts/build_alameda_geojson.py
Input:  data/alameda/street-sweeping-schedule.pdf   (manual download; see manifest)
Output: data/alameda/StreetSweeping.geojson
Requires: uv sync --extra scripts
"""

import re
from collections import defaultdict
from pathlib import Path

import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

# BLOCK  STREET  SIDE  DAY  ROUTE  FREQUENCY  SIGNED  BIWEEKLY  TIME  ORDER, e.g.
#   2800 ADAMS ST EVEN FRIDAY 63 Weekly YES NO 12:00 PM • 3:00 PM 3337
#   2200 ALAMEDA AVE EVEN ALL 62 ALL YES NO 4:00AM • 5:30 AM 998
# MEDIAN rows are skipped.
_ROW_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+(EVEN|ODD)"
    r"\s+(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY|ALL)"
    r"\s+\d+\s+\S+\s+(?:YES|NO)\s+(YES|NO)\s+(.+?)\s+\d+$",
    re.IGNORECASE,
)


def blocks_from(lines) -> dict:
    """Rows keyed by (street, block), both sides merged; ALL = every day."""
    blocks: dict = defaultdict(lambda: {"even": None, "odd": None})
    for line in lines:
        m = None if "MEDIAN" in line.upper() else _ROW_RE.match(line)
        if not m:
            continue
        block, street, side, day, biweekly, time_raw = m.groups()
        block, street, day = int(block), street.strip().upper(), day.upper()
        if day == "ALL":
            code, desc = "E", "Every day"
        else:
            disp = day[:3].title()
            code, desc = pdf_city.WEEKDAY_TOKEN[disp] + "E", f"Every {disp}"
        if biweekly.upper() == "YES":
            desc += " (biweekly)"
        b = blocks[(street, block)]
        b.update(street=street, addr=(block, block + 98, block + 1, block + 99))
        b[side.lower()] = (code, desc, normalize.time_display(time_raw))
    return blocks


def main():
    data_dir = Path(REPO_ROOT) / "data" / "alameda"
    pdf = data_dir / "street-sweeping-schedule.pdf"
    if not pdf.exists():
        raise FileNotFoundError(f"{pdf} missing; see source.notes in data/manifests/alameda.yaml")
    blocks = blocks_from(pdf_city.pdf_lines([pdf]))
    print(f"  {len(blocks)} blocks parsed")
    gdf = pdf_city.join_blocks(blocks, pdf_city.fetch_osm_streets("Alameda"))
    pdf_city.write(gdf, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
