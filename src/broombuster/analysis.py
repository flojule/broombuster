import calendar

import numpy as np
import shapely

from broombuster import normalize

# ---------------------------------------------------------------------------
# Sweep-code grammar (evaluated by frontend/js/urgency.js)
#
#   code     := DAYS [ "E" | ORDINALS ] | "E" | "DATES:" iso-date{,iso-date}
#   DAYS     := one or more of TH SU M T W F S   (longest token first)
#   ORDINALS := week-of-month digits 1..5        (e.g. "135" = 1st, 3rd, 5th)
#
# DAYS with no suffix or "E" sweep every week; "E" alone is every day.
# ---------------------------------------------------------------------------

# Code token -> (weekday Mon=0..Sun=6, display).
WEEKDAY_CODES = {
    tok: (i, normalize.WEEKDAYS[i]) for i, tok in enumerate(("M", "T", "W", "TH", "F", "S", "SU"))
}

# Codes that explicitly mean "no sweeping" (compared upper-cased, stripped).
# Oakland: N-E / N-O = no even / odd addresses; MS = "major street uses 2
# lines"; DM = "shown in a different map"; MISSING = no data.
NO_SWEEP_CODES = frozenset(
    {
        "N",
        "NS",
        "O",
        "N-S",
        "N-E",
        "N-O",
        "NS-UC",
        "NS-H",
        "NS-O",
        "NS-A",
        "MS",
        "DM",
        "MISSING",
    }
)


# Codes for sides that are swept on a day no source gives (Oakland's S / SU): no dates are
# computed; the description and hours are shown so the driver reads the posted sign.
UNDATED_CODES = frozenset({"POSTED"})


def is_no_sweep_code(code) -> bool:
    """True if the given DAY_* code is one of the explicit no-sweep markers."""
    return isinstance(code, str) and code.strip().upper() in NO_SWEEP_CODES


def schedules_for_segment(segment):
    """Return (schedule_even, schedule_odd) for a single segment.

    Each is a list of zero or one (code, desc, time) tuples — the format
    the frontend already consumes via schedule_even / schedule_odd.
    """
    if segment is None:
        return [], []
    e = get_schedule(segment, 0)
    o = get_schedule(segment, 1)
    return ([e] if e else []), ([o] if o else [])


def schedules_for_all_matching_rows(gdf_3857, resolved):
    """(schedule_even, schedule_odd) unioned across every row that draws the same street as
    the resolved one (`coincide`), whatever its name or city.

    SF emits one row per (segment x weekday), Oakland draws a state highway over the street
    it runs on, and border streets are drawn by both cities: the card and the map color
    (tiles.merge_segment_rows) show the union. A MultiLineString row (Alameda: one per street)
    matches the rows of any of its blocks.

    Returns deduped lists of (code, desc, time) tuples, sorted by code.
    """
    if resolved is None or resolved.segment is None:
        return [], []
    seg = resolved.segment
    tparts, _ = line_parts([seg.geometry])
    if getattr(resolved, "is_polygon", False) or not len(tparts):
        return schedules_for_segment(seg)

    sub = gdf_3857.iloc[gdf_3857.sindex.query(_boxes(tparts))[1]]
    cparts, crow = line_parts(sub.geometry.values)
    n = len(tparts)
    i, j = near_pairs(tparts, cparts)
    ok = coincide(
        np.concatenate([tparts, cparts]),
        np.concatenate([np.full(n, street_key(seg), object), street_keys(sub)[crow]]),
        np.concatenate([np.full(n, seg.get("_city"), object), _cities(sub)[crow]]),
        i,
        j + n,
    )
    rows = sub.iloc[np.unique(crow[j[ok]])]
    cols = [c for c in _SCHEDULE_COLS if c in rows]

    even_out: list = []
    odd_out: list = []
    for row in rows[cols].to_dict("records"):
        for side, out in ((0, even_out), (1, odd_out)):
            e = get_schedule(row, side)
            if e and e not in out:
                out.append(e)
    even_out.sort(key=lambda t: (t[0] or "", t[1] or "", t[2] or ""))
    odd_out.sort(key=lambda t: (t[0] or "", t[1] or "", t[2] or ""))
    return even_out, odd_out


_SCHEDULE_COLS = ("DAY_EVEN", "DESC_EVEN", "TIME_EVEN", "DAY_ODD", "DESC_ODD", "TIME_ODD")

EARTH_RADIUS_M = 6378137.0  # EPSG:3857 sphere
COINCIDE_M = 3.0  # two rows of one city within this (Hausdorff) draw one street
BORDER_M = 10.0  # two cities' drawings of one border street (Oakland | Berkeley) within this


def street_key(row) -> str:
    """A row's street comparison key: STREET_KEY when text, else the normalized STREET_NAME."""
    sk = row.get("STREET_KEY")
    return sk if normalize.is_text(sk) else normalize.street_name(row.get("STREET_NAME"))


