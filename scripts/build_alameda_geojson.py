#!/usr/bin/env python3
"""
Build data/alameda/StreetSweeping.geojson from Alameda's PDF schedule.

Usage (from repo root):
    python scripts/build_alameda_geojson.py

Input:  data/alameda/street-sweeping-schedule.pdf
Output: data/alameda/StreetSweeping.geojson

Requires:
    pip install pdfplumber geopandas shapely requests
"""

import os
import pathlib
import re
import sys
from collections import defaultdict

# Allow importing from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import geopandas
import pdfplumber
import requests
from shapely.geometry import LineString, mapping

from broombuster import normalize as _normalize

# ---------------------------------------------------------------------------
# Day-name → Oakland-style sweep code
# ---------------------------------------------------------------------------

_DAY_CODE = {
    "MONDAY":    "ME",
    "TUESDAY":   "TE",
    "WEDNESDAY": "WE",
    "THURSDAY":  "THE",
    "FRIDAY":    "FE",
    "SATURDAY":  "SE",
    "SUNDAY":    "SUE",
    "ALL":       "E",
}

_DAY_LABEL = {
    "ME": "Mon", "TE": "Tue", "WE": "Wed", "THE": "Thu",
    "FE": "Fri", "SE": "Sat", "SUE": "Sun", "E": "Every day",
}


def _time_clean(raw: str) -> str:
    """Normalise OCR-damaged time strings like '12:00 PM • 3:00 PM' → '12PM–3PM'."""
    # Strip the separator (bullet, dash, dot) and surrounding noise
    m = re.search(
        r"(\d{1,2}(?::\d{2})?)\s*(AM|PM)[^A-Z0-9]*(\d{1,2}(?::\d{2})?)\s*(AM|PM)",
        raw, re.IGNORECASE,
    )
    if not m:
        return raw.strip()
    def _fmt(hm, ap):
        h = int(hm.split(":")[0])
        return f"{h}{ap.upper()}"
    return f"{_fmt(m.group(1), m.group(2))}–{_fmt(m.group(3), m.group(4))}"


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

# Schema: BLOCK  STREET  SIDE  DAY  ROUTE  FREQUENCY  SIGNED  BIWEEKLY  TIME  ORDER
# Examples:
#   2800 ADAMS ST EVEN FRIDAY 63 Weekly YES NO 12:00 PM • 3:00 PM 3337
#   2200 ALAMEDA AVE EVEN ALL 62 ALL YES NO 4:00AM • 5:30 AM 998
#   3100 CENTRAL AVE EVEN FRIDAY 63 Weekly YES YES 10:00 AM-1:00PM 3226  ← BIWEEKLY=YES
#   700 ATLANTIC AVE MEDIAN EVEN THURSDAY 62 Weekly NO NO 6:00 AM -9:00 AM 2149  ← skip MEDIAN rows

_ROW_RE = re.compile(
    r"^(\d+)"                                              # BLOCK
    r"\s+(.+?)"                                            # STREET  (lazy)
    r"\s+(EVEN|ODD)"                                       # SIDE
    r"\s+(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY|ALL)"  # DAY
    r"\s+\d+"                                              # ROUTE
    r"\s+\S+"                                              # FREQUENCY
    r"\s+(YES|NO)"                                         # SIGNED
    r"\s+(YES|NO)"                                         # BIWEEKLY
    r"\s+(.+?)"                                            # TIME
    r"\s+\d+$",                                            # ORDER
    re.IGNORECASE,
)


def parse_pdf(pdf_path: pathlib.Path) -> list:
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                # Skip MEDIAN rows and the header
                if "MEDIAN" in line.upper() or line.upper().startswith("BLOCK"):
                    continue
                m = _ROW_RE.match(line)
                if not m:
                    continue
                block, street, side, day, _signed, biweekly, time_raw = m.groups()
                records.append({
                    "block":     int(block),
                    "street":    street.strip().upper(),
                    "side":      side.upper(),
                    "day":       day.upper(),
                    "biweekly":  biweekly.upper() == "YES",
                    "time":      _time_clean(time_raw),
                })
    return records


