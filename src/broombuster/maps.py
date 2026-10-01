"""Map-facing outputs: the PMTiles feature model and the zone/card detail HTML.

merge_segment_rows() produces ONE record per physical feature carrying the raw
schedule codes — no urgency colour, no date-dependent HTML. The client
(urgency.js) computes colour from these codes against the current date, so the
tiles stay date-independent and only need rebuilding when the data refreshes.
"""

import calendar
import datetime as _dt
import html as _html
import re as _re

import shapely
import shapely.geometry

from broombuster import analysis as _analysis
from broombuster import normalize
from broombuster.cities import CITIES as _CITIES


def _safe(val) -> str:
    """Display-friendly text, or 'N/A' for missing / placeholder values."""
    return normalize.clean_text(val) or "N/A"


# ---------------------------------------------------------------------------
# Zone / card detail window
# ---------------------------------------------------------------------------

# Ward number lives in the readable name ("Ward 05, Section 03"); the raw
# ward/section columns are not persisted to the FGB, so parse it from there.
_WARD_RE = _re.compile(r"ward\s*0*(\d+)", _re.IGNORECASE)


def _ward_ordinal(n: int) -> str:
    """Zero-padded ordinal ward number, e.g. 7 -> '07th', 22 -> '22nd'."""
    suffix = "th" if 10 <= (n % 100) <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:02d}{suffix}"


def _pdf_url(name, city):
    """Per-ward PDF URL from a zone name ("Ward 05, ...") and city key, or None."""
    tmpl = (_CITIES.get(city) or {}).get("schedule_pdf_url")
    m = _WARD_RE.search(str(name or "")) if tmpl else None
    return tmpl.format(ward=_ward_ordinal(int(m.group(1)))) if m else None


def _format_cluster(cluster, today):
    """One line for a back-to-back date cluster, e.g. "Apr 17, 18".

    Past day numbers are dimmed individually; if the whole cluster is past,
    the entire line (month included) is wrapped for dimming.
    """
    all_past = all(d < today for d in cluster)
    cells, last_month = [], None
    for d in cluster:
        day_txt = str(d.day)
        if not all_past and d < today:
            day_txt = f"<span class='zd-past'>{day_txt}</span>"
        if d.month != last_month:
            cells.append(f"{calendar.month_abbr[d.month]} {day_txt}")
            last_month = d.month
        else:
            cells.append(day_txt)
    line = ", ".join(cells)
    return f"<span class='zd-past'>{line}</span>" if all_past else line


def zone_detail_html(name, even, odd, city=None, local_now=None, car_side=None,
                     labels=_analysis.DEFAULT_SIDE_LABELS):
    """Click / car-card detail HTML for every schedule entry on both sides.

    'DATES:' codes render as the full year (past dimmed); weekly codes as the
    canonical side lines. Adds the ward PDF link when the city has one.
    """
    dates = sorted({d for e in list(even) + list(odd)
                    for d in (_analysis.parse_dates_code(e[0]) or [])})
    weekly_even = [e for e in even if _analysis.parse_dates_code(e[0]) is None]
    weekly_odd = [e for e in odd if _analysis.parse_dates_code(e[0]) is None]
    parts = [_html.escape(ln) for ln in _analysis.side_lines(
        weekly_even, weekly_odd, car_side, labels, local_now)]
    if dates:
        today = local_now.date() if local_now else _dt.date.today()
        parts.append("<br>".join(_format_cluster(cl, today)
                                 for cl in _analysis.cluster_dates(dates)))
    body = "<br>".join(parts) or "No sweeping scheduled"
    html = f"<b>{_html.escape(_safe(name))}</b><br><span class='zd-dates'>{body}</span>"
    pdf = _pdf_url(name, city)
    if pdf:
        link = f"{dates[0].year} schedule" if dates else "schedule"
        html += (
            f"<br><a class='zd-link' href='{_html.escape(pdf, quote=True)}' "
            f"target='_blank' rel='noopener'>{link} ↗</a>"
        )
    return html


# ---------------------------------------------------------------------------
# Tile-feature model (PMTiles build pipeline)
# ---------------------------------------------------------------------------

_POLY_TYPES = (shapely.geometry.Polygon, shapely.geometry.MultiPolygon)


def _line_parts(geom):
    """Coordinate lists of every LineString part of a line geometry."""
    if isinstance(geom, shapely.geometry.LineString):
        return [list(geom.coords)]
    if isinstance(geom, shapely.geometry.MultiLineString):
        return [list(ls.coords) for ls in geom.geoms]
    return []


def _merge_lines(gdf_4326):
    """Merge line rows sharing endpoints (analysis.line_key) into one segment each.

    SF emits one row per (segment x weekday x side), so a physical line is the
    union of every row sharing its endpoints. Returns {key: segment dict} with
    coords, name, city, labels and the even/odd raw entries.
    """
    seg_data: dict = {}
    for _, row in gdf_4326.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty or isinstance(geom, _POLY_TYPES):
            continue
        be, bo = _analysis.get_schedule(row, 0), _analysis.get_schedule(row, 1)
        labels = _analysis.side_labels(row)
        for coords in _line_parts(geom):
            if len(coords) < 2:
                continue
            sd = seg_data.setdefault(_analysis.line_key(coords), {
                "coords": coords, "city": _safe(row.get("_city")),
                "name": _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME")),
                "labels": labels, "even": [], "odd": [],
            })
            if labels != _analysis.DEFAULT_SIDE_LABELS:
                sd["labels"] = labels
            if be and be not in sd["even"]:
                sd["even"].append(be)
            if bo and bo not in sd["odd"]:
                sd["odd"].append(bo)
    return seg_data


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
    sharing endpoints are merged (_merge_lines); polygons pass through 1:1.
    """
    gdf_4326 = gdf.to_crs("EPSG:4326")
    records = []
    for _, row in gdf_4326.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty or not isinstance(geom, _POLY_TYPES):
            continue
        even, odd = _analysis.schedules_for_segment(row)
        records.append({
            "geometry":    geom,
            "render_type": "polygon",
            "street":      _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME")),
            "city":        _safe(row.get("_city")),
            "schedule":    _tile_sched(even, odd),
            "labels":      _analysis.side_labels(row),
        })
    for sd in _merge_lines(gdf_4326).values():
        records.append({
            "geometry":    shapely.geometry.LineString(sd["coords"]),
            "render_type": "line",
            "street":      sd["name"],
            "city":        sd["city"],
            "schedule":    _tile_sched(sd["even"], sd["odd"]),
            "labels":      sd["labels"],
        })
    return records


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
        except Exception:
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
    return [{
        "geometry":    merged,
        "render_type": "ward_boundary",
        "street":      "",
        "city":        "",
        "schedule":    [],
    }]
