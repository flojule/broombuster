#!/usr/bin/env python3
"""
Build data/dublin/StreetSweeping.geojson from the city's swept-street lines (fetched by
scripts/rebuild_city_data.py dublin).

Usage (from repo root):  python scripts/build_dublin_geojson.py
Input:  data/dublin/segments.geojson   street, "First and Third Friday" per swept street
Output: data/dublin/StreetSweeping.geojson

Both sides of a street share its schedule; the source gives no time window.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT


def build(seg: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows, one per swept street line."""
    rows = []
    for _, s in seg.iterrows():
        code, desc, time = citykit.side_entry(s["Frequency_"])
        if not code or s.geometry is None:
            continue
        rec = {"geometry": s.geometry, "STREET_NAME": pdf_city.street_label(s["Street_Nam"])}
        for side in ("EVEN", "ODD"):
            rec[f"DAY_{side}"], rec[f"DESC_{side}"], rec[f"TIME_{side}"] = code, desc, time
        rows.append(rec)
    return geopandas.GeoDataFrame(rows, crs=seg.crs).to_crs("EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "dublin"
    seg = geopandas.read_file(data_dir / "segments.geojson")
    print(f"  {len(seg)} swept street lines")
    pdf_city.write(build(seg), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
