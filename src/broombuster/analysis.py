import calendar
import datetime
import functools
import logging
import re
import threading
import weakref
from typing import NamedTuple

import numpy as np
import shapely

from broombuster import normalize

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sweep-code grammar (mirrored in frontend/js/urgency.js; tables checked by
# tests/test_urgency_parity.py)
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

_CODE_RE = re.compile(r"((?:TH|SU|M|T|W|F|S)*)(E?|[1-5]+)")
_DAY_TOKEN_RE = re.compile(r"TH|SU|M|T|W|F|S")


def is_no_sweep_code(code) -> bool:
    """True if the given DAY_* code is one of the explicit no-sweep markers."""
    return isinstance(code, str) and code.strip().upper() in NO_SWEEP_CODES


@functools.lru_cache(maxsize=4096)
def _code_parts(code: str) -> tuple[tuple[str, ...], str] | None:
    """(day tokens, suffix) of a weekly code, or None when not weekly/no-sweep."""
    c = code.strip().upper()
    if c in NO_SWEEP_CODES:
        return None
    m = _CODE_RE.fullmatch(c)
    if not m:
        return None
    days, suffix = m.groups()
    if not days and suffix != "E":
        return None
    return tuple(_DAY_TOKEN_RE.findall(days)), suffix


class _Rule(NamedTuple):
    weekdays: frozenset  # Mon=0..Sun=6; empty for DATES codes
    ordinals: frozenset  # week-of-month 1..5; empty = every week
    dates: frozenset     # explicit dates ('DATES:' codes only)


@functools.lru_cache(maxsize=4096)
def _rule(code: str) -> _Rule | None:
    """Parsed sweep rule for a code, or None for no-sweep / unknown codes."""
    dates = parse_dates_code(code)
    if dates is not None:
        return _Rule(frozenset(), frozenset(), frozenset(dates))
    parts = _code_parts(code)
    if parts is None:
        return None
    tokens, suffix = parts
    weekdays = (frozenset(WEEKDAY_CODES[t][0] for t in tokens) if tokens
                else frozenset(range(7)))
    ordinals = frozenset(int(ch) for ch in suffix) if suffix.isdigit() else frozenset()
    return _Rule(weekdays, ordinals, frozenset())


def sweeps_on(code, day: datetime.date) -> bool:
    """True if `code` schedules sweeping on `day`."""
    if not isinstance(code, str):
        return False
    r = _rule(code)
    if r is None:
        return False
    if r.dates:
        return day in r.dates
    if day.weekday() not in r.weekdays:
        return False
    return not r.ordinals or (day.day - 1) // 7 + 1 in r.ordinals


def dates_in_range(code, start: datetime.date, end: datetime.date) -> list:
    """Sorted sweep dates of `code` in [start, end] (inclusive)."""
    out = []
    d = start
    while d <= end:
        if sweeps_on(code, d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


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


def sweep_days(even, odd, start: datetime.date, end: datetime.date) -> dict:
    """{date: [(side, time), ...]} for both sides' sweeps in [start, end], date-sorted.

    even / odd are (code, desc, time) entries; side is "even" or "odd".
    """
    out: dict = {}
    for side, entries in (("even", even), ("odd", odd)):
        for code, _desc, time in entries:
            item = (side, normalize.clean_text(time))
            for d in dates_in_range(code, start, end):
                if item not in out.setdefault(d, []):
                    out[d].append(item)
    return dict(sorted(out.items()))


def check_day_street_sweeping(schedule, local_now=None):
    """"today" | "tomorrow" | "safe" for a list of (code, desc, time) entries.

    "today" only while at least one of today's windows is still open (an
    untimed or unparseable window counts as open all day). Dates are taken in
    the region-local clock `local_now`; without it, the server date and no
    window check.
    """
    today = local_now.date() if local_now else datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    today_times: list = []
    swept_tomorrow = False
    for entry in schedule:
        if not entry:
            continue
        code = entry[0]
        if sweeps_on(code, today):
            today_times.append(entry[2] if len(entry) >= 3 else "")
        if sweeps_on(code, tomorrow):
            swept_tomorrow = True

    if today_times:
        if local_now is None:
            return "today"
        now_t = local_now.time()
        for ts in today_times:
            window = normalize.time_window(ts)
            if window is None or now_t <= window[1]:
                return "today"
    return "tomorrow" if swept_tomorrow else "safe"


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

    Keys mirror the per-row rules schedules_for_all_matching_rows used to
    apply: STREET_KEY when it is text, else the normalised STREET_NAME; one
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


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_dates_code(code) -> list | None:
    """Sorted list of dates for a 'DATES:' code, or None for any other code."""
    if not isinstance(code, str) or not code.upper().startswith("DATES:"):
        return None
    out = []
    for ds in code[6:].split(","):
        ds = ds.strip()
        if not ds:
            continue
        try:
            # YYYY-MM-DD only (fromisoformat also takes "20260929"; JS does not).
            if not _ISO_DATE_RE.fullmatch(ds):
                raise ValueError(ds)
            out.append(datetime.date.fromisoformat(ds))
        except ValueError:
            logger.warning("skipping invalid date %r in sweep code", ds)
    return sorted(out)


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


# A section's two sides are swept a few days apart (e.g. Jun 13 & 16); dates
# within this gap belong to the same sweeping occurrence. Bi-weekly sections
# sweep ~14 days apart, so this stays well below that and never over-merges.
_CLUSTER_GAP_DAYS = 4


def cluster_dates(dates, max_gap_days: int = _CLUSTER_GAP_DAYS) -> list:
    """Group sorted dates into clusters; a gap > max_gap_days starts a new one."""
    clusters: list = []
    cur: list = []
    for d in dates:
        if cur and (d - cur[-1]).days > max_gap_days:
            clusters.append(cur)
            cur = []
        cur.append(d)
    if cur:
        clusters.append(cur)
    return clusters


def next_cluster_dates(code, local_now=None, max_dates: int = 3) -> list | None:
    """Dates of the next upcoming sweep cluster for a 'DATES:' code.

    None for non-DATES codes; [] when no future dates remain. Capped at
    `max_dates`.
    """
    dates = parse_dates_code(code)
    if dates is None:
        return None
    today = local_now.date() if local_now else datetime.date.today()
    future = [d for d in dates if d >= today]
    if not future:
        return []
    return cluster_dates(future)[0][:max_dates]


def _code_weekday(code: str):
    """(rank, display) of a weekly code's first weekday, or None."""
    parts = _code_parts(code)
    return WEEKDAY_CODES[parts[0][0]] if parts and parts[0] else None


def _code_ordinals(code: str) -> set:
    """Week-of-month ordinals of a weekly code ('M13' -> {1, 3})."""
    parts = _code_parts(code)
    return {int(ch) for ch in parts[1]} if parts and parts[1].isdigit() else set()


def _contiguous_runs(ranks):
    """Split sorted weekday ranks into runs of consecutive days ([0,1,2,4] ->
    [[0,1,2],[4]])."""
    runs, cur = [], []
    for r in ranks:
        if cur and r == cur[-1] + 1:
            cur.append(r)
        else:
            if cur:
                runs.append(cur)
            cur = [r]
    if cur:
        runs.append(cur)
    return runs


def _weekday_range_body(label, time) -> str:
    """'<label>, <time>' line for a collapsed weekday range / 'Every day'."""
    t = normalize.time_display(time or "")
    return f"{label}, {t}" if t and t != "N/A" else label


def format_schedule_side(entries, local_now=None) -> list:
    """Canonical display lines for one side's (code, desc, time) entries.

    Unifies every surface (card, hover, zone): weekday-codes are grouped by
    (weekday, time); a 1st&3rd + 2nd&4th pair on the same time collapses to
    "Every <Wd>"; lines are ordered Mon->Sun. 'DATES:' codes (Berkeley/Chicago)
    contribute their next sweep dates, merged across codes into one
    chronological line. No-sweep codes are dropped.
    """
    clean = [
        e for e in (entries or [])
        if e and len(e) >= 1 and isinstance(e[0], str) and not is_no_sweep_code(e[0])
    ]

    dates_entries = [e for e in clean if parse_dates_code(e[0]) is not None]
    weekly = [e for e in clean if parse_dates_code(e[0]) is None]

    # Group weekday entries by (rank, normalized time); union their ordinals.
    groups: dict = {}
    loose: list = []
    for e in weekly:
        code = e[0]
        desc = e[1] if len(e) >= 2 else ""
        time = e[2] if len(e) >= 3 else ""
        wk = _code_weekday(code)
        if wk is None:
            loose.append((desc, time))
            continue
        rank, disp = wk
        key = (rank, normalize.time_display(time or ""))
        g = groups.setdefault(key, {"rank": rank, "disp": disp,
                                    "ords": set(), "time": time, "items": []})
        g["ords"] |= _code_ordinals(code)
        g["items"].append((desc, time))

    # A group recurs "every week" when it carries no ordinal qualifier (plain
    # weekday code) or covers all four ordinals (1st&3rd + 2nd&4th). Every-week
    # groups that share one time across a contiguous run of 3+ weekdays collapse
    # into a single "Mon–Fri, <time>" line (all seven days -> "Every day,
    # <time>"); shorter runs and partial-ordinal groups stay one line per day.
    ranked: list = []  # (rank, ord_key, body)
    everyweek: dict = {}  # time_display -> {"time": raw, "days": {rank: (disp, fallback)}}
    for (rank, td), g in groups.items():
        if (not g["ords"]) or ({1, 2, 3, 4} <= g["ords"]):
            if {1, 2, 3, 4} <= g["ords"]:
                fallback = normalize.sweep_body(f"Every {g['disp']}", g["time"])
            else:
                fallback = normalize.sweep_body(g["items"][0][0], g["items"][0][1])
            slot = everyweek.setdefault(td, {"time": g["time"], "days": {}})
            slot["days"][rank] = (g["disp"], fallback)
        else:
            for desc, time in g["items"]:
                ranked.append((rank, 0, normalize.sweep_body(desc, time)))

    for _td, slot in everyweek.items():
        days = slot["days"]
        ord_ranks = sorted(days)
        if len(ord_ranks) == 7:
            ranked.append((0, -1, _weekday_range_body("Every day", slot["time"])))
            continue
        for run in _contiguous_runs(ord_ranks):
            if len(run) >= 3:
                label = f"{days[run[0]][0]}–{days[run[-1]][0]}"
                ranked.append((run[0], -1, _weekday_range_body(label, slot["time"])))
            else:
                for r in run:
                    ranked.append((r, -1, days[r][1]))
    ranked.sort(key=lambda x: (x[0], x[1]))

    lines: list = []
    seen: set = set()
    for _r, _o, body in ranked:
        if body and body not in seen:
            seen.add(body)
            lines.append(body)
    for desc, time in loose:
        body = normalize.sweep_body(desc, time)
        if body and body not in seen:
            seen.add(body)
            lines.append(body)

    # DATES codes: merge each code's next cluster into one chronological line.
    if dates_entries:
        merged: list = []
        for code, *_ in dates_entries:
            merged += next_cluster_dates(code, local_now) or []
        merged = sorted(set(merged))
        if merged:
            line = format_dates_by_month(merged)
            if line not in seen:
                lines.append(line)

    return lines



DEFAULT_SIDE_LABELS = ("Even", "Odd")


def side_labels(row) -> tuple[str, str]:
    """(even, odd) display labels: the row's SIDE_EVEN / SIDE_ODD, else Even / Odd."""
    if row is None:
        return DEFAULT_SIDE_LABELS
    e, o = row.get("SIDE_EVEN"), row.get("SIDE_ODD")
    return (e if normalize.is_text(e) else DEFAULT_SIDE_LABELS[0],
            o if normalize.is_text(o) else DEFAULT_SIDE_LABELS[1])


def side_groups(even, odd, car_side=None, labels=DEFAULT_SIDE_LABELS, local_now=None):
    """[(side, label, lines)] per side, car's side first; [(None, None, lines)]
    when both sides read identically."""
    ev = format_schedule_side(even, local_now)
    od = format_schedule_side(odd, local_now)
    if ev and ev == od:
        return [(None, None, ev)]
    groups = [("even", labels[0], ev), ("odd", labels[1], od)]
    if car_side == "odd":
        groups.reverse()
    return groups


def side_lines(even, odd, car_side=None, labels=DEFAULT_SIDE_LABELS, local_now=None) -> list:
    """Display lines for both sides: unlabelled when identical, else '<label>: <line>'."""
    out: list = []
    for _side, label, lines in side_groups(even, odd, car_side, labels, local_now):
        out += lines if label is None else [f"{label}: {ln}" for ln in lines]
    return out
