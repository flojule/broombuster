#!/usr/bin/env python3
"""
Build data/san_mateo/StreetSweeping.geojson from the city's sweeping parcels and the county
centerlines (both fetched by scripts/rebuild_city_data.py san_mateo).

Usage (from repo root):  python scripts/build_san_mateo_geojson.py
Input:  data/san_mateo/parcels.geojson      per parcel: address, sweep day, weeks, hours
        data/san_mateo/centerlines.geojson  San Mateo County centerlines in San Mateo
Output: data/san_mateo/StreetSweeping.geojson

Each block side takes the most common schedule of the parcels on it (street, house number
in range, same parity). The city does not say which sides go unticketed: every side keeps
its code. Bi-weekly Saturday sweeps have no published start date (POSTED, with hours).
"""

from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT


def schedule(row) -> str | None:
    """ "weeks day|hours" of a parcel; None when it has no sweep day."""
    day = normalize.clean_text(row["NEW_DAY"]).replace("M/W/F", "MON WED FRI")
    if not day:
        return None
    hours = normalize.clean_text(row["SWEEP_TIME"])
    return f"{normalize.clean_text(row['NEW_WEEK'])} {day}|{hours}"


def side_entry(value: str) -> tuple:
    return citykit.side_entry(*value.split("|"))


def street_key(name: str) -> str:
    """Key of a parcel street: "HUMBOLDT ST N" -> key of "N HUMBOLDT ST", "09TH" -> "9TH"."""
    toks = [t.lstrip("0") or t for t in name.upper().split()]
    if len(toks) > 1 and toks[-1] in ("N", "S", "E", "W"):
        toks = [toks[-1], *toks[:-1]]
    return normalize.street_name(pdf_city.street_label(" ".join(toks)))


def points(parcels: pd.DataFrame) -> pd.DataFrame:
    """Address points: STREET_KEY, NUM, SCHEDULE."""
    return pd.DataFrame(
        {
            "STREET_KEY": parcels["STNAME"].fillna("").map(street_key),
            "NUM": parcels["STNUM"],
            "SCHEDULE": parcels.apply(schedule, axis=1),
        }
    )


def build(parcels: pd.DataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks with a schedule on at least one side."""
    out = citykit.address_blocks(lines, points(parcels), side_entry)
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_mateo"
    inputs = [data_dir / "parcels.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py san_mateo")
    parcels = geopandas.read_file(inputs[0])
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(build(parcels, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
