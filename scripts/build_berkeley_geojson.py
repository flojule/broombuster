#!/usr/bin/env python3
"""
Build data/berkeley/StreetSweeping.geojson from Berkeley's PDF schedules.

Usage (from repo root):
    python scripts/build_berkeley_geojson.py

Input:  data/berkeley/*.pdf  (downloaded from berkeleyca.gov)
Output: data/berkeley/StreetSweeping.geojson

Requires:
    pip install pdfplumber geopandas shapely requests
"""

import datetime
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
# Schedule encoding helpers
# ---------------------------------------------------------------------------

ORDINAL_MAP = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
DAY_MAP     = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
MONTH_ABBR  = {
    1: "Jan", 2: "Feb", 3: "Mar",  4: "Apr",
    5: "May", 6: "Jun", 7: "Jul",  8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _nth_weekday_dates(ordinal: int, weekday: int, year: int) -> list:
    """Return all dates in `year` that are the nth occurrence of weekday."""
    dates = []
    for m in range(1, 13):
        first = datetime.date(year, m, 1)
        diff   = (weekday - first.weekday()) % 7
        nth    = first + datetime.timedelta(days=diff + (ordinal - 1) * 7)
        if nth.month == m:
            dates.append(nth)
    return dates


def _schedule_code_and_desc(day_of_month: str, year: int):
    """
    Convert e.g. "1st Fri" → (DATES:YYYY-MM-DD,...  code, human description).
    Returns (None, None) on parse failure.
    """
    parts = day_of_month.strip().split()
    if len(parts) != 2:
        return None, None
    ordinal_str, day_str = parts[0].capitalize(), parts[1].capitalize()
    ordinal = ORDINAL_MAP.get(ordinal_str)
    weekday = DAY_MAP.get(day_str)
    if ordinal is None or weekday is None:
        return None, None

    dates = _nth_weekday_dates(ordinal, weekday, year)
    code  = "DATES:" + ",".join(d.isoformat() for d in dates)

    today = datetime.date.today()
    future_months: list = []
    for d in sorted(dates):
        key = (d.year, d.month)
        if d >= today and key not in future_months:
            future_months.append(key)
    target = set(future_months[:2])
    shown: dict = {}
    for d in sorted(dates):
        if (d.year, d.month) in target:
            shown.setdefault(d.month, []).append(str(d.day))
    if not shown:
        first_months: list = []
        for d in sorted(dates):
            key = (d.year, d.month)
            if key not in first_months:
                first_months.append(key)
        for d in sorted(dates):
            if (d.year, d.month) in set(first_months[:2]):
                shown.setdefault(d.month, []).append(str(d.day))
    desc = f"{ordinal_str} {day_str}: " + "; ".join(
        f"{MONTH_ABBR[m]} {', '.join(days)}" for m, days in sorted(shown.items())
    )
    return code, desc


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

# Matches lines like:
#   61 Acroft Ct S 1400 1498 1st Fri AM Acton Terminus
#   22 10th Street E 2001 2499 2nd Mon AM University Dwight
_ROW_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+(N|S|E|W)\s+(\d+)\s+(\d+)\s+"
    r"(1st|2nd|3rd|4th)\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(AM|PM)",
    re.IGNORECASE,
)


def parse_pdf(pdf_path: pathlib.Path) -> list:
    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                m = _ROW_RE.match(line.strip())
                if m:
                    _, street, side, from_addr, to_addr, ordinal, day, ampm = m.groups()
                    records.append({
                        "street":       street.strip().upper(),
                        "side":         side.upper(),
                        "from_addr":    int(from_addr),
                        "to_addr":      int(to_addr),
                        "day_of_month": f"{ordinal.capitalize()} {day.capitalize()}",
                        "ampm":         ampm.upper(),
                    })
    return records


# ---------------------------------------------------------------------------
# OSM street geometry
# ---------------------------------------------------------------------------

