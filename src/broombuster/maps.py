import datetime as _dt
import html as _html
import re as _re

import shapely
import shapely.geometry

from broombuster import analysis as _analysis
from broombuster.cities import CITIES as _CITIES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(val):
    """Return a display-friendly string or 'N/A' for NaN / None / empty."""
    if val is None:
        return "N/A"
    s = str(val).strip()
    return s if s.upper() not in ("NAN", "NONE", "") else "N/A"


# Map analysis.compute_urgency's verdict to the urgency colour. The map colour
# and the car-card urgency now derive from this one function, so they cannot
# disagree (the long-running flicker/inconsistency bug).
_URGENCY_TO_COLOR = {"today": "tomato", "tomorrow": "orange"}


def _sweeping_color(row, local_now=None):
    """Return an urgency colour for a row via analysis.compute_urgency."""
    urgency = _analysis.compute_urgency(row, local_now=local_now)
    return _URGENCY_TO_COLOR.get(urgency, "cornflowerblue")


def _geom_lines(geom):
    """Yield (x_arr, y_arr) coordinate pairs for any drawable geometry type."""
    if isinstance(geom, shapely.geometry.LineString):
        yield geom.xy
    elif isinstance(geom, shapely.geometry.MultiLineString):
        for ls in geom.geoms:
            yield ls.xy
    elif isinstance(geom, shapely.geometry.Polygon):
        yield geom.exterior.xy
    elif isinstance(geom, shapely.geometry.MultiPolygon):
        for poly in geom.geoms:
            yield poly.exterior.xy


# ---------------------------------------------------------------------------
# Zone colour palette
# ---------------------------------------------------------------------------

# Urgency-color RGB values used for both zone fill and border.
_URGENCY_RGB = {
    "tomato":         (220, 60,  60),
    "orange":         (230, 130, 20),
    "cornflowerblue": (80,  110, 180),
}

# Border opacity is always high so urgency reads clearly.
_URGENCY_BORDER_ALPHA = {
    "tomato":         0.90,
    "orange":         0.80,
    "cornflowerblue": 0.40,
}

# Fill alpha: urgent zones get higher opacity; clear zones stay subtle.
_URGENCY_FILL_ALPHA = {
    "tomato":         0.55,
    "orange":         0.40,
    "cornflowerblue": 0.18,
}


def _zone_fill_color(urgency: str):
    """Return (fill_rgba, border_rgba) for a polygon zone.

    Both fill and border use the urgency colour (red/orange/blue) so each
    zone's background signals its sweep status. Fill alpha is lighter for
    clear zones and heavier for today/tomorrow; border alpha stays high.
    """
    ur, ug, ub = _URGENCY_RGB[urgency]
    ba = _URGENCY_BORDER_ALPHA[urgency]
    fa = _URGENCY_FILL_ALPHA[urgency]
    fill   = f"rgba({ur},{ug},{ub},{fa:.2f})"
    border = f"rgba({ur},{ug},{ub},{ba:.2f})"
    return fill, border


# ---------------------------------------------------------------------------
# Hover text helpers
# ---------------------------------------------------------------------------

def _zone_hover(row, local_now=None):
    # Prefer the human-friendly display name for UI; fall back to stored STREET_NAME
    name = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))
    # Polygon zones (Chicago 'DATES:') hover via the shared formatter \u2014 shows
    # only the next sweep cluster, identical to the card.
    entries = [
        (row.get("DAY_EVEN"), _safe(row.get("DESC_EVEN")), _safe(row.get("TIME_EVEN"))),
        (row.get("DAY_ODD"),  _safe(row.get("DESC_ODD")),  _safe(row.get("TIME_ODD"))),
    ]
    lines = _analysis.format_schedule_side(entries, local_now)
    body = ("<br>".join(f"Sweeping: {_html.escape(ln)}" for ln in lines)
            if lines else "Sweeping: N/A")
    return f"<b>{_html.escape(name)}</b><br>{body}<br>"


# Ward number lives in the readable name ("Ward 05, Section 03"); the raw
# ward/section columns are not persisted to the FGB, so parse it from there.
_WARD_RE = _re.compile(r"ward\s*0*(\d+)", _re.IGNORECASE)


def _ward_ordinal(n: int) -> str:
    """Zero-padded ordinal ward number, e.g. 7 -> '07th', 22 -> '22nd'."""
    suffix = "th" if 10 <= (n % 100) <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:02d}{suffix}"


def _zone_pdf_url(row):
    """Per-ward official PDF schedule URL for a zone, or None when unavailable."""
    return _pdf_url(row.get("STREET_DISPLAY") or row.get("STREET_NAME"), row.get("_city"))


