#!/usr/bin/env python3
"""Build cities' extra map layers (permit areas, meters, EV charging, …) from the
sources in broombuster/layer_sources.py, for the themes in each manifest's `layers:`.

    python scripts/build_layers.py [city ...] [--theme parking ...]

Writes data/<city>/<theme>.fgb (the server's lookups: /api/rules) and
frontend/layers/<region>/<theme>.geojson (the map's copy, every city of the region).
A source that fails keeps its previous files. Commit both outputs.
"""

import argparse
import colorsys
import os
import sys
from pathlib import Path

import geopandas
import pandas as pd
import shapely

from broombuster import layer_sources, layers
from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)


def _write_fgb(gdf, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.fgb")
    try:
        gdf.to_file(tmp, driver="FlatGeobuf")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _clip(gdf, city_key: str):
    """Only the city (some sources are county- or statewide), schema columns only."""
    lat0, lon0, lat1, lon1 = CITIES[city_key]["bbox"]
    gdf = gdf.set_geometry(gdf.geometry.map(layer_sources.polygonal))
    gdf = gdf.clip(shapely.box(lon0, lat0, lon1, lat1))
    gdf = gdf.set_geometry(gdf.geometry.map(layer_sources.polygonal))
    gdf = gdf[gdf.geometry.notna()]
    cols = [c for c in (*layer_sources.COLUMNS, layer_sources.COLOR) if c in gdf]
    return gdf[~gdf.geometry.is_empty][[*cols, "geometry"]]


def build_city(city_key: str, themes: list[str] | None) -> list[str]:
    """Rebuild a city's layers; returns the themes that failed."""
    failed = []
    for theme in CITIES[city_key].get("layers") or []:
        if themes and theme not in themes:
            continue
        src = layer_sources.SOURCES.get(theme, {}).get(city_key)
        if src is None:
            print(f"    {theme}: no source for {city_key}")
            failed.append(theme)
            continue
        try:
            gdf = src(CITIES[city_key])
        except Exception as e:  # one broken source must not stop the others
            print(f"    {theme}: FAILED ({e}); keeping the previous file")
            failed.append(theme)
            continue
        if gdf.empty:
            print(f"    {theme}: no features; keeping the previous file")
            failed.append(theme)
            continue
        gdf = _clip(gdf, city_key)
        if theme == "meters":
            utm = gdf.estimate_utm_crs()
            gdf = meter_blocks(gdf.to_crs(utm), _city_streets(city_key, utm)).to_crs(4326)
        _write_fgb(gdf, layers.fgb_path(city_key, theme))
        print(f"    {theme}: {len(gdf):,} features")
        if theme == "parking" and city_key in layer_sources.AREAS:
            failed += _build_areas(city_key)
    return failed


def _build_areas(city_key: str) -> list[str]:
    """data/<city dir>/permit_areas.fgb: the city's published permit area boundaries."""
    try:
        gdf = layer_sources.AREAS[city_key]()
    except Exception as e:
        print(f"    {layers.AREAS}: FAILED ({e}); keeping the previous file")
        return [layers.AREAS]
    gdf = _clip(gdf, city_key)
    if gdf.empty:
        print(f"    {layers.AREAS}: no features; keeping the previous file")
        return [layers.AREAS]
    _write_fgb(gdf, layers.fgb_path(city_key, layers.AREAS))
    print(f"    {layers.AREAS}: {len(gdf):,} areas")
    return []


def build_tables(city_key: str) -> list[str]:
    """data/<city dir>/<name>.csv for the city's tables (layer_sources.TABLES)."""
    failed = []
    for name, src in layer_sources.TABLES.get(city_key, {}).items():
        try:
            df = src()
        except Exception as e:
            print(f"    {name}: FAILED ({e}); keeping the previous file")
            failed.append(name)
            continue
        if df.empty:
            failed.append(name)
            continue
        path = layers.city_dir(city_key) / f"{name}.csv"
        df.to_csv(path.with_suffix(".tmp.csv"), index=False)
        os.replace(path.with_suffix(".tmp.csv"), path)
        print(f"    {name}: {len(df):,} rows")
    return failed


# Colors for permit areas whose city publishes none: distinct hues that read on both
# basemaps and stay clear of the sweeping colors (red, orange, blue: core.js URGENCY);
# neighboring areas never share one (_area_colors).
AREA_PALETTE = (
    "#7c3aed",
    "#0d9488",
    "#c026d3",
    "#65a30d",
    "#4d7c0f",
    "#6b21a8",
    "#4f46e5",
    "#0891b2",
    "#15803d",
    "#9333ea",
)


def _alarm_hue(color: str) -> bool:
    """A saturated red to orange (hue 330-50 deg): reads as the sweeping alert colors."""
    h, _l, sat = colorsys.rgb_to_hls(*(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)))
    return sat > 0.35 and (h * 360 >= 330 or h * 360 <= 50)


