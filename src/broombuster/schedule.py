"""Sweep dates on the server (the calendar feed): the same rules as
frontend/js/urgency.js (sweepsOn, timeWindow), kept in step by
tests/test_schedule.py, plus city holidays and weekly trash days."""

import datetime
import re

from broombuster import analysis, holidays
from broombuster.cities import CITIES

_CODE_RE = re.compile(r"((?:TH|SU|M|T|W|F|S)*)(E?|[1-5]+)")
_DAY_TOKEN_RE = re.compile(r"TH|SU|M|T|W|F|S")
# Same lenient pattern as urgency.js TIME_RANGE_RE (PDF artifacts in the raw data).
_TIME_RANGE_RE = re.compile(
    r"(\d\s*\d?)\s*(?::\s*(\d\s*\d))?\s*(A[\s,]*M|P[\s,]*M)"
    r"[\s,]*(?:[-–—•·o]|to)\s*"
    r"(\d\s*\d?)\s*(?::\s*(\d\s*\d))?\s*(A[\s,]*M|P[\s,]*M)",
    re.I,
)
_HALF_DAY = {"AM": (0, 720), "PM": (720, 1440)}


def _rule(code):
    """("dates", {date}) | ("weekly", {weekday}, {ordinal} | None) | None."""
    if not isinstance(code, str):
        return None
    c = code.strip().upper()
    if c.startswith("DATES:"):
        days = set()
        for part in c[6:].split(","):
            try:
                days.add(datetime.date.fromisoformat(part.strip()))
            except ValueError:
                pass
        return ("dates", days)
    m = _CODE_RE.fullmatch(c)
    if analysis.is_no_sweep_code(c) or not m or not (m[1] or m[2] == "E"):
        return None
    days = {analysis.WEEKDAY_CODES[t][0] for t in _DAY_TOKEN_RE.findall(m[1])} or set(range(7))
    ords = {int(ch) for ch in m[2]} if m[2] and m[2] != "E" else None
    return ("weekly", days, ords)


def sweeps_on(code, day: datetime.date) -> bool:
    """urgency.js sweepsOn: does `code` sweep on `day` (holidays aside)?"""
    r = _rule(code)
    if r is None:
        return False
    if r[0] == "dates":
        return day in r[1]
    _, days, ords = r
    return day.weekday() in days and (ords is None or (day.day - 1) // 7 + 1 in ords)


def _clock(h, mn, ap):
    hh, mm = int(re.sub(r"\s+", "", h)), int(re.sub(r"\s+", "", mn)) if mn else 0
    if not 1 <= hh <= 12 or mm > 59:
        return None
    return (hh % 12 + (12 if re.sub(r"[\s,]+", "", ap).upper() == "PM" else 0)) * 60 + mm


def time_window(raw) -> tuple[int, int] | None:
    """urgency.js timeWindow: (start, end) minutes after midnight, end 1440 for a
    window ending at 12AM, end < start past midnight; None when unreadable."""
    if not isinstance(raw, str):
        return None
    m = _TIME_RANGE_RE.search(raw)
    if m:
        a, b = _clock(m[1], m[2], m[3]), _clock(m[4], m[5], m[6])
        if a is not None and b is not None:
            return a, b or 1440
    return _HALF_DAY.get(raw.strip().upper())


def sweep_days(entries, city_key: str | None, start: datetime.date, end: datetime.date):
    """[(date, [(side, time)])] in [start, end] for /check entries
    {"even": [[code, desc, time]...], "odd": [...]}, minus the city's no-sweep holidays."""
    out = []
    day = start
    while day <= end:
        if not (city_key and holidays.suspended(city_key, "sweeping", day)):
            items = []
            for side in ("even", "odd"):
                for code, _desc, time in entries.get(side, []):
                    if sweeps_on(code, day) and (side, time or "") not in items:
                        items.append((side, time or ""))
            if items:
                out.append((day, items))
        day += datetime.timedelta(days=1)
    return out


def weekly_pickups(
    city_key: str, weekday: int, start: datetime.date, days: int
) -> list[datetime.date]:
    """A weekly collection day's dates in [start, start + days), moved by the city's
    trash holiday rule (manifest `trash.holiday_rule`):

    shift_week  on a trash holiday, that day's and the rest of the week's pickups
                (to Sunday) are one day later (Berkeley, ACI);
    next_day    a pickup on a trash holiday is the next day (Chicago);
    none        holidays don't move pickups.
    """
    rule = (CITIES[city_key].get("trash") or {}).get("holiday_rule", "none")
    out = []
    first = start - datetime.timedelta(days=7)  # last week's shift can land in range
    day = first + datetime.timedelta(days=(weekday - first.weekday()) % 7)
    while day < start + datetime.timedelta(days=days):
        moved = day
        if rule == "shift_week":
            monday = day - datetime.timedelta(days=day.weekday())
            hit = [monday + datetime.timedelta(days=i) for i in range(day.weekday() + 1)]
            if any(holidays.suspended(city_key, "trash", h) for h in hit):
                moved = day + datetime.timedelta(days=1)
        elif rule == "next_day" and holidays.suspended(city_key, "trash", day):
            moved = day + datetime.timedelta(days=1)
        if start <= moved < start + datetime.timedelta(days=days):
            out.append(moved)
        day += datetime.timedelta(days=7)
    return out