def _pdf_url(name, city):
    """Per-ward PDF URL from a zone name ("Ward 05, ...") and city key, or None."""
    tmpl = (_CITIES.get(city) or {}).get("schedule_pdf_url")
    if not tmpl:
        return None
    m = _WARD_RE.search(str(name or ""))
    if not m:
        return None
    return tmpl.format(ward=_ward_ordinal(int(m.group(1))))


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
            cells.append(f"{_analysis._MONTH_ABBR[d.month]} {day_txt}")
            last_month = d.month
        else:
            cells.append(day_txt)
    line = ", ".join(cells)
    return f"<span class='zd-past'>{line}</span>" if all_past else line


def _year_html(dates, local_now=None):
    """Full-year dates, one cluster (analysis.cluster_dates) per line, past dimmed."""
    today = local_now.date() if local_now else _dt.date.today()
    return "<br>".join(_format_cluster(cl, today) for cl in _analysis.cluster_dates(dates))


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
        parts.append(_year_html(dates, local_now))
    body = "<br>".join(parts) or "No sweeping scheduled"
    html = (
        f"<b>{_html.escape(_safe(name))}</b><br>"
        f"<span class='zd-dates'>{body}</span>"
    )
    pdf = _pdf_url(name, city)
    if pdf:
        link = f"{dates[0].year} schedule" if dates else "schedule"
        html += (
            f"<br><a class='zd-link' href='{_html.escape(pdf, quote=True)}' "
            f"target='_blank' rel='noopener'>{link} ↗</a>"
        )
    return html


def _zone_detail(row, local_now=None):
    """zone_detail_html for one GDF row."""
    even, odd = _analysis.schedules_for_segment(row)
    return zone_detail_html(
        row.get("STREET_DISPLAY") or row.get("STREET_NAME"), even, odd,
        row.get("_city"), local_now, labels=_analysis.side_labels(row),
    )


# ---------------------------------------------------------------------------
# Main GeoJSON builder
# ---------------------------------------------------------------------------

_color_meta = {
    "tomato":         ("Sweeping today",    3.0),
    "orange":         ("Sweeping tomorrow", 3.0),
    "cornflowerblue": ("No sweeping soon",  3.0),
}

_POLY_TYPES = (shapely.geometry.Polygon, shapely.geometry.MultiPolygon)
_PRIORITY   = {"tomato": 2, "orange": 1, "cornflowerblue": 0}


def _side_entry(code, desc, time):
    """Raw (code, desc, time) for one side, or None for missing / no-sweep codes."""
    if not isinstance(code, str) or code.strip() == "" or _analysis.is_no_sweep_code(code):
        return None
    d, t = _safe(desc), _safe(time)
    return (code, "" if d == "N/A" else d, "" if t == "N/A" else t)


def _merge_lines(myCity_4326, on_row=None):
    """Merge line rows sharing endpoints (analysis.line_key) into one segment each.

    SF emits one row per (segment x weekday x side), so a physical line is the
    union of every row sharing its endpoints. Returns {key: segment dict} with
    x/y, name, city, labels, even/odd raw entries, and whatever `on_row(sd,
    row, x, y)` adds per contributing row.
    """
    seg_data: dict = {}
    for _, row in myCity_4326.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty or isinstance(geom, _POLY_TYPES):
            continue
        be = _side_entry(row.get("DAY_EVEN"), row.get("DESC_EVEN"), row.get("TIME_EVEN"))
        bo = _side_entry(row.get("DAY_ODD"), row.get("DESC_ODD"), row.get("TIME_ODD"))
        labels = _analysis.side_labels(row)
        for x, y in _geom_lines(geom):
            x, y = list(x), list(y)
            if len(x) < 2:
                continue
            k = _analysis.line_key(list(zip(x, y)))
            sd = seg_data.get(k)
            if sd is None:
                sd = seg_data[k] = {
                    "x": x, "y": y, "city": _safe(row.get("_city")),
                    "name": _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME")),
                    "labels": labels, "even": [], "odd": [],
                }
            elif labels != _analysis.DEFAULT_SIDE_LABELS:
                sd["labels"] = labels
            if be and be not in sd["even"]:
                sd["even"].append(be)
            if bo and bo not in sd["odd"]:
                sd["odd"].append(bo)
            if on_row:
                on_row(sd, row, x, y)
    return seg_data