def _area_colors(g: geopandas.GeoDataFrame) -> list[str]:
    """Each area's color: the city's own when it publishes one (not an alarm hue), else the
    least used palette color no touching area (within 150 m) has, taking the areas biggest
    first. Stable for a given input, so a rebuild keeps the map's colors."""
    color = layer_sources.COLOR
    have = g[color] if color in g else pd.Series([None] * len(g), index=g.index)
    out = {i: c for i, c in have.items() if isinstance(c, str) and c and not _alarm_hue(c)}
    near = g.geometry.buffer(150)
    for i in g.geometry.area.sort_values(ascending=False).index:
        if i in out:
            continue
        taken = {out[j] for j in g.index[near.intersects(near[i])] if j in out}
        free = [c for c in AREA_PALETTE if c not in taken] or list(AREA_PALETTE)
        used = list(out.values())
        out[i] = min(free, key=used.count)
    return [out[i] for i in g.index]


def _edge(color: str) -> str | None:
    """A darker edge for a light color (a city's pale yellow vanishes on the light
    basemap); None when the color itself reads."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    if 0.299 * r + 0.587 * g + 0.114 * b <= 165:
        return None
    return "#" + "".join(f"{int(c * 0.6):02x}" for c in (r, g, b))


# Faces of the street network over this size (EPSG:3857 m², ~60,000 m² on the ground in
# the Bay Area) are parks and hills, not blocks: areas keep their own shape there.
BIG_FACE = 100_000
# Holes in an area up to this size (a few blocks: a commercial strip, a school) are filled,
# so its edge is its outline; the permit curbs show the blocks themselves.
HOLE_MAX = 200_000


def snap_to_blocks(areas: geopandas.GeoDataFrame, streets, curbs=None) -> geopandas.GeoDataFrame:
    """Permit areas (EPSG:3857) by street block, so edges run along streets. Blocks are the
    faces of the city's swept streets; a curb lies in its own block's face. A block goes
    whole to the area covering most of it, else to the area with the most permit curbs
    (EPSG:3857, label) in it, else to none. In faces over BIG_FACE and outside all faces,
    each area keeps its own shape, less strips under 30 m wide away from its own curbs. An
    area that would keep under half of itself keeps its own shape wherever no other area
    took the block. Holes up to HOLE_MAX no other area took are filled."""
    lines = [g for g in streets if g is not None and "LineString" in g.geom_type]
    blocks = list(shapely.polygonize([shapely.union_all(lines)]).geoms)
    by_label = {lbl: i for i, lbl in areas["label"].items()}
    marks = []  # (area index, curb part)
    if curbs is not None:
        for lbl, c in zip(curbs["label"], curbs.geometry, strict=True):
            i = by_label.get(lbl.split(" or ")[0].strip())
            if i is not None and c is not None:
                marks += [(i, part) for part in shapely.get_parts(c) if not part.is_empty]
    mtree = shapely.STRtree([part.interpolate(0.5, normalized=True) for _, part in marks])
    near = {i: shapely.union_all([c for j, c in marks if j == i]) for i in areas.index}
    owned: dict[int, list] = {i: [] for i in areas.index}
    big = []
    sidx = areas.sindex
    for b in blocks:
        cover = {i: areas.geometry[i].intersection(b).area for i in areas.index[sidx.query(b)]}
        best = max(cover, key=cover.get, default=None)
        held: dict[int, float] = {}
        for k in mtree.query(b, predicate="contains"):
            held[marks[k][0]] = held.get(marks[k][0], 0.0) + marks[k][1].length
        if best is not None and cover[best] > 0.5 * b.area:
            owned[best].append(b)
        elif held:
            owned[max(held, key=held.get)].append(b)
        elif b.area > BIG_FACE:
            big.append(b)
    taken = shapely.union_all([b for bs in owned.values() for b in bs])
    open_land = shapely.union_all(
        [*big, shapely.union_all(areas.geometry).difference(shapely.union_all(blocks))]
    )

    def strips_off(g, own):
        wide = g.buffer(-15, join_style="mitre").buffer(15, join_style="mitre")
        thin = [t for t in shapely.get_parts(g.difference(wide)) if t.distance(own) < 15]
        return shapely.union_all([wide, *thin])

    out = areas.copy()
    for i, g in areas.geometry.items():
        merged = shapely.union_all([*owned[i], strips_off(g.intersection(open_land), near[i])])
        if merged.area < 0.5 * g.area:
            merged = shapely.union_all([*owned[i], strips_off(g.difference(taken), near[i])])
        merged = merged.buffer(0.5).buffer(-0.5)
        out.at[i, "geometry"] = layer_sources._clean(merged.simplify(2), 2000)
    for i, g in out.geometry.items():
        others = shapely.union_all([h for j, h in out.geometry.items() if j != i])
        out.at[i, "geometry"] = _fill_holes(g, HOLE_MAX).difference(others)
    return out[~out.geometry.is_empty]


def _fill_holes(g, max_area: float):
    """g with its holes under max_area filled."""
    parts = [
        shapely.Polygon(p.exterior, [h for h in p.interiors if shapely.Polygon(h).area >= max_area])
        for p in shapely.get_parts(g)
    ]
    return shapely.union_all(parts)


def _city_streets(city_key: str, crs=3857):
    """The city's swept street centerlines (EPSG:3857 by default)."""
    g = geopandas.read_file(_ROOT / CITIES[city_key]["fgb_path"], columns=[])
    return g.to_crs(crs).geometry


