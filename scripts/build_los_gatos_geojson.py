#!/usr/bin/env python3
"""
Build data/los_gatos/StreetSweeping.geojson from the town's sweeping routes and the county's
street centerlines (both fetched by scripts/rebuild_city_data.py los_gatos).

Usage (from repo root):  python scripts/build_los_gatos_geojson.py
Input:  data/los_gatos/routes.geojson  routes: RouteLabel ("T1 - Week 1 & Week 3 Tuesday")
        data/los_gatos/centerlines.geojson  county centerlines in the town
Output: data/los_gatos/StreetSweeping.geojson

The town restricts no parking on sweeping days, so every block is NT. The schedule comes
from RouteLabel: the town's 2026 calendar sweeps T8 in weeks 2 and 4, as the label says
(RouteSchedule says weeks 1 and 3).
"""

from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster.config import REPO_ROOT


def schedule_words(label: str) -> str:
    """ "T8A - Week 2 & Week 4 Monday" -> "Week 2 & Week 4 Monday"."""
    return label.split(" - ", 1)[-1]


def build(routes, lines) -> geopandas.GeoDataFrame:
    blocks = citykit.zones_to_blocks(lines, routes[["RouteLabel", "geometry"]])
    entries = [citykit.side_entry(schedule_words(x), enforced=False) for x in blocks["RouteLabel"]]
    for side in ("EVEN", "ODD"):
        blocks[[f"DAY_{side}", f"DESC_{side}", f"TIME_{side}"]] = entries
    blocks["STREET_NAME"] = blocks["STREET_NAME"].map(pdf_city.street_label)
    keep = ["STREET_NAME", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "geometry"]
    keep += [f"{p}_{s}" for p in ("DAY", "DESC", "TIME") for s in ("EVEN", "ODD")]
    return geopandas.GeoDataFrame(blocks[keep], crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "los_gatos"
    paths = [data_dir / f"{n}.geojson" for n in ("routes", "centerlines")]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py los_gatos")
    routes = geopandas.read_file(paths[0])
    lines = citykit.centerlines(paths[1], "santa_clara")
    print(f"  {len(routes)} routes, {len(lines)} county blocks")
    pdf_city.write(build(routes, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
