import calendar
import datetime
import functools
import re
import weakref

from broombuster import normalize
from broombuster.domains.sweeping import compose_message


# Canonical street-name comparison key — delegates to normalize module.
def _norm_name(name: str) -> str:
    return normalize.street_name(name)


# Matches time ranges like "8AM–10AM", "7:30AM-9AM", "8AM to 10AM"
_TIME_RANGE_RE = re.compile(
    r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*(?:[-\u2013\u2014]|to)\s*'
    r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)',
    re.IGNORECASE,
)


def _parse_time_range(time_str: str):
    """Parse a time range to (start, end) datetime.time, or (None, None)."""
    if not isinstance(time_str, str) or not time_str.strip():
        return None, None
    m = _TIME_RANGE_RE.search(time_str)
    if not m:
        return None, None
    h1, m1, ap1, h2, m2, ap2 = m.groups()

    def _t(h, mn, ap):
        h, mn = int(h), int(mn or 0)
        ap = ap.upper()
        if ap == 'PM' and h != 12:
            h += 12
        elif ap == 'AM' and h == 12:
            h = 0
        return datetime.time(h, mn)

    try:
        return _t(h1, m1, ap1), _t(h2, m2, ap2)
    except Exception:
        return None, None


# Map sweeping letter codes to weekday integers
weekday_map = {
    'M': 0, 'T': 1, 'W': 2, 'TH': 3, 'F': 4, 'S': 5, 'SU': 6
}

# Handle combinations like 'MWF', 'TTHS', etc.
compound_map = {
    'MWF':  [0, 2, 4],
    'TTH':  [1, 3],
    'TTHS': [1, 3, 5],
    'MF':   [0, 4],
    # Oakland uses two-letter pairs for "Tue+Fri", "Thu+Fri", "Tue+Thu".
    # The trailing 'E' (every) is added by the endswith-'E' branch below.
    'TF':   [1, 4],
    'THF':  [3, 4],
    'E':    list(range(7)),  # Every day
}

# Handle codes like M13, T2, F24
ordinals = {
    '1': [1],
    '2': [2],
    '3': [3],
    '4': [4],
    '13': [1, 3],
    '24': [2, 4],
}

# Name-index cache keyed by id(gdf). Python recycles ids when objects are
# garbage-collected, so each entry pairs the value with a weakref to the
# original GDF; on lookup we verify the weakref still resolves to the same
# object — if not, the entry is stale and we rebuild.
#   id(gdf) → (weakref.ref(gdf), {normalized_street_name: [row_labels]})
_name_index_cache: dict[int, tuple] = {}


def analyze_car(gdf_3857, lat, lon, *, city_key=None, max_distance_m=50.0):
    """Resolve a car coordinate to its segment and return the legacy 4-tuple.

    Returns (schedule, schedule_even, schedule_odd, message) — the shape the
    CLI and notifier consume — via the same resolve_car_segment path as the
    /check endpoint, so the CLI and the API agree on the car's segment. The
    side is geometric (resolved.side), not house-number parity.
    """
    from broombuster import resolve  # local import: resolve has no broombuster deps

    try:
        resolved = resolve.resolve_car_segment(
            gdf_3857, lat, lon, city_key=city_key, max_distance_m=max_distance_m,
        )
    except resolve.NoSegmentNearby:
        resolved = None

    if resolved is None:
        return [], [], [], "Car not near a mapped street."

    schedule_even, schedule_odd = schedules_for_all_matching_rows(gdf_3857, resolved)
    schedule = list(set(schedule_even) | set(schedule_odd))
    car_side = resolved.side or "odd"
    message = compose_message(schedule_even, schedule_odd, car_side)
    return schedule, schedule_even, schedule_odd, message

def compute_urgency(segment, local_now=None):
    """Pure urgency function — no GDF, no spatial work, no Nominatim.

    Given a resolved segment (pandas Series from a normalised GDF) and a
    timezone-aware datetime, return "today" | "tomorrow" | False. Reads
    DAY_EVEN/ODD, TIME_EVEN/ODD, DESC_EVEN/ODD directly from the segment.
    The union of both sides is alerted on, matching the existing
    "alert either side" policy — the car is at risk regardless of which
    side is being swept.

    This is the authoritative urgency function. The /check endpoint, the
    notifier, and (in M3) the TypeScript port all call into this exact
    shape. Behaviour must match check_day_street_sweeping() one-for-one.
    """
    if segment is None:
        return False
    schedules = []
    e = get_schedule(segment, 0)
    o = get_schedule(segment, 1)
    if e:
        schedules.append(e)
    if o:
        schedules.append(o)
    return check_day_street_sweeping(schedules, local_now=local_now)


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