# A meter stands at its own block's curb: 8-9 m (median), 13-16 m (95th percentile) and up
# to 22 m (Adeline St) from the swept centerline in Oakland and Berkeley.
METER_REACH_M = 25
# A meter within this of being as near to another block (a corner) goes to the block most
# other meters are nearest to: meters stand in rows along their own block.
METER_TIE_M = 5


def street_blocks(streets):
    """The street lines between junctions: split where they cross, joined where two meet."""
    lines = [g for g in streets if g is not None and "LineString" in g.geom_type]
    return shapely.get_parts(shapely.line_merge(shapely.union_all(lines)))


def meter_blocks(meters: geopandas.GeoDataFrame, streets) -> geopandas.GeoDataFrame:
    """Meter points (metric CRS) as the street blocks (street_blocks) they stand on, within
    METER_REACH_M: the nearest block, or at a corner (METER_TIE_M) the one with the most
    other meters. One line per block and (label, rule, asof); points farther from every
    block, and other shapes, stay as they are."""
    is_pt = (meters.geom_type == "Point").to_numpy()
    if not is_pt.any():
        return meters
    pts, cols = meters[is_pt], [c for c in meters.columns if c != "geometry"]
    blocks = street_blocks(streets)
    i, j = shapely.STRtree(blocks).query(
        pts.geometry.values, predicate="dwithin", distance=METER_REACH_M
    )
    c = pd.DataFrame({"i": i, "j": j, "d": shapely.distance(pts.geometry.values[i], blocks[j])})
    c["d1"] = c.groupby("i")["d"].transform("min")
    own = c[c["d"] == c["d1"]].drop_duplicates("i").set_index("i")["j"]
    c = c[c["d"] <= c["d1"] + METER_TIE_M]
    c["votes"] = c["j"].map(own.value_counts()).fillna(0) - (c["j"] == c["i"].map(own))
    pick = c.sort_values(["votes", "d"], ascending=[False, True], kind="stable")
    pick = pick.drop_duplicates("i")
    hit = pts.iloc[pick["i"]].assign(_block=pick["j"].to_numpy())
    hit = hit.drop_duplicates(["_block", *cols])
    hit["geometry"] = geopandas.GeoSeries(blocks[hit["_block"]], index=hit.index)
    rest = pd.concat([meters[~is_pt], pts[~pd.RangeIndex(len(pts)).isin(pick["i"])]])
    out = pd.concat([hit.drop(columns="_block"), rest], ignore_index=True)
    out["geometry"] = shapely.force_2d(out.geometry.values)  # FlatGeobuf: no mixed 2D/3D
    return geopandas.GeoDataFrame(out, geometry="geometry", crs=meters.crs)


