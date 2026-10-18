"""The calendar subscription feed (iCalendar, RFC 5545): an account's cars' street
sweeping, its homes' trash pickups and the no-sweep holidays of those cities, for
the next DAYS days, each with a reminder the evening before."""

import datetime
from collections.abc import Callable
from typing import NamedTuple
from zoneinfo import ZoneInfo

from broombuster import holidays, schedule, trash
from broombuster.cities import REGIONS, city_for_point, region_for_point

DAYS = 60
PRODID = "-//BroomBuster//Sweeping calendar//EN"


class CarSpot(NamedTuple):
    """What /check knows about where a car is."""
    region: str
    city: str
    address: str
    entries: dict      # {"even": [[code, desc, time]], "odd": [...]}
    side: str | None   # the car's side ("even" / "odd"), None when unknown


def _esc(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


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


def _event(uid: str, summary: str, start, end, stamp: str, alarm: str,
           description: str = "", location: str = "") -> list[str]:
    """One VEVENT; start/end are dates (all-day) or aware datetimes."""
    if isinstance(start, datetime.datetime):
        when = [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    else:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", *when,
             f"SUMMARY:{_esc(summary)}", "TRANSP:TRANSPARENT"]
    if description:
        lines.append(f"DESCRIPTION:{_esc(description)}")
    if location:
        lines.append(f"LOCATION:{_esc(location)}")
    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(summary)}",
              f"TRIGGER:{alarm}", "END:VALARM", "END:VEVENT"]
    return lines


def build(prefs: dict, locate: Callable[[float, float], CarSpot | None],
          now: datetime.datetime | None = None) -> str:
    """The feed for an account's prefs ({"cars": [...], "homes": [...]}); `locate`
    resolves a car's position like /check."""
    now = now or datetime.datetime.now(datetime.UTC)
    stamp = _utc(now)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:BroomBuster",
             "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    cities_seen: dict[str, str] = {}  # city -> region, for the holidays

    for car in prefs.get("cars") or []:
        spot = locate(car["lat"], car["lon"])
        if spot is None:
            continue
        cities_seen[spot.city] = spot.region
        tz = ZoneInfo(REGIONS[spot.region]["tz"])
        today = now.astimezone(tz).date()
        entries = spot.entries
        if spot.side in ("even", "odd"):
            entries = {spot.side: entries.get(spot.side, [])}
        name = car.get("name") or "Car"
        for day, items in schedule.sweep_days(entries, spot.city, today,
                                              today + datetime.timedelta(days=DAYS)):
            for side, time in items:
                w = schedule.time_window(time)
                uid = f"sweep-{car['id']}-{day:%Y%m%d}-{side}-{w[0] if w else 'day'}@broombuster"
                summary = f"Move {name}: street sweeping"
                if w:
                    start = _at(day, w[0], tz)
                    end = _at(day + datetime.timedelta(days=1) if w[1] <= w[0] else day, w[1], tz)
                    lines += _event(uid, summary, start, end, stamp, "-PT12H",
                                    f"{spot.address} ({side} side) {time}", spot.address)
                else:
                    lines += _event(uid, summary, day, day + datetime.timedelta(days=1), stamp,
                                    "-PT5H", f"{spot.address} ({side} side)", spot.address)

    for i, home in enumerate(prefs.get("homes") or []):
        region = _region_of(home)
        if region is None:
            continue
        city = city_for_point(home["lat"], home["lon"], region)
        today = now.astimezone(ZoneInfo(REGIONS[region]["tz"])).date()
        picks = trash.pickups(city, home.get("address") or "", today, home.get("collection_day"))
        label = "Home" if len(prefs.get("homes") or []) == 1 else f"Home {i + 1}"
        by_day: dict[str, list[str]] = {}
        for stream, dates in picks.items():
            for iso in dates:
                by_day.setdefault(iso, []).append(stream)
        for iso, streams in sorted(by_day.items()):
            day = datetime.date.fromisoformat(iso)
            lines += _event(f"trash-{home['id']}-{day:%Y%m%d}@broombuster",
                            f"{label}: {', '.join(streams)} pickup", day,
                            day + datetime.timedelta(days=1), stamp, "-PT5H",
                            location=home.get("address") or "")

    for city, region in cities_seen.items():
        today = now.astimezone(ZoneInfo(REGIONS[region]["tz"])).date()
        for day, h in sorted(holidays.for_city(city).items()):
            last = today + datetime.timedelta(days=DAYS)
            if "sweeping" in h["suspends"] and today <= day <= last:
                lines += ["BEGIN:VEVENT", f"UID:holiday-{city}-{day:%Y%m%d}@broombuster",
                          f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                          f"DTEND;VALUE=DATE:{day + datetime.timedelta(days=1):%Y%m%d}",
                          f"SUMMARY:{_esc('No street sweeping: ' + h['name'])}",
                          "TRANSP:TRANSPARENT", "END:VEVENT"]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


def _region_of(item: dict) -> str | None:
    try:
        return region_for_point(item["lat"], item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
