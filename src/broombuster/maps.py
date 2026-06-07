import datetime as _dt
import re as _re

import shapely
import shapely.geometry

from broombuster import analysis as _analysis
from broombuster.cities import CITIES as _CITIES


def _clean_desc(s: str) -> str:
    """Remove redundant phrases from schedule descriptions (e.g. SF '(every)')."""
    if not s or s == "N/A":
        return s
    s = _re.sub(r"\s*\(every\)", "", s, flags=_re.IGNORECASE)
    return _re.sub(r"\s+", " ", s).strip()

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
    body = "<br>".join(f"Sweeping: {ln}" for ln in lines) if lines else "Sweeping: N/A"
    return f"<b>{name}</b><br>{body}<br>"


# Ward number lives in the readable name ("Ward 05, Section 03"); the raw
# ward/section columns are not persisted to the FGB, so parse it from there.
_WARD_RE = _re.compile(r"ward\s*0*(\d+)", _re.IGNORECASE)


def _ward_ordinal(n: int) -> str:
    """Zero-padded ordinal ward number, e.g. 7 -> '07th', 22 -> '22nd'."""
    suffix = "th" if 10 <= (n % 100) <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:02d}{suffix}"


def _zone_pdf_url(row):
    """Per-ward official PDF schedule URL for a zone, or None when unavailable."""
    tmpl = (_CITIES.get(row.get("_city")) or {}).get("schedule_pdf_url")
    if not tmpl:
        return None
    m = _WARD_RE.search(str(row.get("STREET_DISPLAY") or row.get("STREET_NAME") or ""))
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


def _zone_year_html(code, local_now=None):
    """Full-year schedule for a 'DATES:' code: one cluster per line, past dimmed.

    Dates within a few days (analysis.cluster_dates) are grouped onto one line
    as a single sweeping occurrence. Returns None for non-DATES codes; "" when
    the code has no dates.
    """
    dates = _analysis.parse_dates_code(code)
    if dates is None:
        return None
    if not dates:
        return ""
    today = local_now.date() if local_now else _dt.date.today()
    clusters = _analysis.cluster_dates(dates)
    return "<br>".join(_format_cluster(cl, today) for cl in clusters)


def _zone_detail(row, local_now=None):
    """Click popup HTML: full-year schedule (past dimmed) plus a PDF link."""
    name = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))
    code = row.get("DAY_EVEN")
    year_html = _zone_year_html(code, local_now)
    if year_html is None:
        year_html = _clean_desc(_safe(row.get("DESC_EVEN")))
    body = year_html if year_html and year_html != "N/A" else "No sweeping scheduled"

    dates = _analysis.parse_dates_code(code)
    html = (
        f"<b>{name}</b><br>"
        f"<span class='zd-dates'>{body}</span>"
    )
    pdf = _zone_pdf_url(row)
    if pdf:
        year = dates[0].year if dates else ""
        link = f"{year} schedule".strip()
        html += (
            f"<br><a class='zd-link' href='{pdf}' target='_blank' "
            f"rel='noopener'>{link} ↗</a>"
        )
    return html


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