def permit_curbs(city_key: str) -> geopandas.GeoDataFrame:
    """The city's permit curbs (EPSG:4326, label, ~1 m detail): street shapes (Berkeley) as
    their edges."""
    g = geopandas.read_file(layers.fgb_path(city_key, "parking"))
    g = g[g["label"].str.startswith("Permit area ")][["label", "geometry"]].copy()
    poly = g.geometry.geom_type.isin(("Polygon", "MultiPolygon", "GeometryCollection"))
    g.loc[poly, "geometry"] = g.geometry[poly].make_valid().boundary
    g["geometry"] = g.geometry.simplify(1e-5)
    return g[~g.geometry.is_empty]


def _with_curbs(areas: geopandas.GeoDataFrame, curbs: geopandas.GeoDataFrame):
    """Areas, then the curbs (kind "curb") in their areas' colors: color for the first
    permit, color2 for a second one (the map stripes it)."""
    code = areas["label"].str.removeprefix("Permit area ")
    by = dict(zip(zip(areas["city"], code, strict=True), areas[layer_sources.COLOR], strict=True))
    c = curbs.copy()
    codes = c["label"].str.removeprefix("Permit area ").str.split(" or ")
    for n, col in enumerate((layer_sources.COLOR, "color2")):
        c[col] = [
            by.get((ck, ks[n])) if len(ks) > n else None
            for ck, ks in zip(c["city"], codes, strict=True)
        ]
    c["kind"] = "curb"
    return geopandas.GeoDataFrame(pd.concat([areas, c], ignore_index=True), crs="EPSG:4326")


def permit_areas(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """The map's copy of the region's permit areas (each city's permit_areas.fgb): where
    two cities' areas overlap (their borders, drawn by each), the newer source keeps the
    overlap; each area in its own color (_area_colors), edged darker when that is pale."""
    g = gdf.to_crs(3857).sort_values(["asof", "city"], ascending=False)
    taken = {}
    for i, row in g.iterrows():
        others = [geom for ck, geom in taken.items() if ck != row["city"]]
        geom = row.geometry.difference(shapely.union_all(others)) if others else row.geometry
        g.at[i, "geometry"] = geom
        taken[row["city"]] = shapely.union(taken.get(row["city"], shapely.Polygon()), geom)
    g = g[~g.geometry.is_empty].sort_index()
    g[layer_sources.COLOR] = _area_colors(g)
    g["edge"] = g[layer_sources.COLOR].map(_edge)
    return g.to_crs(4326)[["label", "city", layer_sources.COLOR, "edge", "geometry"]]


def build_region_geojson(region_key: str) -> None:
    """frontend/layers/<region>/<theme>.geojson from the cities' FGBs."""
    out_dir = _ROOT / "frontend" / "layers" / region_key
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme in layers.THEMES:
        parts, curbs = [], []
        for ck in REGIONS[region_key]["cities"]:
            # Parking: the permit areas by street block, and the permit curbs.
            p = layers.fgb_path(ck, layers.AREAS if theme == "parking" else theme)
            if p.exists():
                g = geopandas.read_file(p)
                if theme == "parking":
                    c = permit_curbs(ck)
                    g = snap_to_blocks(g.to_crs(3857), _city_streets(ck), c.to_crs(3857))
                    g = g.to_crs(4326)
                    curbs.append(c.assign(city=ck))
                g["city"] = ck
                parts.append(g)
        target = out_dir / f"{theme}.geojson"
        if not parts or not layers.THEMES[theme]["map"]:
            target.unlink(missing_ok=True)
            continue
        gdf = geopandas.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")
        if theme == "parking":
            gdf = _with_curbs(permit_areas(gdf), pd.concat(curbs, ignore_index=True))
        target.write_text(layer_sources.dump_json(gdf, theme))
        print(
            f"  {region_key}/{theme}.geojson: {len(gdf):,} features, "
            f"{target.stat().st_size / 1e6:.1f} MB"
        )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cities", nargs="*", choices=list(CITIES), help="cities (default: all)")
    ap.add_argument("--theme", action="append", choices=list(layers.THEMES))
    a = ap.parse_args()
    failed = []
    for ck in a.cities or CITIES:
        print(CITIES[ck]["name"])
        failed += [f"{ck}/{t}" for t in build_city(ck, a.theme)]
        if not a.theme:
            failed += [f"{ck}/{t}" for t in build_tables(ck)]
    for rk in REGIONS:
        build_region_geojson(rk)
    if failed:
        print("\nFailed (previous files kept): " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
