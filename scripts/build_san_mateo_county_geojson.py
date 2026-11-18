#!/usr/bin/env python3
"""
Build data/san_mateo_county/StreetSweeping.geojson from the county's sweeping routes in
unincorporated San Mateo County (fetched by scripts/rebuild_city_data.py san_mateo_county).

Usage (from repo root):  python scripts/build_san_mateo_county_geojson.py
Input:  data/san_mateo_county/routes.geojson  street lines: schedule words, street name
Output: data/san_mateo_county/StreetSweeping.geojson

Only the areas with a schedule adopted by ordinance give hours; those are ticketed. A route
without hours is swept, nobody ticketed (NT). Both sides share the schedule.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

_HOURS_RE = re.compile(r"\d{1,2}(?:[:.]\d\d)?\s*[ap]m\s*-\s*\d{1,2}(?:[:.]\d\d)?\s*[ap]m", re.I)


def entry(schedule) -> tuple:
    """(code, desc, time) of a route; (None, None, None) when it has no schedule."""
    text = normalize.clean_text(schedule)
    hours = _HOURS_RE.search(text)
    if not hours:
        return citykit.side_entry(text, enforced=False)
    return citykit.side_entry(text[: hours.start()], re.sub(r"(\d)\.(\d\d)", r"\1:\2", hours[0]))


def build(routes: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per scheduled route line."""
    out = geopandas.GeoDataFrame(
        {"STREET_NAME": routes["Street_Name"].fillna("").map(pdf_city.street_label)},
        geometry=routes.geometry,
        crs=routes.crs,
    )
    entries = [entry(s) for s in routes["Schedule"]]
    for side in ("EVEN", "ODD"):
        for i, col in enumerate(("DAY", "DESC", "TIME")):
            out[f"{col}_{side}"] = [e[i] for e in entries]
    return out[out["DAY_EVEN"].notna() & (out["STREET_NAME"] != "")].reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_mateo_county"
    src = data_dir / "routes.geojson"
    if not src.exists():
        raise FileNotFoundError(f"{src} missing; run rebuild_city_data.py san_mateo_county")
    pdf_city.write(
        build(geopandas.read_file(src).to_crs("EPSG:4326")), data_dir / "StreetSweeping.geojson"
    )


if __name__ == "__main__":
    main()
