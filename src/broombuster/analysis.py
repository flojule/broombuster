import datetime
import functools
import logging
import re
import weakref
from typing import NamedTuple

from broombuster import normalize

logger = logging.getLogger(__name__)


# Canonical street-name comparison key — delegates to normalize module.
def _norm_name(name: str) -> str:
    return normalize.street_name(name)


# Matches time ranges like "8AM–10AM", "7:30AM-9AM", "8AM to 10AM"
_TIME_RANGE_RE = re.compile(
    r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:[-\u2013\u2014]|to)\s*'
    r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)',
    re.IGNORECASE,
)


def _clock(h: str, mn: str | None, ap: str) -> datetime.time | None:
    """12-hour clock parts -> datetime.time, or None when out of range."""
    hh, mm = int(h), int(mn or 0)
    if not 1 <= hh <= 12 or mm > 59:
        return None
    hh = hh % 12 + (12 if ap.upper() == "PM" else 0)
    return datetime.time(hh, mm)


def _end_of(t: datetime.time) -> datetime.time:
    """A window ending at 12AM runs to the end of the day."""
    return t if t != datetime.time(0) else datetime.time(23, 59, 59)


def parse_window(time_str) -> tuple[datetime.time, datetime.time] | None:
    """(start, end) of a time range, or None when unparseable."""
    m = _TIME_RANGE_RE.search(time_str) if isinstance(time_str, str) else None
    start, end = (_clock(*m.group(1, 2, 3)), _clock(*m.group(4, 5, 6))) if m else (None, None)
    return (start, _end_of(end)) if start and end else None


def _parse_end_time(time_str) -> datetime.time | None:
    """End of a time range as datetime.time, or None when unparseable."""
    m = _TIME_RANGE_RE.search(time_str) if isinstance(time_str, str) else None
    end = _clock(*m.group(4, 5, 6)) if m else None
    return _end_of(end) if end else None


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
WEEKDAY_CODES = {
    "M": (0, "Mon"), "T": (1, "Tue"), "W": (2, "Wed"), "TH": (3, "Thu"),
    "F": (4, "Fri"), "S": (5, "Sat"), "SU": (6, "Sun"),
}

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


def compute_urgency(segment, local_now=None):
    """Urgency ("today" | "tomorrow" | False) for one segment row, both sides."""
    even, odd = schedules_for_segment(segment)
    return check_day_street_sweeping(even + odd, local_now=local_now)


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

    target_key = resolved.segment.get("STREET_KEY") or _norm_name(
        resolved.segment.get("STREET_NAME") or ""
    )
    target_endpoints = _segment_endpoints(target_geom)
    if target_key == "" or not target_endpoints:
        return schedules_for_segment(resolved.segment)

    # Iterate the name index — only rows with the same STREET_KEY are candidates
    # (avoids touching every row in the GDF).
    name_idx = _get_name_index(gdf_3857)
    candidates = name_idx.get(target_key, [])

    seen_even: set = set()
    seen_odd:  set = set()
    even_out: list = []
    odd_out:  list = []

    for i in candidates:
        try:
            row = gdf_3857.loc[i]
        except KeyError:
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type not in ("LineString", "MultiLineString"):
            continue
        cand = _segment_endpoints(geom)
        # Match if any sub-line endpoint pair is shared. Alameda lumps a
        # whole street into one MultiLineString row, so blocks of the same
        # street are siblings (same STREET_KEY) but only some sub-line keys
        # overlap with the resolved row's geometry.
        if not cand or cand.isdisjoint(target_endpoints):
            continue

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
            item = (side, time if _is_str(time) else "")
            for d in dates_in_range(code, start, end):
                if item not in out.setdefault(d, []):
                    out[d].append(item)
    return dict(sorted(out.items()))


def check_day_street_sweeping(schedule, local_now=None):
    """"today" | "tomorrow" | False for a list of (code, desc, time) entries.

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
            end_t = _parse_end_time(ts)
            if end_t is None or now_t <= end_t:
                return "today"
    return "tomorrow" if swept_tomorrow else False


def _is_str(v):
    """True only for non-empty strings (filters NaN, None, floats)."""
    return isinstance(v, str) and v.strip() != ""


# Name-index cache keyed by id(gdf); each entry holds a weakref so a recycled
# id (GDF garbage-collected) is detected and rebuilt.
#   id(gdf) -> (weakref.ref(gdf), {normalized_street_name: [row_labels]})
_name_index_cache: dict[int, tuple] = {}


def _get_name_index(gdf) -> dict:
    """Build and cache a {normalized_name: [row_labels]} lookup for fast street matching."""
    gdf_id = id(gdf)
    cached = _name_index_cache.get(gdf_id)
    if cached is not None:
        ref, idx = cached
        if ref() is gdf:
            return idx
        # Stale entry — id was recycled by a different GDF. Drop it.
        del _name_index_cache[gdf_id]

    idx = {}
    for i, row in gdf.iterrows():
        # Prefer precomputed STREET_KEY if available (already canonical).
        k = row.get("STREET_KEY")
        if _is_str(k):
            idx.setdefault(k, []).append(i)
            continue
        # Fallback to normalising the stored STREET_NAME
        n = row.get("STREET_NAME")
        if _is_str(n):
            idx.setdefault(_norm_name(n), []).append(i)
    _name_index_cache[gdf_id] = (weakref.ref(gdf), idx)
    return idx


def get_schedule(street_section, side):
    """Return a (code, desc, time) tuple for the given side (0 = even, 1 = odd).

    Returns None when the code is missing or marks an explicit "no sweeping"
    state — those rows still drive the urgency colour (cornflowerblue) but
    have no schedule to render in the card or hover.
    """
    suffix = "EVEN" if side % 2 == 0 else "ODD"
    code = street_section.get(f"DAY_{suffix}")
    if not _is_str(code) or is_no_sweep_code(code):
        return None
    desc = street_section.get(f"DESC_{suffix}")
    time = street_section.get(f"TIME_{suffix}")
    return (code, desc if _is_str(desc) else "", time if _is_str(time) else "")


_MONTH_ABBR = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


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
        f"{_MONTH_ABBR[m]} " + ", ".join(str(day) for day in grouped[(y, m)])
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
    return (e if _is_str(e) else DEFAULT_SIDE_LABELS[0],
            o if _is_str(o) else DEFAULT_SIDE_LABELS[1])


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
