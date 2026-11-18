#!/usr/bin/env python3
"""
Build data/south_san_francisco/StreetSweeping.geojson from the city's sweeping lookup layer
(fetched by scripts/rebuild_city_data.py south_san_francisco).

Usage (from repo root):  python scripts/build_south_san_francisco_geojson.py
Input:  data/south_san_francisco/schedule.geojson  blocks: sweep day and weeks, posted No Parking
Output: data/south_san_francisco/StreetSweeping.geojson

Sweeping itself is not posted (NT); a block with posted No Parking (NoParkDay, NoParkTime)
takes the sign. "Monday-Tuesday" signs give one day per side, sides not on file (POSTED).
"""

from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT


def entry(row) -> tuple:
    """(code, desc, time) of a block, both sides alike; (None, None, None) when not swept."""
    sign, hours = normalize.clean_text(row["NoParkDay"]), row["NoParkTime"]
    if sign:
        if "-" in sign:
            days = " on one side, ".join(d.strip()[:3] for d in sign.split("-"))
            return citykit.UNKNOWN, f"{days} on the other", citykit.time_text(hours)
        if sign.upper() == "EVERYDAY":
            return "E", "Every day", citykit.time_text(hours)
        return citykit.side_entry(sign, hours)
    weeks, day = normalize.clean_text(row["NewDays"]), normalize.clean_text(row["DAY"])
    if weeks:
        return citykit.side_entry(weeks, enforced=False)
    if day and day.upper() != "NO INFO":
        return citykit.COURTESY, f"{day} (weeks not published), no tickets", None
    return None, None, None


def build(src: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept block."""
    rows = []
    for _, r in src.iterrows():
        code, desc, time = entry(r)
        if not code:
            continue
        rec = {"geometry": r.geometry, "STREET_NAME": pdf_city.street_label(r["FULL_NAME"])}
        for col, s in zip(
            ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"),
            ("FR_ADD_L", "TO_ADD_L", "FR_ADD_R", "TO_ADD_R"),
            strict=True,
        ):
            n = pd.to_numeric(r[s], errors="coerce")
            rec[col] = float(n) if n and n > 0 else None
        for side in ("EVEN", "ODD"):
            rec[f"DAY_{side}"], rec[f"DESC_{side}"], rec[f"TIME_{side}"] = code, desc, time
        rows.append(rec)
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "south_san_francisco"
    src = data_dir / "schedule.geojson"
    if not src.exists():
        raise FileNotFoundError(f"{src} missing; run rebuild_city_data.py south_san_francisco")
    pdf_city.write(build(geopandas.read_file(src)), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
