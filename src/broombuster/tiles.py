"""PMTiles feature model: one record per physical feature with raw schedule codes.

Tiles carry no urgency or dates; the client colors them against its clock, so
they only need rebuilding when the data changes.
"""

import re

import numpy as np
import shapely
import shapely.geometry

from broombuster import analysis as _analysis
from broombuster import normalize, resolve


def _safe(val) -> str:
    """Display text, or 'N/A' for missing / placeholder values."""
    return normalize.clean_text(val) or "N/A"


# Chicago zone names read "Ward 05, Section 03".
_WARD_RE = re.compile(r"ward\s*0*(\d+)", re.IGNORECASE)


_POLY_TYPES = (shapely.geometry.Polygon, shapely.geometry.MultiPolygon)
_ROW_COLS = (
    "STREET_NAME",
    "STREET_DISPLAY",
    "_city",
    "SIDE_EVEN",
    "SIDE_ODD",
    "L_F_ADD",
    "L_T_ADD",
    "R_F_ADD",
    "R_T_ADD",
    "DAY_EVEN",
    "DESC_EVEN",
    "TIME_EVEN",
    "DAY_ODD",
    "DESC_ODD",
    "TIME_ODD",
)


def _records(gdf, rows) -> list[dict]:
    """The rows at positions `rows` as dicts of the columns tiles read."""
    cols = [c for c in _ROW_COLS if c in gdf]
    return gdf.iloc[rows][cols].to_dict("records")


def _groups(parts, keys, cities) -> np.ndarray:
    """Group label (its first part's index) of each line part: parts that draw one street
    (analysis.coincide), transitively."""
    i, j = _analysis.near_pairs(parts, parts)
    i, j = i[i < j], j[i < j]
    ok = _analysis.coincide(parts, keys, cities, i, j)
    root = list(range(len(parts)))

    def find(x):
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for a, b in zip(i[ok].tolist(), j[ok].tolist(), strict=True):
        ra, rb = find(a), find(b)
        if ra != rb:
            root[max(ra, rb)] = min(ra, rb)
    return np.array([find(x) for x in range(len(parts))], dtype=int)


def _merge_lines(gdf):
    """Merge the line rows that draw one street (analysis.coincide, as the card's union) into
    one segment each.

    SF emits one row per (segment x weekday x side) and border streets are drawn by two
    cities. Returns [segment dict] with coords (EPSG:4326, the group's first part), name,
    city, labels, the even/odd raw entries and even_left: every answer its rows gave to
    "is the even side left of `coords`' direction?".
    """
    g3857 = gdf.to_crs("EPSG:3857")
    parts, prow = _analysis.line_parts(g3857.geometry.values)
    parts4326, _ = _analysis.line_parts(gdf.to_crs("EPSG:4326").geometry.values)
    group = _groups(parts, _analysis.street_keys(gdf)[prow], _analysis._cities(gdf)[prow])
    a0, a1 = _analysis._ends(parts)
    info = {}  # row -> (row dict, even entry, odd entry, labels, even_on_left)
    for n, row in zip(np.unique(prow).tolist(), _records(gdf, np.unique(prow)), strict=True):
        sched = _analysis.get_schedule(row, 0), _analysis.get_schedule(row, 1)
        info[n] = (row, *sched, _analysis.side_labels(row), resolve.even_on_left(row))

    segs: dict = {}
    for p, (g, n) in enumerate(zip(group.tolist(), prow.tolist(), strict=True)):
        row, even, odd, labels, left = info[n]
        sd = segs.get(g)
        if sd is None:  # p == g: the group's first part
            sd = segs[g] = {
                "coords": list(parts4326[p].coords),
                "city": _safe(row.get("_city")),
                "name": _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME")),
                "labels": labels,
                "even": [],
                "odd": [],
                "even_left": set(),
            }
        if left is not None:
            # A part drawn the other way round has its sides swapped.
            same = np.hypot(*(a0[p] - a0[g])) <= np.hypot(*(a0[p] - a1[g]))
            sd["even_left"].add(left == bool(same))
        if labels != _analysis.DEFAULT_SIDE_LABELS:
            sd["labels"] = labels
        if even and even not in sd["even"]:
            sd["even"].append(even)
        if odd and odd not in sd["odd"]:
            sd["odd"].append(odd)
    return list(segs.values())


