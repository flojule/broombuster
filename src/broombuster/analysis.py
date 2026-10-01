import calendar
import threading
import weakref
from typing import NamedTuple

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
WEEKDAY_CODES = {tok: (i, normalize.WEEKDAYS[i])
                 for i, tok in enumerate(("M", "T", "W", "TH", "F", "S", "SU"))}

# Codes that explicitly mean "no sweeping" (compared upper-cased, stripped).
# Oakland: N-E / N-O = no even / odd addresses; MS = "major street uses 2
# lines"; DM = "shown in a different map"; MISSING = no data.
NO_SWEEP_CODES = frozenset({
    "N", "NS", "O", "N-S", "N-E", "N-O",
    "NS-UC", "NS-H", "NS-O", "NS-A",
    "MS", "DM", "MISSING",
})


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
    """Return (schedule_even, schedule_odd) UNIONED across every GDF row
    that describes the same physical segment as `resolved`.

    SF's data layer emits one row per (segment × weekday); the resolver
    picks one of them. Without this helper, every downstream view (card,
    hover, urgency) sees only that one row's schedule, even though the map
    color reflects the union of all rows. This produces the long-running
    "hover says Wed but card says Mon" inconsistency.

    Two rows are treated as the same physical segment when they share
    `STREET_KEY` and overlap geometrically — at least one sub-line endpoint
    pair (rounded to ~1m, CRS-aware) appears in both. The overlap test
    handles Alameda's schema where one MultiLineString row spans every
    block of a street.

    Returns deduped lists of (code, desc, time) tuples — order is
    deterministic (sorted by code) so downstream message-formatting
    produces stable output.
    """
    if resolved is None or resolved.segment is None:
        return [], []
    if getattr(resolved, "is_polygon", False):
        # Polygon zones (Chicago) are one row per zone — no merge needed.
        return schedules_for_segment(resolved.segment)

    target_geom = resolved.segment.geometry
    if target_geom is None or target_geom.is_empty:
        return schedules_for_segment(resolved.segment)
    if target_geom.geom_type not in ("LineString", "MultiLineString"):
        return schedules_for_segment(resolved.segment)

    target_key = resolved.segment.get("STREET_KEY") or normalize.street_name(
        resolved.segment.get("STREET_NAME") or ""
    )
    target_endpoints = _segment_endpoints(target_geom)
    if target_key == "" or not target_endpoints:
        return schedules_for_segment(resolved.segment)

    # Only rows sharing the street key AND a sub-line endpoint can match; the
    # prebuilt index maps exactly those pairs to row positions.
    index = segment_index(gdf_3857)
    positions = sorted({p for ep in target_endpoints
                        for p in index.by_key.get((target_key, ep), ())})

    seen_even: set = set()
    seen_odd:  set = set()
    even_out: list = []
    odd_out:  list = []

    for p in positions:
        row = {col: arr[p] for col, arr in index.cols.items()}
        e = get_schedule(row, 0)
        o = get_schedule(row, 1)
        if e and e not in seen_even:
            seen_even.add(e)
            even_out.append(e)
        if o and o not in seen_odd:
            seen_odd.add(o)
            odd_out.append(o)

    # Stable order: sort by sweep code so message formatting is deterministic.
    even_out.sort(key=lambda t: (t[0] or "", t[1] or "", t[2] or ""))
    odd_out.sort (key=lambda t: (t[0] or "", t[1] or "", t[2] or ""))
    return even_out, odd_out


def line_key(coords) -> frozenset:
    """Orientation-free endpoint key of a line (~1 m: 0 decimals in metres, 5 in degrees)."""
    decimals = 0 if abs(coords[0][0]) > 180 else 5
    a = (round(coords[0][0], decimals), round(coords[0][1], decimals))
    b = (round(coords[-1][0], decimals), round(coords[-1][1], decimals))
    return frozenset({a, b})


