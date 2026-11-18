#!/usr/bin/env python3
"""
Build data/cupertino/StreetSweeping.geojson from the city's sweeping layers and the county's
street centerlines (all fetched by scripts/rebuild_city_data.py cupertino).

Usage (from repo root):  python scripts/build_cupertino_geojson.py
Input:  data/cupertino/signed.geojson       signed segments: SCHEDULE ("1ST & 3RD MONDAY"), Time
        data/cupertino/zones.geojson        eight sweeping zones, one schedule each
        data/cupertino/centerlines.geojson  county centerlines in the city
Output: data/cupertino/StreetSweeping.geojson

A county block along a signed segment takes its schedule and posted hours (ticketed, both
sides); every other block takes its zone's schedule as NT (unsigned: no tickets).
"""

from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster.config import REPO_ROOT


def build(signed, zones, lines) -> geopandas.GeoDataFrame:
    """Standard-schema rows: signed blocks (enforced) and the rest of each zone (NT)."""
    segs = signed.assign(_sched=signed["SCHEDULE"], _time=signed["Time"])[
        ["_sched", "_time", "FullStreet", "geometry"]
    ]
    on_signed = citykit.lines_along(lines, segs, "FullStreet")
    rest = lines[~lines.index.isin(on_signed["_src"])]
    in_zone = citykit.zones_to_blocks(rest, zones[["Schedule", "geometry"]])
    parts = [
        on_signed.assign(_entry=[citykit.side_entry(s, t) for s, t in zip(
            on_signed["_sched"], on_signed["_time"], strict=True)]),
        in_zone.assign(_entry=[citykit.side_entry(s, enforced=False) for s in in_zone["Schedule"]]),
    ]  # fmt: skip
    out = pd.concat(parts, ignore_index=True)
    for side in ("EVEN", "ODD"):
        out[[f"DAY_{side}", f"DESC_{side}", f"TIME_{side}"]] = out["_entry"].tolist()
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    cols = ["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]
    keep = cols + [f"{p}_{s}" for p in ("DAY", "DESC", "TIME") for s in ("EVEN", "ODD")]
    return geopandas.GeoDataFrame(out[keep], crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "cupertino"
    paths = [data_dir / f"{n}.geojson" for n in ("signed", "zones", "centerlines")]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py cupertino")
    signed, zones = (geopandas.read_file(p) for p in paths[:2])
    lines = citykit.centerlines(paths[2], "santa_clara")
    print(f"  {len(signed)} signed segments, {len(zones)} zones, {len(lines)} county blocks")
    pdf_city.write(build(signed, zones, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
