"""The calendar subscription feed (iCalendar, RFC 5545): an account's cars' street
sweeping, its homes' trash pickups and the no-sweep holidays of those cities, for
the next DAYS days. Each sweep has two reminders: the evening before (ALARMS) and an
hour before, so a car parked late still gets one."""

import datetime
from collections.abc import Callable
from typing import NamedTuple
from zoneinfo import ZoneInfo

from broombuster import holidays, normalize, schedule, trash
from broombuster.cities import REGIONS, city_for_point, region_for_point

DAYS = 365
TRASH_LOOKUPS = 3  # uncached ReCollect lookups per feed; other homes use cached answers
PRODID = "-//BroomBuster//Sweeping calendar//EN"


class CarSpot(NamedTuple):
    """What /check knows about where a car is."""

    region: str
    city: str
    address: str
    entries: dict  # {"even": [[code, desc, time]], "odd": [...]}
    labels: tuple[str, str]  # the sides' names (even, odd): "North", "Even", ...


# Reminders before a timed sweep (the evening before, an hour before) and an all-day one
# (7PM the evening before, 6AM that day).
TIMED_ALARMS = ("-PT12H", "-PT1H")
DAY_ALARMS = ("-PT5H", "PT6H")


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space."""
    out, raw = [], line.encode()
    while len(raw) > 75:
        cut = 75
        while cut and (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode())
        raw = b" " + raw[cut:]
    out.append(raw.decode())
    return "\r\n".join(out)


def _at(day: datetime.date, minutes: int, tz) -> datetime.datetime:
    """Local wall time `minutes` after midnight of `day` (1440: the next midnight)."""
    return datetime.datetime.combine(day, datetime.time(), tz) + datetime.timedelta(minutes=minutes)


def _utc(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def _event(
    uid: str,
    summary: str,
    start,
    end,
    stamp: str,
    alarms: tuple[str, ...],
    description: str = "",
    location: str = "",
) -> list[str]:
    """One VEVENT; start/end are dates (all-day) or aware datetimes."""
    if isinstance(start, datetime.datetime):
        when = [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    else:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        *when,
        f"SUMMARY:{_esc(summary)}",
        "TRANSP:TRANSPARENT",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    for alarm in alarms:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_esc(summary)}",
            f"TRIGGER:{alarm}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def build(
    prefs: dict,
    locate: Callable[[float, float], CarSpot | None],
    now: datetime.datetime | None = None,
    *,
    days: int = DAYS,
    first_only: bool = False,
) -> str:
    """The feed for an account's prefs ({"cars": [...], "homes": [...]}); `locate`
    resolves a car's position like /check. A car's side is the one its owner picked
    ("side"), else both. first_only: each car's next sweep day only, no holidays."""
    now = now or datetime.datetime.now(datetime.UTC)
    stamp = _utc(now)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:BroomBuster",
        "X-PUBLISHED-TTL:PT6H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]
    cities_seen: dict[str, str] = {}  # city -> region, for the holidays

    for car in prefs.get("cars") or []:
        spot = locate(car["lat"], car["lon"])
        if spot is None:
            continue
        cities_seen[spot.city] = spot.region
        tz = ZoneInfo(REGIONS[spot.region]["tz"])
        today = now.astimezone(tz).date()
        entries = spot.entries
        if car.get("side") in ("even", "odd"):
            entries = {car["side"]: entries.get(car["side"], [])}
        name = car.get("name") or "Car"
        # first_only: from yesterday, whose overnight window may still run.
        start = today - datetime.timedelta(days=1) if first_only else today
        sweeps = schedule.iter_sweep_days(
            entries, spot.city, start, today + datetime.timedelta(days=days)
        )
        if first_only:  # stops at the first sweep still to come
            later = ((d, [it for it in its if _not_over(d, it[1], tz, now)]) for d, its in sweeps)
            first = next((s for s in later if s[1]), None)
            sweeps = [first] if first else []
        for day, items in sweeps:
            sides_at: dict[str, list[str]] = {}  # time -> sides swept then: one event each
            for side, time in items:
                sides_at.setdefault(time, []).append(side)
            for time, sides in sides_at.items():
                side = sides[0] if len(set(sides)) == 1 else "both"
                where = spot.address
                if side != "both":
                    where += f", {spot.labels[side == 'odd']} side"
                w = schedule.time_window(time)
                uid = f"sweep-{car['id']}-{day:%Y%m%d}-{side}-{w[0] if w else 'day'}@broombuster"
                summary = f"Move {name}: street sweeping"
                if w:
                    start = _at(day, w[0], tz)
                    end = _at(day + datetime.timedelta(days=1) if w[1] <= w[0] else day, w[1], tz)
                    lines += _event(
                        uid,
                        summary,
                        start,
                        end,
                        stamp,
                        TIMED_ALARMS,
                        f"{where}, {normalize.time_display(time)}",
                        spot.address,
                    )
                else:
                    lines += _event(
                        uid,
                        summary,
                        day,
                        day + datetime.timedelta(days=1),
                        stamp,
                        DAY_ALARMS,
                        where,
                        spot.address,
                    )

    lookups = 0  # trash answers fetched (not cached) so far, at most TRASH_LOOKUPS
    for i, home in enumerate(prefs.get("homes") or []):
        region = _region_of(home)
        if region is None:
            continue
        city = city_for_point(home["lat"], home["lon"], region)
        today = now.astimezone(ZoneInfo(REGIONS[region]["tz"])).date()
        args = (city, home.get("address") or "", today, home.get("collection_day"))
        picks = trash.pickups(*args, network=False)
        if picks is None and lookups < TRASH_LOOKUPS:
            lookups += 1
            picks = trash.pickups(*args)
        picks = picks or {}
        label = "Home" if len(prefs.get("homes") or []) == 1 else f"Home {i + 1}"
        by_day: dict[str, list[str]] = {}
        for stream, dates in picks.items():
            for iso in dates:
                by_day.setdefault(iso, []).append(stream)
        for iso, streams in sorted(by_day.items()):
            day = datetime.date.fromisoformat(iso)
            lines += _event(
                f"trash-{home['id']}-{day:%Y%m%d}@broombuster",
                f"{label} · trash day: {', '.join(streams).lower()}",
                day,
                day + datetime.timedelta(days=1),
                stamp,
                DAY_ALARMS[:1],
                location=home.get("address") or "",
            )

    for city, region in ({} if first_only else cities_seen).items():
        today = now.astimezone(ZoneInfo(REGIONS[region]["tz"])).date()
        last = today + datetime.timedelta(days=days)
        for day, h in sorted(holidays.for_city(city).items()):
            kind = next((k for k in _NO_SWEEP if k in h["suspends"]), None)
            if kind and today <= day <= last:
                lines += [
                    "BEGIN:VEVENT",
                    f"UID:holiday-{city}-{day:%Y%m%d}@broombuster",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                    f"DTEND;VALUE=DATE:{day + datetime.timedelta(days=1):%Y%m%d}",
                    f"SUMMARY:{_esc(_NO_SWEEP[kind] + h['name'])}",
                    "TRANSP:TRANSPARENT",
                    "END:VEVENT",
                ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


_NO_SWEEP = {"sweeping": "No street sweeping: ", "sweeping_day": "No daytime street sweeping: "}


def _not_over(day: datetime.date, time: str, tz, now: datetime.datetime) -> bool:
    """Whether a sweep on `day` with window text `time` is still to come or running."""
    next_day = day + datetime.timedelta(days=1)
    w = schedule.time_window(time)
    end = _at(next_day, 0, tz) if not w else _at(next_day if w[1] <= w[0] else day, w[1], tz)
    return end > now


def _region_of(item: dict) -> str | None:
    try:
        return region_for_point(item["lat"], item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
