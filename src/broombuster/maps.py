import re as _re
from datetime import date, timedelta

import numpy as np
import shapely
import shapely.geometry

import analysis as _analysis
import normalize as _normalize


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


def _sweeping_color(row, local_now=None):
    """Return an urgency color string based on sweeping schedule."""
    today    = local_now.date() if local_now else date.today()
    tomorrow = today + timedelta(days=1)

    def has_sweep_on(day_code, check_date):
        s = _safe(day_code)
        if s in ("N/A", "N", "NS", "O"):
            return False
        try:
            return check_date in _analysis.parse_sweeping_code(s)
        except Exception:
            return False

    def is_done(time_key):
        if local_now is None:
            return False
        time_str = _safe(row.get(time_key))
        if time_str in ("N/A", ""):
            return False
        _, end_t = _analysis._parse_time_range(time_str)
        return end_t is not None and local_now.time() > end_t

    if has_sweep_on(row.get("DAY_EVEN"), today) and not is_done("TIME_EVEN"):
        return "tomato"
    if has_sweep_on(row.get("DAY_ODD"), today) and not is_done("TIME_ODD"):
        return "tomato"
    if has_sweep_on(row.get("DAY_EVEN"), tomorrow) or has_sweep_on(row.get("DAY_ODD"), tomorrow):
        return "orange"
    return "cornflowerblue"


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

_ZONE_PALETTE = [
    ("Crimson",    220, 50,  60),
    ("Coral",      255, 100, 80),
    ("Tomato",     255, 70,  47),
    ("Salmon",     248, 138, 105),
    ("Amber",      255, 185, 15),
    ("Goldenrod",  218, 160, 30),
    ("Tangerine",  255, 145, 0),
    ("Khaki",      195, 170, 65),
    ("Lime",       128, 190, 48),
    ("Olive",      120, 150, 52),
    ("Teal",       38,  155, 140),
    ("Turquoise",  52,  182, 168),
    ("Sky",        75,  175, 215),
    ("Steelblue",  70,  130, 180),
    ("Royalblue",  60,  100, 220),
    ("Periwinkle", 118, 138, 218),
    ("Lavender",   148, 112, 202),
    ("Plum",       172, 72,  168),
    ("Orchid",     192, 92,  172),
    ("Rose",       225, 82,  112),
]

# Urgency-color RGB values used for border and urgent fill.
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

# Fill alpha: urgent zones get higher opacity; non-urgent stay subtle.
_URGENCY_FILL_ALPHA = {
    "tomato":         0.55,
    "orange":         0.40,
    "cornflowerblue": 0.18,  # palette fill for non-urgent
}


def _zone_fill_color(w: int, s: int, urgency: str):
    """Return (fill_rgba, border_rgba, color_name) for a polygon zone.

    Border: always urgency color so roads clearly signal sweep day.
    Fill: urgency color for today/tomorrow; palette color for non-urgent
          (helps distinguish adjacent zones visually).
    """
    ur, ug, ub = _URGENCY_RGB[urgency]
    ba = _URGENCY_BORDER_ALPHA[urgency]
    fa = _URGENCY_FILL_ALPHA[urgency]

    border = f"rgba({ur},{ug},{ub},{ba:.2f})"

    if urgency == "cornflowerblue":
        # Non-urgent: fill with palette colour so zones are distinguishable.
        idx = (w * 100 + s) % len(_ZONE_PALETTE)
        name, r, g, b = _ZONE_PALETTE[idx]
        fill = f"rgba({r},{g},{b},{fa:.2f})"
    else:
        # Urgent: fill with the urgency colour itself.
        name = "Urgent"
        fill = f"rgba({ur},{ug},{ub},{fa:.2f})"

    return fill, border, name


# ---------------------------------------------------------------------------
# Hover text helpers
# ---------------------------------------------------------------------------

def _hover_side(desc, time, label):
    d = _clean_desc(_safe(desc))
    t = _normalize.time_display(_safe(time))
    body = d if (t in ("N/A", "") or t in d) else f"{d} \u2014 {t}"
    return f"{label}: {body}"