def _segment_endpoints(geom):
    """Return a frozenset of endpoint pairs for the geometry (~1m precision).

    Each LineString contributes one (start, end) pair. A MultiLineString
    contributes one pair per sub-line (Alameda's schema lumps every
    Channing Way block into a single multiline row). The set comparison is
    "any sub-line in common" so siblings that share at least one block of
    geometry will match.

    Coordinate rounding is CRS-aware: 0 decimals for meter-scale CRSs
    (|coord| > 180), 5 decimals for degree-scale — both ~1m tolerance,
    matching maps._seg_key.
    """
    if geom is None or geom.is_empty:
        return None
    sub_lines: list = []
    if geom.geom_type == "LineString":
        sub_lines = [list(geom.coords)]
    elif geom.geom_type == "MultiLineString":
        for sub in geom.geoms:
            if sub is None or sub.is_empty:
                continue
            sub_lines.append(list(sub.coords))
    else:
        return None
    out = set()
    decimals = None
    for coords in sub_lines:
        if len(coords) < 2:
            continue
        if decimals is None:
            decimals = 0 if abs(coords[0][0]) > 180 else 5
        a = (round(coords[0][0],  decimals), round(coords[0][1],  decimals))
        b = (round(coords[-1][0], decimals), round(coords[-1][1], decimals))
        # Each sub-line is order-independent: store as a frozen pair so
        # comparisons treat (a, b) and (b, a) as identical.
        out.add(frozenset({a, b}))
    return frozenset(out) if out else None


def check_day_street_sweeping(schedule, local_now=None):
    myDay      = local_now.date() if local_now else datetime.date.today()
    myTomorrow = myDay + datetime.timedelta(days=1)
    schedule_ymd: set = set()
    # date → list of time strings, INCLUDING empty strings. An empty entry
    # means "this side sweeps today but has no time info" — which we treat
    # as "still active" all day. Storing it makes the per-day window check
    # consistent with maps._sweeping_color, which evaluates each side
    # independently rather than filtering out empty times.
    date_times: dict  = {}

    for day in schedule:
        if not day:
            continue
        code     = day[0]
        time_str = day[2] if len(day) >= 3 else ""
        try:
            dates = parse_sweeping_code(code)
            for d in dates:
                schedule_ymd.add(d)
                date_times.setdefault(d, []).append(time_str)
        except Exception:
            pass

    def _day_active(d):
        """True if sweeping is scheduled on d and at least one side is still active."""
        if d not in schedule_ymd:
            return False
        if local_now is None:
            return True
        times = date_times.get(d, [])
        if not times:
            return True  # day scheduled but no time entries — assume active
        for ts in times:
            if not ts:
                return True  # an empty time on a swept side → that side is still active
            _, end_t = _parse_time_range(ts)
            if end_t is None or local_now.time() <= end_t:
                return True  # at least one window still open
        return False  # all sides with time info have closed; no untimed side either

    if _day_active(myDay):
        return "today"
    elif myTomorrow in schedule_ymd:
        return "tomorrow"
    else:
        return False


def _is_str(v):
    """True only for non-empty strings (filters NaN, None, floats)."""
    return isinstance(v, str) and v.strip() != ""


# Codes that explicitly mean "no sweeping" — these have parse_sweeping_code() == [],
# but they are pre-listed here so the formatter doesn't render their descriptor
# strings (e.g. "No Sweeping (HYW)", "No Signage", "No Sweeping (Uncontrol Condition)")
# as if they were real schedule entries on the car card.
# Compared case-insensitively after stripping. Includes Oakland's variants and
# the SF "no sweeping" placeholder.
_NO_SWEEP_CODES = frozenset({
    "N", "NS", "O", "N-S",
    "N-E", "N-O",   # Oakland: "No Even/Odd Addresses" — that side simply doesn't exist.
    "NS-UC", "NS-H", "NS-O", "NS-A",
})


def is_no_sweep_code(code) -> bool:
    """True if the given DAY_* code is one of the explicit no-sweep markers.

    These codes have no associated sweep dates; their DESC fields describe
    why (e.g. "No Sweeping (HYW)") and should not be rendered as schedules.
    """
    if not isinstance(code, str):
        return False
    return code.strip().upper() in _NO_SWEEP_CODES


