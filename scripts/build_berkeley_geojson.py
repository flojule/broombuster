#!/usr/bin/env python3
"""
Build data/berkeley/StreetSweeping.geojson from Berkeley's PDF schedules.

Usage (from repo root):  python scripts/build_berkeley_geojson.py
Input:  data/berkeley/*.pdf   (fetched by scripts/rebuild_city_data.py berkeley)
Output: data/berkeley/StreetSweeping.geojson
Requires: uv sync --extra scripts

Rows read "1st Fri" etc.: the nth weekday of every month, encoded as the
recurring week-of-month code ("F1"), so the data never expires at year end.
"""

import re
from collections import defaultdict
from pathlib import Path

import pdf_city

from broombuster.config import REPO_ROOT

# e.g. "61 Acroft Ct S 1400 1498 1st Fri AM Acton Terminus"
_ROW_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+(N|S|E|W)\s+(\d+)\s+(\d+)\s+"
    r"(1st|2nd|3rd|4th)\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(AM|PM)",
    re.IGNORECASE,
)


def blocks_from(lines) -> dict:
    """Rows keyed by (street, even-based address range); S/W side is even.

    Address numbers are rounded down to even so complementary blocks (1400-1498
    S-side, 1401-1499 N-side) merge into one row carrying both sides.
    """
    blocks: dict = defaultdict(lambda: {"even": None, "odd": None})
    for line in lines:
        m = _ROW_RE.match(line)
        if not m:
            continue
        _, street, side, lo, hi, nth, day, ampm = m.groups()
        lo, hi = int(lo) & ~1, int(hi) & ~1
        street = street.strip().upper()
        day = day.capitalize()
        b = blocks[(street, lo, hi)]
        b.update(street=street, addr=(lo, hi, lo + 1, hi + 1))
        nth = nth.lower()
        code = pdf_city.WEEKDAY_TOKEN[day] + nth[0]
        b["even" if side.upper() in ("S", "W") else "odd"] = (code, f"{nth} {day}", ampm.upper())
    return blocks


def main():
    data_dir = Path(REPO_ROOT) / "data" / "berkeley"
    pdfs = sorted(data_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs in {data_dir}; run scripts/rebuild_city_data.py berkeley")
    blocks = blocks_from(pdf_city.pdf_lines(pdfs))
    print(f"  {len(blocks)} blocks parsed from {len(pdfs)} PDF(s)")
    gdf = pdf_city.join_blocks(blocks, pdf_city.fetch_osm_streets("Berkeley"))
    pdf_city.write(gdf, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