def build_map_geojson(
    myCar, myCity,
    schedule_even=None, schedule_odd=None, message=None, local_now=None,
    simplify_tolerance: float | None = None,
) -> dict:
    """Return zone data as a GeoJSON FeatureCollection for client-side rendering.

    `simplify_tolerance` is in degrees (the CRS the geometry is converted to
    before serialization). When provided, every feature's geometry is run
    through `geom.simplify(tolerance, preserve_topology=True)` before being
    serialized to GeoJSON. The expected use is sub-pixel simplification at
    the requested viewport — a typical browser viewport is ~1000 px wide,
    so a tolerance of `viewport_width_deg / 2000` is invisible to the user
    but can shrink Chicago polygon payloads from ~1.2 MB to ~100 KB at wide
    zooms.
    """
    schedule_even = schedule_even or []
    schedule_odd  = schedule_odd  or []

    myCity_ = myCity.to_crs("EPSG:4326")

    do_simplify = bool(simplify_tolerance and simplify_tolerance > 0)

    def _simplify(geom):
        if do_simplify and geom is not None:
            try:
                return geom.simplify(simplify_tolerance, preserve_topology=True)
            except (ValueError, TypeError):
                return geom
        return geom

    features = []

    # ------------------------------------------------------------------
    # Single pass over the (already clipped) GDF.
    #   - Polygon rows (Chicago ward sections) are emitted directly.
    #   - Line rows (Oakland / SF) accumulate into seg_data and are emitted
    #     as deduplicated segments after the loop.
    # No densification needed — MapLibre hit-tests along the full geometry.
    # ------------------------------------------------------------------
    def _seg_key(x, y):
        return frozenset({
            (round(x[0], 5), round(y[0], 5)),
            (round(x[-1], 5), round(y[-1], 5)),
        })

    def _side_entry(code, desc, time):
        # Raw (code, desc, time) tuple for one side, or None when there is no
        # renderable code. analysis.format_schedule_side does the no-sweep
        # filtering and canonical formatting later, so keep the raw values.
        if not isinstance(code, str) or code.strip() == "":
            return None
        d = _safe(desc)
        t = _safe(time)
        return (code, "" if d == "N/A" else d, "" if t == "N/A" else t)

    seg_data: dict = {}

    for _, row in myCity_.iterrows():
        geom = row["geometry"]
        if not hasattr(geom, "is_empty") or geom.is_empty:
            continue

        color = _sweeping_color(row, local_now=local_now)

        if isinstance(geom, _POLY_TYPES):
            fill_color, border_color = _zone_fill_color(color)
            hover  = _zone_hover(row, local_now)
            detail = _zone_detail(row, local_now)

            out_geom = _simplify(geom)
            if out_geom is None or out_geom.is_empty:
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
                    "hover_html":   hover,
                    "detail_html":  detail,
                },
            })
            continue

        # Line / multiline path
        pri   = _PRIORITY[color]
        be    = _side_entry(row.get("DAY_EVEN"), row.get("DESC_EVEN"), row.get("TIME_EVEN"))
        bo    = _side_entry(row.get("DAY_ODD"),  row.get("DESC_ODD"),  row.get("TIME_ODD"))
        # Use display name for UI rendering; fallback to stored STREET_NAME
        name  = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))

        for x, y in _geom_lines(geom):
            x, y = list(x), list(y)
            k = _seg_key(x, y)
            if k not in seg_data:
                seg_data[k] = {
                    "color": color, "pri": pri,
                    "x": x, "y": y, "name": name,
                    # Raw (code, desc, time) entries are ACCUMULATED across every
                    # row that shares this physical segment. SF's normalizer emits
                    # one row per (segment × weekday); without accumulation the
                    # hover would show one weekday while the colour reflects the
                    # union of all weekdays. format_schedule_side formats the
                    # union once, below. Dedup on first append.
                    "even": [be] if be else [],
                    "odd":  [bo] if bo else [],
                }
            else:
                sd = seg_data[k]
                # Color (and the geometry it shows on hover-pick) follows the
                # most-urgent row, but entries accumulate regardless of priority.
                if pri > sd["pri"]:
                    sd["pri"] = pri
                    sd["color"] = color
                    sd["x"] = x
                    sd["y"] = y
                if be and be not in sd["even"]:
                    sd["even"].append(be)
                if bo and bo not in sd["odd"]:
                    sd["odd"].append(bo)

    for sd in seg_data.values():
        color = sd["color"]
        # Canonical formatting (day-first, "Every <Wd>" merge, Mon->Sun order,
        # next-cluster dates) — identical to the card via format_schedule_side.
        evens = _analysis.format_schedule_side(sd["even"], local_now)
        odds  = _analysis.format_schedule_side(sd["odd"], local_now)
        # One schedule entry per line. Drop the Even/Odd labels when both sides
        # sweep identically; otherwise prefix each entry with its side.
        if evens and odds and evens == odds:
            sched_html = "<br>".join(evens)
        elif not evens and not odds:
            sched_html = "No sweeping data"
        else:
            lines = [f"Even: {e}" for e in evens] + [f"Odd: {o}" for o in odds]
            sched_html = "<br>".join(lines)

        hover      = f"<b>{sd['name']}</b><br>{sched_html}"
        line_width = _color_meta[color][1]

        # GeoJSON coords: [[lon, lat], ...]
        if simplify_tolerance and simplify_tolerance > 0 and len(sd["x"]) > 2:
            try:
                ls = shapely.geometry.LineString(zip(sd["x"], sd["y"]))
                ls_simple = ls.simplify(simplify_tolerance, preserve_topology=False)
                coords = [[float(x), float(y)] for x, y in ls_simple.coords]
            except (ValueError, TypeError):
                coords = [[float(lon), float(lat)] for lon, lat in zip(sd["x"], sd["y"])]
        else:
            coords = [[float(lon), float(lat)] for lon, lat in zip(sd["x"], sd["y"])]

        if len(coords) < 2:
            continue

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "render_type": "line",
                "domain":      "sweeping",
                "urgency":     color,
                "line_color":  color,
                "line_width":  line_width,
                "hover_html":  hover,
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


