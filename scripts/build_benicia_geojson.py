#!/usr/bin/env python3
"""
Build data/benicia/StreetSweeping.geojson from the city's sweeping areas and Solano
County's centerlines (both fetched by scripts/rebuild_city_data.py benicia).

Usage (from repo root):  python scripts/build_benicia_geojson.py
Input:  data/benicia/zones.geojson        areas and route buffers with ScheduledD
        data/benicia/centerlines.geojson  blocks with address ranges
Output: data/benicia/StreetSweeping.geojson

Blocks take, in order: downtown, high-priority routes, the 100 blocks off First Street
(third Monday), then their neighborhood. Republic Services' 2025 brochure overrides the
city's 2023 layer for the industrial area and downtown. Both sides share the schedule.
"""

from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

# ScheduledD -> (schedule words, posted time) per the 2025 brochure.
_BROCHURE = {
    "Third Tuesday": ("2nd & 4th Thursday", None),  # industrial area
    "Every Monday and Friday Morning": ("Every Friday", "AM"),  # downtown
}
_DOWNTOWN, _PRIORITY = "Every Monday and Friday Morning", "Every Thursday Night"
_FIRST_ST = {normalize.street_name("FIRST ST"), normalize.street_name("1ST ST")}
_UTM = "EPSG:26910"


def entry(scheduled: str) -> tuple[str, str, str | None]:
    """(code, desc, time) of an area's ScheduledD."""
    words, time = _BROCHURE.get(scheduled, (scheduled, None))
    hit = citykit.code_from_text(words)
    if hit is None:
        raise ValueError(f"unreadable schedule {scheduled!r}")
    desc = f"{hit[1]} night" if scheduled == _PRIORITY else hit[1]
    return hit[0], desc, time


def off_first_street(lines: geopandas.GeoDataFrame) -> pd.Series:
    """Lettered-street blocks numbered 100-199 that meet First Street."""
    first = lines[lines["STREET_KEY"].isin(_FIRST_ST)].to_crs(_UTM).union_all()
    near = lines.to_crs(_UTM).distance(first) < 5
    lettered = lines["STREET_NAME"].str.match(r"[EW] [A-L] ST$")
    return near & lettered & citykit.in_range(lines, 100, 199)


def rows(lines: geopandas.GeoDataFrame, zones: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept block."""
    lines = lines.assign(_id=range(len(lines)))
    hundred = lines["_id"][off_first_street(lines)]
    parts = []
    for name in (_DOWNTOWN, _PRIORITY):
        hit = citykit.zones_to_blocks(lines, zones[zones["ScheduledD"] == name], min_share=0.6)
        parts.append(hit)
        lines = lines[~lines["_id"].isin(hit["_id"])]
    hundred = lines["_id"].isin(hundred)
    parts.append(lines[hundred].assign(ScheduledD="Third Monday"))
    rest = zones[~zones["ScheduledD"].isin([_DOWNTOWN, _PRIORITY])]
    parts.append(citykit.zones_to_blocks(lines[~hundred], rest))
    out = geopandas.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
    entries = [entry(s) for s in out["ScheduledD"]]
    out = out[["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]].copy()
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    for side in ("EVEN", "ODD"):
        for i, col in enumerate(("DAY", "DESC", "TIME")):
            out[f"{col}_{side}"] = [e[i] for e in entries]
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "benicia"
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "solano")
    zones = geopandas.read_file(data_dir / "zones.geojson")
    print(f"  {len(zones)} areas, {len(lines)} blocks")
    pdf_city.write(rows(lines, zones), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