def build_map_geojson(myCity, local_now=None, simplify_tolerance: float | None = None) -> dict:
    """Return zone data as a GeoJSON FeatureCollection for client-side rendering.

    `simplify_tolerance` (degrees) runs every geometry through `simplify`
    before serialization; `viewport_width_deg / 2000` is sub-pixel for a
    ~1000 px viewport.
    """
    myCity_ = myCity.to_crs("EPSG:4326")
    do_simplify = bool(simplify_tolerance and simplify_tolerance > 0)
    features = []

    # Polygon rows (Chicago ward sections) pass through 1:1.
    for _, row in myCity_.iterrows():
        geom = row["geometry"]
        if geom is None or geom.is_empty or not isinstance(geom, _POLY_TYPES):
            continue
        color = _sweeping_color(row, local_now=local_now)
        fill_color, border_color = _zone_fill_color(color)
        out_geom = (geom.simplify(simplify_tolerance, preserve_topology=True)
                    if do_simplify else geom)
        if out_geom.is_empty:
            continue
        features.append({
            "type": "Feature",
            "geometry": shapely.geometry.mapping(out_geom),
            "properties": {
                "render_type":  "polygon",
                "domain":       "sweeping",
                "urgency":      color,
                "fill_color":   fill_color,
                "border_color": border_color,
                "hover_html":   _zone_hover(row, local_now),
                "detail_html":  _zone_detail(row, local_now),
            },
        })

    # Line rows: the colour (and drawn geometry) follow the most urgent row;
    # schedule entries accumulate across every row of the segment.
    def _on_row(sd, row, x, y):
        color = _sweeping_color(row, local_now=local_now)
        if _PRIORITY[color] > sd.get("pri", -1):
            sd["pri"], sd["color"], sd["x"], sd["y"] = _PRIORITY[color], color, x, y

    for sd in _merge_lines(myCity_, _on_row).values():
        color = sd["color"]
        lines = _analysis.side_lines(sd["even"], sd["odd"], None, sd["labels"], local_now)
        sched_html = "<br>".join(_html.escape(ln) for ln in lines) or "No sweeping data"
        coords = list(zip(sd["x"], sd["y"]))
        if do_simplify and len(coords) > 2:
            coords = list(shapely.geometry.LineString(coords)
                          .simplify(simplify_tolerance, preserve_topology=False).coords)
        if len(coords) < 2:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[float(x), float(y)] for x, y in coords],
            },
            "properties": {
                "render_type": "line",
                "domain":      "sweeping",
                "urgency":     color,
                "line_color":  color,
                "line_width":  _color_meta[color][1],
                "hover_html":  f"<b>{_html.escape(sd['name'])}</b><br>{sched_html}",
            },
        })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Tile-feature model (PMTiles build pipeline)
# ---------------------------------------------------------------------------
#
# merge_segment_rows() produces ONE record per physical feature carrying the
# raw schedule codes — no urgency colour, no date-dependent HTML. The client
# (urgency.js) computes colour from these codes against the current date, so
# the tiles themselves stay date-independent and only need rebuilding when the
# underlying data refreshes. This mirrors build_map_geojson's segment dedup:
# SF emits one row per (segment x weekday), so a physical line is the union of
# every row sharing its endpoints.


def _tile_sched(even, odd):
    """Tile schedule entries [{code,time,desc,side}] for raw even / odd entries."""
    out = []
    for side, entries in (("even", even), ("odd", odd)):
        for entry in entries:
            clean = _side_entry(*entry)
            if clean is None:
                continue
            e = {"code": clean[0], "time": clean[2], "desc": clean[1], "side": side}
            if e not in out:
                out.append(e)
    return out


def merge_segment_rows(myCity):
    """One tile record per physical feature with raw schedule codes.

    Each record: {geometry (shapely, EPSG:4326), render_type, street, city,
    schedule: [{code,time,desc,side}, ...], labels: (even, odd)}. Lines
    sharing endpoints are merged (_merge_lines); polygons pass through 1:1.
    """
    myCity_ = myCity.to_crs("EPSG:4326")
    records = []
    for _, row in myCity_.iterrows():
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
    for sd in _merge_lines(myCity_).values():
        records.append({
            "geometry":    shapely.geometry.LineString(list(zip(sd["x"], sd["y"]))),
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
        if not m:
            continue
        geom = rec.get("geometry")
        if geom is None or geom.is_empty:
            continue
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


# ---------------------------------------------------------------------------
# Legacy offline preview (CLI only — not used by the API)
# ---------------------------------------------------------------------------

def plot_map(myCar, myCity, local_now=None):
    """Render the map in a browser tab (offline/CLI use only)."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly not installed; cannot render offline preview.")
        return

    geojson = build_map_geojson(myCity, local_now)
    lats, lons = [], []
    for f in geojson["features"]:
        if f["properties"]["render_type"] == "line":
            coords = f["geometry"]["coordinates"]
            lats.extend([c[1] for c in coords] + [None])
            lons.extend([c[0] for c in coords] + [None])

    fig = go.Figure(go.Scattermapbox(lat=lats, lon=lons, mode="lines"))
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=myCar.lat, lon=myCar.lon), zoom=15),
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
    )
    fig.show(config=dict(scrollZoom=True, displayModeBar=True, displaylogo=False))