def _sched_entry(code, time, desc, side):
    """One raw schedule entry {code,time,desc,side}, or None for no-sweep codes."""
    if not isinstance(code, str) or code.strip() == "":
        return None
    if _analysis.is_no_sweep_code(code):
        return None
    t = _safe(time)
    d = _clean_desc(_safe(desc))
    return {
        "code": code,
        "time": t if t != "N/A" else "",
        "desc": d if d != "N/A" else "",
        "side": side,
    }


def merge_segment_rows(myCity):
    """Yield one tile record per physical feature with raw schedule codes.

    Each record: {geometry (shapely, EPSG:4326), render_type, street, city,
    schedule: [{code,time,side}, ...]}. Lines sharing endpoints are merged and
    their schedule entries unioned; polygons pass through 1:1.
    """
    myCity_ = myCity.to_crs("EPSG:4326")
    records = []

    def _seg_key(x, y):
        return frozenset({
            (round(x[0], 5), round(y[0], 5)),
            (round(x[-1], 5), round(y[-1], 5)),
        })

    seg_data: dict = {}

    for _, row in myCity_.iterrows():
        geom = row["geometry"]
        if not hasattr(geom, "is_empty") or geom.is_empty:
            continue
        city = _safe(row.get("_city"))
        name = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))

        if isinstance(geom, _POLY_TYPES):
            sched = []
            for entry in (
                _sched_entry(row.get("DAY_EVEN"), row.get("TIME_EVEN"),
                             row.get("DESC_EVEN"), "even"),
                _sched_entry(row.get("DAY_ODD"), row.get("TIME_ODD"),
                             row.get("DESC_ODD"), "odd"),
            ):
                if entry and entry not in sched:
                    sched.append(entry)
            records.append({
                "geometry":    geom,
                "render_type": "polygon",
                "street":      name,
                "city":        city,
                "schedule":    sched,
            })
            continue

        be = _sched_entry(row.get("DAY_EVEN"), row.get("TIME_EVEN"), row.get("DESC_EVEN"), "even")
        bo = _sched_entry(row.get("DAY_ODD"),  row.get("TIME_ODD"),  row.get("DESC_ODD"),  "odd")
        for x, y in _geom_lines(geom):
            x, y = list(x), list(y)
            k = _seg_key(x, y)
            sd = seg_data.get(k)
            if sd is None:
                sd = {"x": x, "y": y, "name": name, "city": city, "schedule": []}
                seg_data[k] = sd
            for entry in (be, bo):
                if entry and entry not in sd["schedule"]:
                    sd["schedule"].append(entry)

    for sd in seg_data.values():
        if len(sd["x"]) < 2:
            continue
        coords = list(zip(sd["x"], sd["y"]))
        records.append({
            "geometry":    shapely.geometry.LineString(coords),
            "render_type": "line",
            "street":      sd["name"],
            "city":        sd["city"],
            "schedule":    sd["schedule"],
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

def plot_map(myCar, myCity, schedule_even=None, schedule_odd=None, message=None, local_now=None):
    """Render the map in a browser tab (offline/CLI use only)."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly not installed; cannot render offline preview.")
        return

    geojson = build_map_geojson(myCar, myCity, schedule_even, schedule_odd, message, local_now)
    lats, lons = [], []
    for f in geojson["features"]:
        props = f["properties"]
        geom  = f["geometry"]
        if props["render_type"] == "line":
            coords = geom["coordinates"]
            lats.extend([c[1] for c in coords] + [None])
            lons.extend([c[0] for c in coords] + [None])

    fig = go.Figure(go.Scattermapbox(lat=lats, lon=lons, mode="lines"))
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=myCar.lat, lon=myCar.lon), zoom=15),
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
    )
    fig.show(config=dict(scrollZoom=True, displayModeBar=True, displaylogo=False))


# Keep old name as alias so any external callers don't break immediately.
def plot_map_dict(*args, **kwargs):
    raise NotImplementedError(
        "plot_map_dict() is removed. Use build_map_geojson() instead."
    )