def _safe_int(v):
    """Parse a value as int, returning None on failure (handles NaN)."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


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
    if side % 2 == 0:
        code = street_section.get("DAY_EVEN")
        if _is_str(code) and not is_no_sweep_code(code):
            return (
                code,
                street_section.get("DESC_EVEN") or "",
                street_section.get("TIME_EVEN") or "",
            )
    else:
        code = street_section.get("DAY_ODD")
        if _is_str(code) and not is_no_sweep_code(code):
            return (
                code,
                street_section.get("DESC_ODD") or "",
                street_section.get("TIME_ODD") or "",
            )


def get_all_dates_for_weekday(weekday, year, month):
    """Get all dates in a month for a specific weekday."""
    _, days_in_month = calendar.monthrange(year, month)
    return [
        datetime.date(year, month, day)
        for day in range(1, days_in_month + 1)
        if datetime.date(year, month, day).weekday() == weekday
    ]

def get_weekdays_by_ordinal(weekday, ordinals, year, month):
    """Get list of dates for a specific weekday and ordinal(s)."""
    dates = get_all_dates_for_weekday(weekday, year, month)
    return [dates[i - 1] for i in ordinals if i <= len(dates)]

@functools.lru_cache(maxsize=512)
def _parse_sweeping_code_cached(code: str, year: int, month: int) -> tuple:
    """
    Expand a sweep code into a tuple of dates for (year, month).
    Results are cached; since inputs include (year, month) the cache stays
    correct across month boundaries.
    """
    code = code.upper()

    # Handle compound sweep codes
    if code in compound_map:
        return tuple(
            d for wd in compound_map[code]
            for d in get_all_dates_for_weekday(wd, year, month)
        )

    # Handle every <day> (e.g., 'ME' = every Mon, 'TE' = every Tues).
    # Also handles compound "every X and Y" codes Oakland uses (e.g., 'TFE'
    # = every Tue+Fri, 'MFE' = every Mon+Fri) by falling back to compound_map
    # for the prefix.
    if code.endswith('E'):
        day_code = code[:-1]
        wd = weekday_map.get(day_code)
        if wd is not None:
            return tuple(get_all_dates_for_weekday(wd, year, month))
        wds = compound_map.get(day_code)
        if wds is not None:
            return tuple(
                d for w in wds
                for d in get_all_dates_for_weekday(w, year, month)
            )

    # Bare weekend codes ('S' = every Saturday, 'SU' = every Sunday).
    # Oakland uses these without the 'E' suffix that weekdays carry — treat
    # them as "every Sat" / "every Sun". Without this branch the parser
    # silently returns no dates and ~1000 Saturday/Sunday Oakland rows go
    # un-rendered in the urgency colour and card.
    if code in ('S', 'SU'):
        wd = weekday_map[code]
        return tuple(get_all_dates_for_weekday(wd, year, month))

    # Try matching ordinal part
    for suffix, ordinal_list in ordinals.items():
        if code.endswith(suffix):
            day_code = code[:len(code) - len(suffix)]
            wd = weekday_map.get(day_code)
            if wd is not None:
                return tuple(get_weekdays_by_ordinal(wd, ordinal_list, year, month))

    # 'E' = every day
    if code == 'E':
        _, days_in_month = calendar.monthrange(year, month)
        return tuple(datetime.date(year, month, d) for d in range(1, days_in_month + 1))

    return ()  # Unknown code


def parse_sweeping_code(code: str) -> list:
    """
    Expand a sweep code into a list of dates.
    Covers the current month, plus the next month on the last day of the
    current month so the tomorrow-check is never silently missed.
    """
    # Chicago-style explicit date list: "DATES:2026-04-01,2026-04-02,..."
    if code.upper().startswith("DATES:"):
        return [
            datetime.date.fromisoformat(ds.strip())
            for ds in code[6:].split(",")
            if ds.strip()
        ]

    today = datetime.date.today()
    dates = list(_parse_sweeping_code_cached(code, today.year, today.month))

    # When today is the last day of the month, tomorrow falls in the next
    # month — expand that month too so we never miss a next-day alert.
    tomorrow = today + datetime.timedelta(days=1)
    if tomorrow.month != today.month:
        dates.extend(_parse_sweeping_code_cached(code, tomorrow.year, tomorrow.month))

    return dates
