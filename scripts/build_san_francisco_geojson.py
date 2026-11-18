#!/usr/bin/env python3
"""
Build data/san_francisco/StreetSweeping.geojson from DataSF's sweeping schedule, its street
centerlines and the county line (all fetched by scripts/rebuild_city_data.py san_francisco).

Usage (from repo root):  python scripts/build_san_francisco_geojson.py
Input:  data/san_francisco/yhqp-riqs.geojson  schedule rows per cnn and curb
        data/san_francisco/3psu-pn9h.geojson  centerline per cnn
        data/san_francisco/county.geojson     San Francisco County (Census TIGER)
Output: data/san_francisco/StreetSweeping.geojson

A row without geometry takes its cnn's centerline. Each row keeps the stretch of its block
whose curb lies inside the county (Mission St 5900 block: the county line runs down the
centerline from 37.70849,-122.45360 to Huron Ave, so the south curb there is Daly City).
"""

import itertools
from pathlib import Path

import geopandas
import pandas as pd
import shapely
from shapely.ops import substring

from broombuster import schemas
from broombuster.config import REPO_ROOT

_DIR = Path(REPO_ROOT) / "data" / "san_francisco"
_METRIC = "EPSG:26910"  # UTM 10N
_CURB_M = 5.0  # curb offset from the centerline
_SLACK_M = 10.0  # curb stretches this short do not cut a block (county line drawn ~m off)


def _span(line, left: bool, county) -> tuple[float, float] | None:
    """Fractions (from, to) of `line` (metric) whose `left` / right curb lies in `county`."""
    curb = line.offset_curve(_CURB_M if left else -_CURB_M)
    inside = curb.intersection(county)
    if inside.length < _SLACK_M:
        return None
    at = [line.project(shapely.Point(c)) for c in shapely.get_coordinates(inside)]
    a, b = min(at), max(at)
    a = 0.0 if a < _SLACK_M else round(a / line.length, 3)
    b = 1.0 if b > line.length - _SLACK_M else round(b / line.length, 3)
    return a, b


def _cut(rows, left, county) -> geopandas.GeoDataFrame:
    """Rows cut where a curb leaves the county: every row of the cnn at the same fractions
    (pieces numbered in cnn), each row keeping the pieces its curb has inside."""
    metric = rows.geometry.to_crs(_METRIC)
    near = rows.index[~metric.buffer(_CURB_M).within(county)]
    span = {i: _span(metric[i], left[i], county) for i in near if left[i] is not None}
    cut = {i: s for i, s in span.items() if s != (0.0, 1.0)}
    out, gone = [], 0
    for cnn, idx in rows.loc[list(cut)].groupby("cnn").groups.items():
        stops = sorted({0.0, 1.0} | {f for s in (cut[i] for i in idx) if s for f in s})
        pieces = list(itertools.pairwise(stops))
        for i in rows.index[rows["cnn"] == cnn]:
            a, b = span.get(i, (0.0, 1.0)) or (1.0, 0.0)
            gone += a > b
            for k, (f, t) in enumerate(pieces):
                if a <= f and t <= b:
                    r = rows.loc[i].copy()
                    r["cnn"] = f"{cnn}-{k}" if len(pieces) > 1 else cnn
                    r["geometry"] = substring(metric[i], f, t, normalized=True)
                    out.append(r)
    done = rows["cnn"].isin({rows.at[i, "cnn"] for i in cut})
    print(
        f"    {done.sum()} rows on {len(set(rows.loc[done, 'cnn']))} blocks cut at the county "
        f"line, {gone} rows outside dropped"
    )
    for i, s in cut.items():
        r = rows.loc[i]
        print(f"      {r.corridor} {r.limits} {r.cnnrightleft} {r.blockside} {r.weekday}: {s}")
    cuts = geopandas.GeoDataFrame(out, geometry="geometry", crs=_METRIC).to_crs(rows.crs)
    return pd.concat([rows[~done], cuts], ignore_index=True)


def main() -> None:
    rows = geopandas.read_file(_DIR / "yhqp-riqs.geojson")
    lines = geopandas.read_file(_DIR / "3psu-pn9h.geojson")
    county = geopandas.read_file(_DIR / "county.geojson")
    cnn = lines["cnn"].astype(str).str.replace(r"\.0$", "", regex=True)
    geo = dict(zip(cnn, lines.geometry, strict=True))

    missing = rows.geometry.isna()
    unknown = sorted(set(rows.loc[missing, "cnn"]) - set(geo))
    if unknown:
        raise SystemExit(f"rows without geometry, cnn not in the centerlines: {unknown}")
    rows.loc[missing, "geometry"] = rows.loc[missing, "cnn"].map(geo)
    print(f"    {missing.sum()} rows took their cnn's centerline")

    left = [
        schemas.sf_on_left(lr, side, g)
        for lr, side, g in zip(rows["cnnrightleft"], rows["blockside"], rows.geometry, strict=True)
    ]
    area = county.to_crs(_METRIC).geometry.union_all()
    _cut(rows, left, area).to_file(_DIR / "StreetSweeping.geojson", driver="GeoJSON")


if __name__ == "__main__":
    main()