def fetch_berkeley_streets() -> geopandas.GeoDataFrame:
    """Fetch named streets in Berkeley via the Overpass API."""
    query = """
    [out:json][timeout:120];
    area["name"="Berkeley"]["admin_level"="8"]["boundary"="administrative"]->.city;
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
        timeout=150,
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

def _norm(name: str) -> str:
    return _normalize.street_name(name)


def build_geojson(
    records: list,
    streets_gdf: geopandas.GeoDataFrame,
    year: int,
) -> geopandas.GeoDataFrame:
    streets_gdf = streets_gdf.copy()
    streets_gdf["_norm"] = streets_gdf["name"].apply(_norm)

    def _even_base(n: int) -> int:
        """Round an address number down to the nearest even so that complementary
        even/odd blocks (e.g. 1400-1498 S-side and 1401-1499 N-side) share the
        same key and are stored as one merged row with both DAY_EVEN and DAY_ODD.
        """
        return n if n % 2 == 0 else n - 1

    # Group PDF rows into blocks: (street, even_from, even_to) → {even, odd}
    # Address keys are normalised to the even base so complementary N/S pairs
    # (which differ by ±1) collapse into a single row.
    blocks: dict = defaultdict(lambda: {"even": None, "odd": None})
    for r in records:
        parity = "even" if r["side"] in ("S", "W") else "odd"
        key    = (r["street"], _even_base(r["from_addr"]), _even_base(r["to_addr"]))
        code, desc = _schedule_code_and_desc(r["day_of_month"], year)
        blocks[key][parity] = (code, desc, r["ampm"])

    rows = []
    no_geom = 0
    for (street, from_addr, to_addr), sides in blocks.items():
        norm = _norm(street)
        hits = streets_gdf[streets_gdf["_norm"] == norm]
        if hits.empty:
            # Loose match: prefix
            hits = streets_gdf[streets_gdf["_norm"].str.startswith(norm)]
        if hits.empty:
            no_geom += 1
            continue

        # Union all matching OSM ways so the full street length is coloured,
        # not just the first segment returned by Overpass.
        geom = hits.geometry.union_all()
        even = sides["even"] or (None, None, None)
        odd  = sides["odd"]  or (None, None, None)

        rows.append({
            "geometry":   geom,
            "STREET_NAME": street,
            "DAY_EVEN":   even[0], "DESC_EVEN": even[1], "TIME_EVEN": even[2],
            "DAY_ODD":    odd[0],  "DESC_ODD":  odd[1],  "TIME_ODD":  odd[2],
            "L_F_ADD":    float(from_addr),      # even side start (always even)
            "L_T_ADD":    float(to_addr),          # even side end   (always even)
            "R_F_ADD":    float(from_addr + 1),    # odd  side start
            "R_T_ADD":    float(to_addr + 1),      # odd  side end
        })

    if no_geom:
        print(f"  ⚠  {no_geom} blocks had no matching OSM geometry and were dropped.")

    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    data_dir  = repo_root / "data" / "berkeley"
    out_path  = data_dir / "StreetSweeping.geojson"
    year      = datetime.date.today().year

    pdfs = sorted(data_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(
            f"No PDFs found in {data_dir}.\n"
            "Download them from:\n"
            "  https://berkeleyca.gov/city-services/streets-sidewalks-sewers-and-utilities/street-sweeping"
        )

    print(f"Parsing {len(pdfs)} PDF(s) …")
    records = []
    for pdf_path in pdfs:
        r = parse_pdf(pdf_path)
        print(f"  {pdf_path.name}: {len(r)} records")
        records.extend(r)
    print(f"Total schedule records: {len(records)}")

    print("Fetching Berkeley street geometry from OpenStreetMap …")
    streets_gdf = fetch_berkeley_streets()
    print(f"  {len(streets_gdf)} street ways fetched")

    print("Joining schedule to geometry …")
    out_gdf = build_geojson(records, streets_gdf, year)
    print(f"  {len(out_gdf)} segments in output")

    out_gdf.to_file(out_path, driver="GeoJSON")
    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
