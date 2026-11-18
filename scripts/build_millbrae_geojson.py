#!/usr/bin/env python3
"""
Build data/millbrae/StreetSweeping.geojson from the city's sweeping parcels and the county
centerlines (both fetched by scripts/rebuild_city_data.py millbrae).

Usage (from repo root):  python scripts/build_millbrae_geojson.py
Input:  data/millbrae/parcels.geojson      per parcel: address, sweep day, hours, car removal
        data/millbrae/centerlines.geojson  San Mateo County centerlines in Millbrae
Output: data/millbrae/StreetSweeping.geojson

Each block side takes the most common schedule of the parcels on it (street, house number
in range, same parity). "POSTED MANDATORY CAR REMOVAL" is ticketed; "CAR REMOVAL
REQUESTED" is not (NT); no word on removal keeps the normal code.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

_HOURS_RE = re.compile(r"(\d{1,2}:\d\d\s*[AP]M)\s*(?:and|to)\s*(\d{1,2}:\d\d\s*[AP]M)", re.I)


def schedule(row) -> str | None:
    """ "day|hours|removal" of a parcel; None when it has no sweep day."""
    text = normalize.clean_text(row["SweepDay"]) or normalize.clean_text(row["SweepShort"])
    if not text:
        return None
    m = _HOURS_RE.search(str(row["SweepText"] or "") + " " + str(row["SweepShort"] or ""))
    hours = f"{m[1]} to {m[2]}" if m else ""
    return f"{text}|{hours}|{normalize.clean_text(row['CarDesc']).upper()}"


def side_entry(value: str) -> tuple:
    text, hours, removal = value.split("|")
    return citykit.side_entry(text, hours, enforced="REQUESTED" not in removal)


def points(parcels: pd.DataFrame) -> pd.DataFrame:
    """Address points: STREET_KEY, NUM, SCHEDULE."""
    name = (parcels["SITUSDIREC"].fillna("") + " " + parcels["SITUSSTREE"].fillna("") + " "
            + parcels["SITUSSTR_1"].fillna(""))  # fmt: skip
    return pd.DataFrame(
        {
            "STREET_KEY": name.map(normalize.street_name),
            "NUM": parcels["SITUSNO1"],
            "SCHEDULE": parcels.apply(schedule, axis=1),
        }
    )


def build(parcels: pd.DataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks with a schedule on at least one side."""
    out = citykit.address_blocks(lines, points(parcels), side_entry)
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "millbrae"
    inputs = [data_dir / "parcels.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py millbrae")
    parcels = geopandas.read_file(inputs[0])
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(build(parcels, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
