#!/usr/bin/env python3
"""
Build data/albany/StreetSweeping.geojson from the city's zone pages (transcribed in
data/albany/zones.yaml) and the county's street centerlines (fetched by
scripts/rebuild_city_data.py albany).

Usage (from repo root):  python scripts/build_albany_geojson.py
Input:  data/albany/zones.yaml            zone schedules and [zone, street, where] rows
        data/albany/centerlines.geojson   county blocks with address ranges
Output: data/albany/StreetSweeping.geojson

The odd side is swept one week, the even side the next. A block takes the row of its
street whose limit holds: an address range before a Solano/Marin/San Pablo limit before
the whole street.
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city
import yaml
from shapely.geometry import Point

from broombuster.config import REPO_ROOT

_UTM = "EPSG:26910"


def _divider(lines, name):
    return lines[lines["STREET_NAME"].map(citykit.street_key) == name].geometry.union_all()


def limit_holds(where: str, block, mid: Point, dividers: dict) -> bool:
    """Whether a row's limit covers a block (`mid`: its midpoint, in UTM)."""

    def north_of(name):
        q = dividers[name].interpolate(dividers[name].project(mid))
        return mid.y > q.y

    if where == "all":
        return True
    if where == "north of Solano":
        return north_of("SOLANO")
    if where == "Solano to Marin":
        return not north_of("SOLANO") and north_of("MARIN")
    if where == "south of Marin":
        return not north_of("MARIN")
    if where == "west of San Pablo":
        q = dividers["SANPABLO"].interpolate(dividers["SANPABLO"].project(mid))
        return mid.x < q.x
    lo, hi = (int(n) for n in where.split("-"))
    return bool(citykit.in_range(block.to_frame().T, lo, hi).iloc[0])


def _rank(where: str) -> int:
    return 0 if where[0].isdigit() else 2 if where == "all" else 1


def build(table: dict, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: each block of a listed street with its zone's schedule."""
    lines = lines.reset_index(drop=True)
    utm = lines.to_crs(_UTM)
    dividers = {k: _divider(utm, k) for k in ("SOLANO", "MARIN", "SANPABLO")}
    rows = sorted(table["streets"], key=lambda r: _rank(r[2]))
    out = []
    for i, blk in lines.iterrows():
        mid = utm.geometry[i].interpolate(0.5, normalized=True)
        hit = next(
            (
                r
                for r in rows
                if citykit.street_key(r[1]) == citykit.street_key(blk["STREET_NAME"])
                and limit_holds(r[2], blk, mid, dividers)
            ),
            None,
        )
        if hit is None:
            continue
        zone = table["zones"][hit[0]]
        rec = blk.to_dict()
        parity = citykit.parity_sides(rec) or {"L": "EVEN", "R": "ODD"}
        for par in set(parity.values()):
            entry = citykit.side_entry(zone[par.lower()], zone["time"])
            rec[f"DAY_{par}"], rec[f"DESC_{par}"], rec[f"TIME_{par}"] = entry
        out.append(rec)
    gdf = geopandas.GeoDataFrame(out, geometry="geometry", crs="EPSG:4326")
    gdf["STREET_NAME"] = gdf["STREET_NAME"].map(pdf_city.street_label)
    return gdf


def main():
    data_dir = Path(REPO_ROOT) / "data" / "albany"
    table = yaml.safe_load((data_dir / "zones.yaml").read_text(encoding="utf-8"))
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "alameda")
    out = build(table, lines)
    print(f"  {len(table['streets'])} zone rows, {len(out)} of {len(lines)} blocks swept")
    pdf_city.write(out, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
