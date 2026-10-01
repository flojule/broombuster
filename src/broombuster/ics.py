"""iCalendar (RFC 5545) feed of street-sweeping windows for one location."""

import datetime
import hashlib
from zoneinfo import ZoneInfo

from broombuster import analysis, normalize

# Days of sweeps published per feed; clients re-fetch every REFRESH.
HORIZON_DAYS = 180
REFRESH = "PT12H"
# Reminder offset before each window start.
ALARM = "-PT12H"


def _text(s: str) -> str:
    """Escape a TEXT value (RFC 5545 3.3.11)."""
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets per physical line (RFC 5545 3.1)."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def _utc(d: datetime.date, t: datetime.time, tz: ZoneInfo) -> str:
    return (datetime.datetime.combine(d, t, tzinfo=tz)
            .astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ"))


def build_calendar(even, odd, sides, labels, street, tz_name, uid_seed, today,
                   now_utc=None) -> str:
    """VCALENDAR text with one VEVENT per sweep day and time window.

    even / odd: (code, desc, time) entries; sides: subset of ("even", "odd")
    to publish; labels: (even, odd) display labels; times are region-local
    (tz_name) and emitted in UTC. Unparseable times become all-day events.
    """
    tz = ZoneInfo(tz_name)
    now_utc = now_utc or datetime.datetime.now(datetime.UTC)
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    seed = hashlib.sha1(uid_seed.encode()).hexdigest()[:12]
    names = dict(zip(("even", "odd"), labels))
    days = analysis.sweep_days(even, odd, today, today + datetime.timedelta(days=HORIZON_DAYS))

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BroomBuster//Street sweeping//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_text(f'Street sweeping - {street}')}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}", f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    for day, items in days.items():
        # One event per time window; the sides swept in it share the event.
        by_time: dict = {}
        for side, time in items:
            if side in sides:
                by_time.setdefault(time, []).append(side)
        for time, swept in by_time.items():
            where = "both sides" if len(swept) == 2 else f"{names[swept[0]]} side"
            window = normalize.time_window(time)
            slot = f"{window[0]:%H%M}" if window else "allday"
            uid = f"{day:%Y%m%d}-{'-'.join(swept)}-{slot}-{seed}@broombuster"
            lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
            if window:
                end_day = day + datetime.timedelta(days=window[1] <= window[0])  # overnight
                lines += [f"DTSTART:{_utc(day, window[0], tz)}",
                          f"DTEND:{_utc(end_day, window[1], tz)}"]
            else:
                lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                          f"DTEND;VALUE=DATE:{day + datetime.timedelta(days=1):%Y%m%d}"]
            shown = normalize.time_display(time) if time else "time not published"
            lines += [
                f"SUMMARY:{_text(f'Street sweeping ({where})')}",
                f"DESCRIPTION:{_text(f'{street}, {where}: {shown}. Move your car.')}",
                "TRANSP:TRANSPARENT",
                "BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{ALARM}",
                f"DESCRIPTION:{_text(f'Move car before street sweeping: {street} ({where})')}",
                "END:VALARM", "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"