def _zone_hover(row):
    # Prefer the human-friendly display name for UI; fall back to stored STREET_NAME
    name = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))
    return (
        f"<b>{name}</b><br>"
        + _hover_side(row.get("DESC_EVEN"), row.get("TIME_EVEN"), "Sweeping") + "<br>"
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


def build_map_geojson(myCar, myCity, schedule_even=None, schedule_odd=None, message=None, local_now=None) -> dict:
    """Return zone data as a GeoJSON FeatureCollection for client-side rendering."""
    schedule_even = schedule_even or []
    schedule_odd  = schedule_odd  or []

    myCity_ = myCity.to_crs("EPSG:4326")

    # ------------------------------------------------------------------
    # Pre-compute urgency color for every row
    # ------------------------------------------------------------------
    _row_color: dict = {}
    for _idx, _row in myCity_.iterrows():
        _row_color[_idx] = _sweeping_color(_row, local_now=local_now)

    features = []

    # ------------------------------------------------------------------
    # Polygon zones (e.g. Chicago ward sections)
    # ------------------------------------------------------------------
    for _, row in myCity_.iterrows():
        geom = row["geometry"]
        if not hasattr(geom, "is_empty") or geom.is_empty:
            continue
        if not isinstance(geom, _POLY_TYPES):
            continue

        color = _row_color[_]
        try:
            w = int(float(row.get("ward_id") or row.get("ward") or 0))
            s = int(float(row.get("section_id") or row.get("section") or 0))
        except (TypeError, ValueError):
            w, s = 0, 0

        fill_color, border_color, _ = _zone_fill_color(w, s, color)
        hover = _zone_hover(row)

        features.append({
            "type": "Feature",
            "geometry": shapely.geometry.mapping(geom),
            "properties": {
                "render_type":  "polygon",
                "urgency":      color,
                "fill_color":   fill_color,
                "border_color": border_color,
                "hover_html":   hover,
            },
        })

    # ------------------------------------------------------------------
    # Line street rendering (Oakland / SF)
    # Deduplicates segments and merges even/odd schedule text.
    # No densification needed — MapLibre hit-tests along the full geometry.
    # ------------------------------------------------------------------
    def _seg_key(x, y):
        return frozenset({
            (round(x[0], 5), round(y[0], 5)),
            (round(x[-1], 5), round(y[-1], 5)),
        })

    def _side_body(desc, time):
        d = _clean_desc(_safe(desc))
        t = _normalize.time_display(_safe(time))
        if d in ("N/A", ""):
            return None
        return d if t in ("N/A", "") or t in d else f"{d} \u2014 {t}"

    seg_data: dict = {}

    for _, row in myCity_.iterrows():
        geom = row["geometry"]
        if not hasattr(geom, "is_empty") or geom.is_empty:
            continue
        if isinstance(geom, _POLY_TYPES):
            continue
        color = _row_color[_]
        pri   = _PRIORITY[color]
        be    = _side_body(row.get("DESC_EVEN"), row.get("TIME_EVEN"))
        bo    = _side_body(row.get("DESC_ODD"),  row.get("TIME_ODD"))
        # Use display name for UI rendering; fallback to stored STREET_NAME
        name  = _safe(row.get("STREET_DISPLAY") or row.get("STREET_NAME"))

        for x, y in _geom_lines(geom):
            x, y = list(x), list(y)
            k = _seg_key(x, y)
            if k not in seg_data:
                seg_data[k] = {
                    "color": color, "pri": pri,
                    "x": x, "y": y, "name": name,
                    "even": [be] if be else [],
                    "odd":  [bo] if bo else [],
                }
            else:
                sd = seg_data[k]
                if pri > sd["pri"]:
                    sd["pri"] = pri; sd["color"] = color
                    sd["x"] = x;    sd["y"] = y
                    if be: sd["even"] = [be]
                    if bo: sd["odd"]  = [bo]
                else:
                    if be and not sd["even"]: sd["even"] = [be]
                    if bo and not sd["odd"]:  sd["odd"]  = [bo]

    for sd in seg_data.values():
        evens = sd["even"]
        odds  = sd["odd"]
        color = sd["color"]
        if evens and odds and evens == odds:
            sched_html = f"Street: {' / '.join(evens)}"
        elif not evens and not odds:
            sched_html = "No sweeping data"
        else:
            parts = []
            if evens: parts.append("Even: " + " / ".join(evens))
            if odds:  parts.append("Odd: "  + " / ".join(odds))
            sched_html = "<br>".join(parts)

        hover      = f"<b>{sd['name']}</b><br>{sched_html}"
        line_width = _color_meta[color][1]

        # GeoJSON coords: [[lon, lat], ...]
        coords = [[float(lon), float(lat)] for lon, lat in zip(sd["x"], sd["y"])]

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "render_type": "line",
                "urgency":     color,
                "line_color":  color,
                "line_width":  line_width,
                "hover_html":  hover,
            },
        })

    return {"type": "FeatureCollection", "features": features}


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