def _tile_sched(even, odd):
    """Tile schedule entries [{code,time,desc,side}] for (code, desc, time) entries."""
    out = []
    for side, entries in (("even", even), ("odd", odd)):
        for code, desc, time in entries:
            e = {"code": code, "time": time, "desc": desc, "side": side}
            if e not in out:
                out.append(e)
    return out


def merge_segment_rows(gdf):
    """One tile record per physical feature with raw schedule codes.

    Each record: {geometry (shapely, EPSG:4326), render_type, street, city,
    schedule: [{code,time,desc,side}, ...], labels: (even, odd)}. Lines
    drawing one street are merged (_merge_lines); polygons pass through 1:1.
    """
    gdf_4326 = gdf.to_crs("EPSG:4326")
    geoms = gdf_4326.geometry.values
    tid = shapely.get_type_id(np.asarray(geoms, dtype=object))
    polys = np.flatnonzero(((tid == 3) | (tid == 6)) & ~shapely.is_empty(geoms))
    records = []
    for p, row in zip(polys.tolist(), _records(gdf_4326, polys), strict=True):
        even, odd = _analysis.schedules_for_segment(row)
        records.append(
            {
                "geometry": geoms[p],
                "render_type": "polygon",
                "street": _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME")),
                "city": _safe(row.get("_city")),
                "schedule": _tile_sched(even, odd),
                "labels": _analysis.side_labels(row),
            }
        )
    for sd in _merge_lines(gdf):
        records.append(
            {
                "geometry": shapely.geometry.LineString(sd["coords"]),
                "render_type": "line",
                "street": sd["name"],
                "city": sd["city"],
                "schedule": _tile_sched(sd["even"], sd["odd"]),
                "labels": sd["labels"],
                "even_left": _even_left(sd),
            }
        )
    return records


def _even_left(sd):
    """Whether the even side is left of the line's direction, for a line the map draws
    as two (one per side); None when its rows disagree or don't say, or when both sides
    share a schedule (one line tells it all)."""
    if len(sd["even_left"]) != 1 or set(sd["even"]) == set(sd["odd"]):
        return None
    return next(iter(sd["even_left"]))


def ward_boundary_features(records):
    """Yield one line feature tracing the boundaries BETWEEN different wards.

    Sections are dissolved per ward (parsed from "Ward 05, Section 03"); only
    edges shared by two different wards are kept and merged, so each divider is
    drawn exactly once. Same-ward gaps (disjoint section groups) and the outer
    perimeter are excluded — the per-section outlines already cover those — so
    there are no orphan or doubled lines. Empty for line-only regions.
    """
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    by_ward: dict = {}
    for rec in records:
        if rec.get("render_type") != "polygon":
            continue
        m = _WARD_RE.search(str(rec.get("street") or ""))
        geom = rec.get("geometry")
        if m and geom is not None and not geom.is_empty:
            by_ward.setdefault(int(m.group(1)), []).append(geom)

    if len(by_ward) < 2:
        return []

    polys = []
    for _ward, geoms in sorted(by_ward.items()):
        try:
            polys.append(unary_union(geoms).buffer(0))
        except Exception:  # noqa: S112 (skip a ward whose geometry will not merge)
            continue

    tree = STRtree(polys)
    shared = []
    for i, p in enumerate(polys):
        pb = p.boundary
        for j in tree.query(p):
            if j <= i:
                continue
            qb = polys[j].boundary
            if not pb.intersects(qb):
                continue
            inter = pb.intersection(qb)
            for part in getattr(inter, "geoms", [inter]):
                if part.geom_type in ("LineString", "MultiLineString") and not part.is_empty:
                    shared.append(part)

    if not shared:
        return []
    merged = unary_union(shared)
    if merged is None or merged.is_empty:
        return []
    return [
        {
            "geometry": merged,
            "render_type": "ward_boundary",
            "street": "",
            "city": "",
            "schedule": [],
        }
    ]
