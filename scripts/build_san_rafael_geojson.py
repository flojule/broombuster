#!/usr/bin/env python3
"""
Build data/san_rafael/StreetSweeping.geojson from the city's sweeping zones and swept
streets (both fetched by scripts/rebuild_city_data.py san_rafael).

Usage (from repo root):  python scripts/build_san_rafael_geojson.py
Input:  data/san_rafael/zones.geojson    2025 sweeper zones (FREQUENCY per zone)
        data/san_rafael/streets.geojson  swept street blocks with address ranges
Output: data/san_rafael/StreetSweeping.geojson

Each block takes the zone holding most of it; both sides share the zone's schedule. The city
sweeps April to October and asks residents to move their cars; only the Canal residential
zone posts No Parking signs (read on the street), so every other zone is COURTESY.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

SEASON = "Apr–Oct 2026"  # "Current schedule is for April to October 2026"
# Zones with No Parking street-sweeping signs: the posted hours.
SIGNED = {"Canal Residential": "8AM to 10AM"}
_RANGES = {"L_F_ADD": "L_ADD_FROM", "L_T_ADD": "L_ADD_TO", "R_F_ADD": "R_ADD_FROM",
           "R_T_ADD": "R_ADD_TO"}  # fmt: skip


def zone_entry(route, freq) -> tuple:
    """(code, desc, time) of a zone's blocks."""
    if route in SIGNED:
        return citykit.side_entry(freq, SIGNED[route])
    words = normalize.clean_text(freq)
    if re.search(r"\d/\d", words):  # explicit dates
        return citykit.COURTESY, f"{words} {SEASON[-4:]}, no tickets", None
    if "-" in words:  # "Downtown-Every Friday, Miracle Mile-Every Thursday"
        return citykit.COURTESY, f"{words.replace('-', ': ')}, {SEASON}, no tickets", None
    return citykit.courtesy_entry([words], note=SEASON)


def streets(raw: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """The city's swept blocks in the standard columns."""
    out = geopandas.GeoDataFrame(
        {"STREET_NAME": raw["FULLNAME"].fillna("").str.split().str.join(" ").str.upper()},
        geometry=raw.geometry,
        crs=raw.crs,
    ).to_crs("EPSG:4326")
    for col, src in _RANGES.items():
        n = pd.to_numeric(raw[src], errors="coerce")
        out[col] = n.where(n > 0)
    return out[out["STREET_NAME"] != ""]


def join(zones: geopandas.GeoDataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    out = citykit.zones_to_blocks(lines, zones[["ROUTE_ID", "FREQUENCY", "geometry"]])
    entries = [zone_entry(r, f) for r, f in zip(out["ROUTE_ID"], out["FREQUENCY"], strict=True)]
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"], out[f"DESC_{side}"], out[f"TIME_{side}"] = zip(*entries, strict=True)
    return out.drop(columns=["ROUTE_ID", "FREQUENCY"])


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_rafael"
    zones = geopandas.read_file(data_dir / "zones.geojson")
    lines = streets(geopandas.read_file(data_dir / "streets.geojson"))
    print(f"  {len(zones)} zones, {len(lines)} swept blocks")
    pdf_city.write(join(zones, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