# ---------------------------------------------------------------------------
# OSM street geometry
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    return _normalize.street_name(name)


def fetch_alameda_streets() -> geopandas.GeoDataFrame:
    """Fetch named streets in Alameda (the city) via the Overpass API."""
    query = """
    [out:json][timeout:180];
    area["name"="Alameda"]["admin_level"="8"]["boundary"="administrative"]->.city;
    (
      way["highway"~"^(residential|primary|secondary|tertiary|unclassified|living_street)$"]
         ["name"](area.city);
    );
    out geom;
    """
    print("  Querying Overpass API …")
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=200,
    )
    resp.raise_for_status()

    features = []
    for el in resp.json().get("elements", []):
        if "geometry" not in el:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": mapping(LineString(coords)),
            "properties": {
                "osm_id": el["id"],
                "name":   el.get("tags", {}).get("name", "").upper(),
            },
        })

    gdf = geopandas.GeoDataFrame.from_features(features, crs="EPSG:4326")
    return gdf


# ---------------------------------------------------------------------------
# Join schedule → geometry
# ---------------------------------------------------------------------------

def build_geojson(
    records: list,
    streets_gdf: geopandas.GeoDataFrame,
) -> geopandas.GeoDataFrame:
    streets_gdf = streets_gdf.copy()
    streets_gdf["_norm"] = streets_gdf["name"].apply(_norm)

    # Group records by (street, block) merging even/odd sides
    blocks: dict = defaultdict(lambda: {"even": None, "odd": None})
    for r in records:
        parity = "even" if r["side"] == "EVEN" else "odd"
        key    = (r["street"], r["block"])
        code   = _DAY_CODE.get(r["day"], "E")
        label  = _DAY_LABEL.get(code, code)
        desc   = f"Every {label}" if code != "E" else "Every day"
        if r["biweekly"]:
            desc += " (biweekly)"
        blocks[key][parity] = (code, desc, r["time"])

    rows = []
    no_geom = 0
    for (street, block), sides in blocks.items():
        norm = _norm(street)
        hits = streets_gdf[streets_gdf["_norm"] == norm]
        if hits.empty:
            hits = streets_gdf[streets_gdf["_norm"].str.startswith(norm)]
        if hits.empty:
            no_geom += 1
            continue

        # Union all matching OSM ways for full street coverage
        geom = hits.geometry.union_all()
        even = sides["even"] or (None, None, None)
        odd  = sides["odd"]  or (None, None, None)

        rows.append({
            "geometry":    geom,
            "STREET_NAME": street,
            "DAY_EVEN":    even[0], "DESC_EVEN": even[1], "TIME_EVEN": even[2],
            "DAY_ODD":     odd[0],  "DESC_ODD":  odd[1],  "TIME_ODD":  odd[2],
            "L_F_ADD":     float(block),
            "L_T_ADD":     float(block + 98),
            "R_F_ADD":     float(block + 1),
            "R_T_ADD":     float(block + 99),
        })

    if no_geom:
        print(f"  ⚠  {no_geom} blocks had no matching OSM geometry and were dropped.")

    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    data_dir  = repo_root / "data" / "alameda"
    out_path  = data_dir / "StreetSweeping.geojson"

    pdf_path = data_dir / "street-sweeping-schedule.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF not found: {pdf_path}\n"
            "Download from:\n"
            "  https://www.alamedaca.gov/Residents/Transportation-and-Streets/"
            "Street-Sweeping-Schedule\n"
            "and save as data/alameda/street-sweeping-schedule.pdf"
        )

    print("Parsing PDF …")
    records = parse_pdf(pdf_path)
    print(f"  {len(records)} schedule records parsed")

    print("Fetching Alameda street geometry from OpenStreetMap …")
    streets_gdf = fetch_alameda_streets()
    print(f"  {len(streets_gdf)} street ways fetched")

    print("Joining schedule to geometry …")
    result = build_geojson(records, streets_gdf)
    print(f"  {len(result)} segments in output")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(str(out_path), driver="GeoJSON")
    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