def _segment_endpoints(geom):
    """Frozenset of line_key per sub-line (MultiLineString rows span many blocks)."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        parts = [geom]
    elif geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
    else:
        return None
    out = {line_key(list(p.coords)) for p in parts if not p.is_empty and len(p.coords) >= 2}
    return frozenset(out) if out else None



class SegmentIndex(NamedTuple):
    """Per-GDF lookup behind schedules_for_all_matching_rows."""
    by_key: dict  # (street_key, line_key) -> [row positions], ascending
    cols: dict    # schedule column -> object ndarray, indexed by row position


_SCHEDULE_COLS = ("DAY_EVEN", "DESC_EVEN", "TIME_EVEN", "DAY_ODD", "DESC_ODD", "TIME_ODD")

# id(gdf) -> (weakref.ref(gdf), SegmentIndex); the weakref callback drops the
# entry when the GDF is collected, so replaced region frames don't leak.
_segment_index_cache: dict[int, tuple] = {}
_segment_index_lock = threading.Lock()


def segment_index(gdf) -> SegmentIndex:
    """Cached SegmentIndex for `gdf` (built once; ~1 s for the Bay Area on a Pi).

    Keys: STREET_KEY when it is text, else the normalised STREET_NAME; one
    entry per non-empty LineString part with at least two coordinates.
    """
    gdf_id = id(gdf)
    cached = _segment_index_cache.get(gdf_id)  # lock-free hit: another region's
    if cached is not None and cached[0]() is gdf:  # build must not stall it
        return cached[1]
    with _segment_index_lock:
        cached = _segment_index_cache.get(gdf_id)
        if cached is not None and cached[0]() is gdf:
            return cached[1]
        index = _build_segment_index(gdf)
        _segment_index_cache[gdf_id] = (
            weakref.ref(gdf, lambda _r, k=gdf_id: _segment_index_cache.pop(k, None)),
            index,
        )
        return index


def _build_segment_index(gdf) -> SegmentIndex:
    n = len(gdf)
    keys = gdf["STREET_KEY"].to_numpy(object) if "STREET_KEY" in gdf else [None] * n
    names = gdf["STREET_NAME"].to_numpy(object) if "STREET_NAME" in gdf else [None] * n
    # street_name() returns "" for non-text, which the loop below skips.
    street_keys = [k if normalize.is_text(k) else normalize.street_name(nm)
                   for k, nm in zip(keys, names)]

    geoms = np.asarray(gdf.geometry.values, dtype=object)
    type_ids = shapely.get_type_id(geoms)
    is_line = (type_ids == 1) | (type_ids == 5)  # LineString | MultiLineString
    rows = np.flatnonzero(is_line)
    parts, part_row = shapely.get_parts(geoms[rows], return_index=True)
    part_row = rows[part_row]
    ok = ~shapely.is_empty(parts) & (shapely.get_num_coordinates(parts) >= 2)
    parts, part_row = parts[ok], part_row[ok]
    first, last = shapely.get_point(parts, 0), shapely.get_point(parts, -1)
    xy = np.column_stack([shapely.get_x(first), shapely.get_y(first),
                          shapely.get_x(last), shapely.get_y(last)]).tolist()

    by_key: dict = {}
    for pos, (x0, y0, x1, y1) in zip(part_row.tolist(), xy):
        sk = street_keys[pos]
        if not sk:
            continue
        lst = by_key.setdefault((sk, line_key(((x0, y0), (x1, y1)))), [])
        if not lst or lst[-1] != pos:  # a row's parts can share one key
            lst.append(pos)

    cols = {c: gdf[c].to_numpy(object) for c in _SCHEDULE_COLS if c in gdf}
    return SegmentIndex(by_key, cols)


def side_entry(code, desc, time):
    """(code, desc, time) for one side, or None for a missing / no-sweep code.

    Placeholder desc / time ("N/A", NaN, …) become "". No-sweep rows still drive
    the map colour, but have no schedule to render.
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
    return (e if normalize.is_text(e) else DEFAULT_SIDE_LABELS[0],
            o if normalize.is_text(o) else DEFAULT_SIDE_LABELS[1])