def street_keys(gdf) -> np.ndarray:
    """street_key of every row (object array)."""
    n = len(gdf)
    keys = gdf["STREET_KEY"].to_numpy(object) if "STREET_KEY" in gdf else [None] * n
    names = gdf["STREET_NAME"].to_numpy(object) if "STREET_NAME" in gdf else [None] * n
    out = [
        k if normalize.is_text(k) else normalize.street_name(nm)
        for k, nm in zip(keys, names, strict=True)
    ]
    return np.array(out, dtype=object)


def _cities(gdf) -> np.ndarray:
    return gdf["_city"].to_numpy(object) if "_city" in gdf else np.full(len(gdf), None, object)


def line_parts(geoms):
    """(parts, row) of every LineString part with 2+ coordinates; other geometries skipped."""
    geoms = np.asarray(geoms, dtype=object)
    tid = shapely.get_type_id(geoms)
    rows = np.flatnonzero((tid == 1) | (tid == 5))  # LineString | MultiLineString
    parts, idx = shapely.get_parts(geoms[rows], return_index=True)
    ok = shapely.get_num_coordinates(parts) >= 2
    return parts[ok], rows[idx[ok]]


def _scale(parts) -> np.ndarray:
    """EPSG:3857 units per ground meter at each part's start."""
    return np.cosh(shapely.get_y(shapely.get_point(parts, 0)) / EARTH_RADIUS_M)


def _boxes(parts):
    """Each part's bounding box grown by BORDER_M."""
    b, r = shapely.bounds(parts), BORDER_M * _scale(parts)
    return shapely.box(b[:, 0] - r, b[:, 1] - r, b[:, 2] + r, b[:, 3] + r)


def near_pairs(parts, others):
    """(i, j) of parts[i] and others[j] whose boxes are within BORDER_M: coincide candidates."""
    return shapely.STRtree(others).query(_boxes(parts))


def _ends(parts):
    """(start, end) of each part, rounded to 1 EPSG:3857 unit, as (n, 2) arrays."""
    return tuple(
        np.round(shapely.get_coordinates(shapely.get_point(parts, k))).reshape(-1, 2)
        for k in (0, -1)
    )


def coincide(parts, keys, cities, i, j) -> np.ndarray:
    """Which pairs (parts[i], parts[j]) of line parts (EPSG:3857), with their rows' street keys
    and cities, draw one street: within COINCIDE_M of each other (Hausdorff; BORDER_M for two
    cities) and half the shorter part, or one street key with the same ends (~1 m)."""
    b, length, scale = shapely.bounds(parts), shapely.length(parts), _scale(parts)
    tol = np.where(cities[i] == cities[j], COINCIDE_M, BORDER_M) * scale[i]
    tol = np.minimum(tol, np.minimum(length[i], length[j]) / 2)
    # Hausdorff <= tol needs every bound within tol: a cheap filter first.
    near = (np.abs(b[i] - b[j]) <= tol[:, None]).all(axis=1)
    near[near] = shapely.hausdorff_distance(parts[i[near]], parts[j[near]]) <= tol[near]
    s, e = _ends(parts)
    same = ((s[i] == s[j]) & (e[i] == e[j])).all(1) | ((s[i] == e[j]) & (e[i] == s[j])).all(1)
    return near | (same & (keys[i] == keys[j]) & (keys[i] != ""))


def side_entry(code, desc, time):
    """(code, desc, time) for one side, or None for a missing / no-sweep code.

    Placeholder desc / time ("N/A", NaN, …) become "". No-sweep rows still drive
    the map color, but have no schedule to render.
    """
    if not normalize.is_text(code) or is_no_sweep_code(code):
        return None
    return code, normalize.clean_text(desc), normalize.clean_text(time)


def get_schedule(row, side):
    """side_entry for a GDF row's even (side 0) or odd (side 1) columns."""
    s = "EVEN" if side % 2 == 0 else "ODD"
    return side_entry(row.get(f"DAY_{s}"), row.get(f"DESC_{s}"), row.get(f"TIME_{s}"))


def format_dates_by_month(dates) -> str:
    """Group sorted dates into "Apr 17, 18; May 15" (preserving date order)."""
    grouped: dict = {}
    order: list = []
    for d in dates:
        key = (d.year, d.month)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(d.day)
    return "; ".join(
        f"{calendar.month_abbr[m]} " + ", ".join(str(day) for day in grouped[(y, m)])
        for (y, m) in order
    )


DEFAULT_SIDE_LABELS = ("Even", "Odd")


def side_labels(row) -> tuple[str, str]:
    """(even, odd) display labels: the row's SIDE_EVEN / SIDE_ODD, else Even / Odd."""
    if row is None:
        return DEFAULT_SIDE_LABELS
    e, o = row.get("SIDE_EVEN"), row.get("SIDE_ODD")
    return (
        e if normalize.is_text(e) else DEFAULT_SIDE_LABELS[0],
        o if normalize.is_text(o) else DEFAULT_SIDE_LABELS[1],
    )
