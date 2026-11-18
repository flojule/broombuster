#!/usr/bin/env python3
"""
Build data/hayward/StreetSweeping.geojson from the city's schedule PDF and the county's
street centerlines (both fetched by scripts/rebuild_city_data.py hayward).

Usage (from repo root):  python scripts/build_hayward_geojson.py
Input:  data/hayward/Street-Sweeping-Schedule-2025-Updated-250507.pdf
            "300 CENTRAL BLVD 2 999998 EVEN 2ND & 4TH THU AS POSTED"
        data/hayward/centerlines.geojson  county blocks with address ranges
Output: data/hayward/StreetSweeping.geojson

Each block side takes the narrowest PDF range of its street and parity (ODD, EVEN or
BOTH). The posted window is 7:00-11:30 or 11:30-2:30 by neighborhood and not in the
PDF, so the whole day counts. "Every other" weeks are check-the-sign.
"""

import re
import sys
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

PDF = "Street-Sweeping-Schedule-2025-Updated-250507.pdf"
_ROW_RE = re.compile(
    r"^\d+\s+(?P<name>.+?)\s+(?P<lo>\d+)\s+(?P<hi>\d+)(?:\s+\(.*?\))?\s+"
    r"(?:(?:[NSEW]S?|CL)\s+)?(?P<dir>ODD|EVEN|BOTH)\s+(?P<sched>.+?)\s+(?:ANYTIME|AS POSTED)$",
    re.I,
)
_DAY_ABBR = {"M": "Mon", "T": "Tue", "TU": "Tue", "W": "Wed", "TH": "Thu", "F": "Fri"}
_WHOLE = 99999  # an end number this high: the whole street
_ORDINAL = {"FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH"}
_ORDINAL |= {"SIXTH": "6TH", "SEVENTH": "7TH", "SAINT": "ST"}


def street_key(name: str) -> str:
    """PDF and county names alike: "ST ANDREWS ST", "SAINT ANDREWS ST" -> "STANDREWS"."""
    toks = normalize.street_name(pdf_city.street_label(name)).split()
    return re.sub(r"[^A-Z0-9]", "", "".join(_ORDINAL.get(t, t) for t in toks))


def schedule_words(sched: str) -> str:
    """PDF schedule words for code_from_text: "EVERY M,W,F" -> "EVERY Mon,Wed,Fri"."""
    s = re.sub(r"\bEVERYOTHER\b", "every other", sched, flags=re.I)
    return re.sub(r"\b(TH|TU|M|T|W|F)\b", lambda m: _DAY_ABBR[m[1].upper()], s)


def rows_from(lines) -> list[dict]:
    """Schedule rows of the PDF text lines (headers, unswept and red-curb rows skipped)."""
    out = []
    for line in lines:
        m = _ROW_RE.match(line)
        if not m:
            continue
        name = re.sub(r"\(.*?\)", " ", m["name"])
        out.append(
            {
                "key": street_key(name),
                "lo": int(m["lo"]),
                "hi": int(m["hi"]),
                "dir": m["dir"].upper(),
                "entry": citykit.side_entry(schedule_words(m["sched"])),
            }
        )
    return out


def join(rows: list[dict], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: each county block with a schedule on at least one side."""
    by_key = {}
    for r in rows:
        by_key.setdefault(r["key"], []).append(r)
    out = []
    for _, blk in lines.iterrows():
        cand = by_key.get(street_key(blk["STREET_NAME"]), [])
        rec = blk.to_dict()
        for side, par in (citykit.parity_sides(rec) or {"L": "EVEN", "R": "ODD"}).items():
            nums = [rec[f"{side}_F_ADD"], rec[f"{side}_T_ADD"]]
            lo, hi = (min(nums), max(nums)) if all(n == n and n for n in nums) else (0, 0)
            fits = [
                r
                for r in cand
                if r["dir"] in (par, "BOTH")
                and (r["lo"] <= hi and r["hi"] >= lo if hi else r["hi"] >= _WHOLE)
            ]
            if fits:
                code, desc, time = min(fits, key=lambda r: r["hi"] - r["lo"])["entry"]
                rec[f"DAY_{par}"], rec[f"DESC_{par}"], rec[f"TIME_{par}"] = code, desc, time
        if rec.get("DAY_EVEN") or rec.get("DAY_ODD"):
            out.append(rec)
    gdf = geopandas.GeoDataFrame(out, geometry="geometry", crs="EPSG:4326")
    gdf["STREET_NAME"] = gdf["STREET_NAME"].map(pdf_city.street_label)
    return gdf


def main():
    data_dir = Path(REPO_ROOT) / "data" / "hayward"
    rows = rows_from(pdf_city.pdf_lines([data_dir / PDF]))
    if len(rows) < 1000:
        sys.exit(f"{PDF}: only {len(rows)} schedule rows read; has the layout changed?")
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "alameda")
    out = join(rows, lines)
    unused = {r["key"] for r in rows} - set(lines["STREET_NAME"].map(street_key))
    print(f"  {len(rows)} PDF rows, {len(out)} of {len(lines)} blocks swept")
    print(f"  {len(unused)} PDF streets without a county centerline: {sorted(unused)[:20]}")
    pdf_city.write(out, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
