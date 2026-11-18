#!/usr/bin/env python3
"""
Build data/corte_madera/StreetSweeping.geojson from Mill Valley Refuse Service's sweeping
lists (scripts/mvrs.py), the Town's own public works schedule (DPW_PDF) and the county's road
centerlines (fetched by scripts/rebuild_city_data.py corte_madera).

Usage (from repo root):  python scripts/build_corte_madera_geojson.py
Output: data/corte_madera/StreetSweeping.geojson

"Street sweeping is conducted by Mill Valley Refuse Service (MVRS) and by our Department of
Public Works (DPW). This means your street is swept twice a month."
"""

import io
import re

import mvrs
import pdf_city
import requests

DPW_PDF = "https://www.townofcortemadera.org/DocumentCenter/View/4722"
# "Tamal Vista Blvd., Tamalpais Dr. and the highway overpass ... are swept every Monday."
EVERY = dict.fromkeys(("Tamal Vista Blvd", "Tamalpais Dr"), "Every Monday")
_BULLET = ""


def dpw_rows(lines) -> list[tuple[str, str]]:
    """(street as listed, "3rd Monday of the Month (Town crew, fall and winter)") from the
    PDF's two-column bullet lists; a wrapped "(from ... to ...)" rejoins its street."""
    out: list[tuple[str, str]] = []
    head = None
    for line in lines:
        if m := re.match(r"Streets Swept on the (.+)", line):
            head = f"{m[1]} (Town crew, fall and winter)"
            continue
        if head is None:
            continue
        lead, *items = line.split(_BULLET)
        if lead.strip():
            for k in range(len(out) - 1, -1, -1):
                if out[k][0].count("(") > out[k][0].count(")"):
                    out[k] = (f"{out[k][0]} {lead.strip()}", out[k][1])
                    break
        out += [(item.strip(), head) for item in items if item.strip()]
    return out


def main():
    resp = requests.get(DPW_PDF, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    dpw = dpw_rows(pdf_city.pdf_lines([io.BytesIO(resp.content)]))
    if len(dpw) < 50:
        raise SystemExit(f"{DPW_PDF}: {len(dpw)} streets; has the schedule changed?")
    mvrs.build("corte_madera", "Corte Madera", EVERY, dpw)


if __name__ == "__main__":
    main()
