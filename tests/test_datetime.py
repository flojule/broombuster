"""Dates and times end to end: source normalisers, the committed data, and the
frontend's evaluation and display (frontend/js/urgency.js, run under node).

The JS is checked against independent Python oracles (datetime / zoneinfo)
over every code and time window in the committed data, not only hand-picked
cases.
"""
import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas
import pandas as pd
import pytest
import shapely.geometry

from broombuster import analysis, normalize, schemas
from broombuster.cities import REGIONS

_DIR = Path(__file__).parent
_FRONTEND = _DIR.parent / "frontend" / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def js(*calls):
    """Results of BroomUrgency calls: js(("fn", [args]), ...)."""
    out = subprocess.run(["node", str(_DIR / "_js_call.js")], input=json.dumps(calls),
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def ymd(d: datetime.date) -> dict:
    return {"y": d.year, "m": d.month, "d": d.day}


def now(d: datetime.date, hh: int = 12, mm: int = 0) -> dict:
    return {**ymd(d), "min": hh * 60 + mm}


WED = datetime.date(2026, 10, 14)

# ── Independent oracles ──────────────────────────────────────────────────────

_TOKENS = {"M": 0, "T": 1, "W": 2, "TH": 3, "F": 4, "S": 5, "SU": 6}


def oracle_days(code: str):
    """(weekdays, week-of-month ordinals or None) for a weekly code, else None."""
    m = re.fullmatch(r"((?:TH|SU|M|T|W|F|S)*)(E?|[1-5]+)", code.strip().upper())
    if analysis.is_no_sweep_code(code) or not m or not (m[1] or m[2] == "E"):
        return None
    days = {_TOKENS[t] for t in re.findall(r"TH|SU|M|T|W|F|S", m[1])} or set(range(7))
    return days, ({int(c) for c in m[2]} if m[2].isdigit() else None)


def oracle_sweeps(code: str, day: datetime.date) -> bool:
    """Whether `code` sweeps on `day`; nth weekday counted by enumeration."""
    if code.upper().startswith("DATES:"):
        return day.isoformat() in {s.strip() for s in code[6:].split(",")}
    parsed = oracle_days(code)
    if parsed is None or day.weekday() not in parsed[0]:
        return False
    nth = sum(1 for k in range(1, day.day + 1)
              if day.replace(day=k).weekday() == day.weekday())
    return parsed[1] is None or nth in parsed[1]


_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_label(line: str):
    """(weekdays, ordinals or None) named by a display line's day label."""
    label = line.split(" · ")[0]
    if label.startswith("Every day"):
        return set(range(7)), None
    days = set()
    for a, b in re.findall(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:–(Mon|Tue|Wed|Thu|Fri|Sat|Sun))?",
                           label):
        days |= set(range(_WD.index(a), _WD.index(b or a) + 1))
    ords = {int(n) for n in re.findall(r"([1-5])(?:st|nd|rd|th)\b", label)}
    return days, (ords or None)


# ── Data under test ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_rows(bay_area_gdf, chicago_gdf):
    return pd.concat([bay_area_gdf, chicago_gdf], ignore_index=True)


@pytest.fixture(scope="module")
def data_codes(all_rows):
    codes = pd.concat([all_rows["DAY_EVEN"], all_rows["DAY_ODD"]]).dropna().unique()
    return sorted(c for c in codes if normalize.is_text(c) and not analysis.is_no_sweep_code(c))


@pytest.fixture(scope="module")
def data_times(all_rows):
    s = pd.concat([all_rows["TIME_EVEN"], all_rows["TIME_ODD"]])
    return sorted(t for t in s.dropna().unique() if normalize.clean_text(t))


SYNTHETIC_CODES = ["E", "ME", "M13", "M135", "M245", "TTHS", "MTWTHF", "SSU", "SUE", "FE",
                   "TH5", "me", " THE ", "MWF", "THFE", "F1", "W4"]


# ── Sweep days: JS vs oracle ─────────────────────────────────────────────────

@needs_node
def test_dates_in_range_match_oracle_for_every_code(data_codes):
    start, end = datetime.date(2026, 1, 1), datetime.date(2028, 12, 31)  # 2028 is a leap year
    weekly = [c for c in data_codes if not c.startswith("DATES:")] + SYNTHETIC_CODES
    dated = [c for c in data_codes if c.startswith("DATES:")]
    codes = weekly + dated
    got = js(*[("datesInRange", [c, ymd(start), ymd(end)]) for c in codes])
    days = [start + datetime.timedelta(n) for n in range((end - start).days + 1)]
    for code, dates in zip(codes, got, strict=True):
        want = [ymd(d) for d in days if oracle_sweeps(code, d)]
        assert dates == want, code


# ── Region-local clock ───────────────────────────────────────────────────────

@needs_node
@pytest.mark.parametrize("region", sorted(REGIONS))
def test_now_for_time_zone_matches_zoneinfo(region):
    tz = REGIONS[region]["tz"]
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    instants = [t0 + datetime.timedelta(minutes=97 * k) for k in range(5500)]
    # Every 5 minutes around each DST change and the local midnights next to it.
    for day in ("2026-03-08", "2026-11-01"):
        base = datetime.datetime.fromisoformat(day + "T00:00:00+00:00")
        instants += [base + datetime.timedelta(minutes=5 * k) for k in range(24 * 12 * 2)]
    got = js(*[("nowForTimeZone", [tz, {"$date": t.isoformat()}]) for t in instants])
    for t, n in zip(instants, got, strict=True):
        loc = t.astimezone(ZoneInfo(tz))
        assert n == {"y": loc.year, "m": loc.month, "d": loc.day,
                     "min": loc.hour * 60 + loc.minute}, t


# ── Time windows ─────────────────────────────────────────────────────────────

TIME_CASES = [
    # raw, display, [start, end] minutes (end < start: runs past midnight)
    ("9:00AM-12:00PM", "9AM–12PM", [540, 720]),
    ("12:00AM-3:00AM", "12AM–3AM", [0, 180]),
    ("12:30PM-3:30PM", "12:30PM–3:30PM", [750, 930]),
    ("10PM–12AM", "10PM–12AM", [1320, 1440]),
    ("10PM-2AM", "10PM–2AM", [1320, 120]),
    ("1 0:00 AM -1: 00 PM", "10AM–1PM", [600, 780]),
    ("12:00 PM-3:00 P,M", "12PM–3PM", [720, 900]),
    ("4:00 AM o 7:00 AM", "4AM–7AM", [240, 420]),
    ("8:00 AM to 9:30 AM", "8AM–9:30AM", [480, 570]),
    ("AM", "AM", [0, 720]),          # Berkeley: a morning route
    ("pm", "PM", [720, 1440]),       # Berkeley: an afternoon route
    ("13:00PM-14:00PM", "13:00PM-14:00PM", None),  # not a clock time: shown as is
    ("0AM-2AM", "0AM-2AM", None),
    ("8AM", "8AM", None),
    ("NA", "N/A", None),
    ("", "N/A", None),
]


@needs_node
@pytest.mark.parametrize("raw,display,window", TIME_CASES)
def test_time_display_and_window(raw, display, window):
    assert js(("timeDisplay", [raw]), ("timeWindow", [raw])) == [display, window]


@needs_node
def test_every_data_time_window_parses_and_matches_python(data_times):
    got = js(*[c for t in data_times for c in (("timeDisplay", [t]), ("timeWindow", [t]))])
    for t, disp, win in zip(data_times, got[::2], got[1::2], strict=True):
        assert win is not None, t
        assert win[0] < win[1], (t, win)  # no committed window runs past midnight
        if t.upper() not in ("AM", "PM"):
            assert disp == normalize.time_display(t), t
            assert re.fullmatch(r"\d{1,2}(:\d\d)?[AP]M–\d{1,2}(:\d\d)?[AP]M", disp), t


# ── Urgency verdict around window edges and calendar edges ───────────────────

THU = WED + datetime.timedelta(1)
URGENCY_CASES = [
    ("WE", "8AM-10AM", now(WED, 9, 59), "today"),
    ("WE", "8AM-10AM", now(WED, 10, 0), "today"),
    ("WE", "8AM-10AM", now(WED, 10, 1), "safe"),
    ("WE", "8AM-10AM", now(WED, 0, 0), "today"),
    ("WE", "10PM-12AM", now(WED, 23, 59), "today"),
    ("WE", "AM", now(WED, 11, 59), "today"),
    ("WE", "AM", now(WED, 12, 1), "safe"),
    ("WE", "PM", now(WED, 23, 0), "today"),
    ("WE", "10PM-2AM", now(WED, 23, 0), "today"),   # running past midnight
    ("WE", "10PM-2AM", now(THU, 1, 0), "today"),    # ... into Thursday
    ("WE", "10PM-2AM", now(THU, 2, 1), "safe"),
    ("WE", "", now(WED, 23, 59), "today"),          # untimed: all day
    ("WE", "garbled", now(WED, 23, 59), "today"),
    # A sweep starting within 6 hours is today's problem, even past midnight.
    ("THE", "12AM-3AM", now(WED, 23, 0), "today"),
    ("THE", "12AM-3AM", now(WED, 18, 0), "today"),
    ("THE", "12AM-3AM", now(WED, 17, 59), "tomorrow"),
    ("THE", "2AM-6AM", now(WED, 20, 0), "today"),
    ("THE", "2AM-6AM", now(WED, 19, 59), "tomorrow"),
    ("THE", "8AM-10AM", now(WED, 23, 59), "tomorrow"),
    ("THE", "AM", now(WED, 18, 0), "today"),        # morning route: from midnight
    ("THE", "", now(WED, 18, 0), "today"),          # untimed: from midnight
    ("THE", "PM", now(WED, 23, 59), "tomorrow"),
    ("THE", "12AM-3AM", now(THU, 0, 0), "today"),
    ("F1", "1AM-3AM", now(datetime.date(2026, 12, 31), 20), "today"),  # into next year
    ("FE", "8AM-10AM", now(datetime.date(2026, 12, 31), 12), "tomorrow"),  # year rollover
    ("F1", "8AM-10AM", now(datetime.date(2026, 12, 31), 12), "tomorrow"),
    ("TU5", "8AM-10AM", now(WED), "safe"),           # not a code
    ("T5", "8AM-10AM", now(datetime.date(2028, 2, 28), 12), "tomorrow"),  # Feb 29, 5th Tue
    ("DATES:2026-03-01", "", now(datetime.date(2026, 2, 28), 12), "tomorrow"),
]


@needs_node
@pytest.mark.parametrize("code,time,at,want", URGENCY_CASES)
def test_check_day_sweeping(code, time, at, want):
    assert js(("checkDaySweeping", [[{"code": code, "time": time}], at])) == [want]


DEADLINE_CASES = [
    ([("WE", "9AM-11AM")], now(WED, 7, 0), {"state": "by", "at": "9AM"}),
    ([("WE", "9AM-11AM")], now(WED, 10, 0), {"state": "running", "at": "11AM"}),
    ([("WE", "9AM-11AM")], now(WED, 11, 1), None),
    ([("WE", "")], now(WED, 20, 0), {"state": "today", "at": None}),
    ([("THE", "9:30AM-11AM")], now(WED, 12, 0), {"state": "tomorrow", "at": "9:30AM"}),
    ([("THE", "")], now(WED, 12, 0), {"state": "tomorrow", "at": None}),
    ([("THE", "12AM-3AM")], now(WED, 20, 0), {"state": "by", "at": "midnight"}),
    ([("WE", "10PM-2AM")], now(THU, 1, 0), {"state": "running", "at": "2AM"}),
    # The running window wins over a later one; the sooner start wins among starts.
    ([("WE", "2PM-4PM"), ("WE", "9AM-11AM")], now(WED, 10, 0), {"state": "running", "at": "11AM"}),
    ([("WE", "2PM-4PM"), ("WE", "12PM-1PM")], now(WED, 10, 0), {"state": "by", "at": "12PM"}),
]


@needs_node
@pytest.mark.parametrize("entries,at,want", DEADLINE_CASES)
def test_sweep_deadline(entries, at, want):
    es = [{"code": c, "time": t} for c, t in entries]
    assert js(("sweepDeadline", [es, at])) == [want]
    # Due exactly when the verdict says the car must move.
    assert (want is not None) == (js(("checkDaySweeping", [es, at]))[0] != "safe")


# ── Schedule display ─────────────────────────────────────────────────────────

@needs_node
def test_display_names_every_sweep_day_of_every_code(data_codes):
    weekly = [c for c in data_codes if not c.startswith("DATES:")] + SYNTHETIC_CODES
    got = js(*[("formatScheduleSide", [[[c, "", "8AM-10AM"]], now(WED)]) for c in weekly])
    for code, lines in zip(weekly, got, strict=True):
        assert len(lines) == 1, (code, lines)
        assert parse_label(lines[0]) == oracle_days(code), (code, lines)


DISPLAY_CASES = [
    ([["MWF", "Every Mon, Wed, Fri", "9:00AM-12:00PM"]], ["Mon, Wed, Fri · 9AM–12PM"]),
    ([["MTWTHF", "", "8AM-10AM"]], ["Mon–Fri · 8AM–10AM"]),
    ([["E", "Everyday", "9:00AM-12:00PM"]], ["Every day · 9AM–12PM"]),
    ([["E", "Every day", "4AM–5AM"]], ["Every day · 4AM–5AM"]),
    ([["ME", "", "8AM-10AM"], ["M13", "", "8AM-10AM"]], ["Mon · 8AM–10AM"]),
    ([["M13", "", "8AM-10AM"], ["W13", "", "8AM-10AM"]], ["Mon, Wed 1st & 3rd · 8AM–10AM"]),
    ([["ME", "", "8AM-10AM"], ["ME", "", "12PM-2PM"]], ["Mon · 8AM–10AM", "Mon · 12PM–2PM"]),
    ([["F1", "1st Fri", "AM"]], ["Fri 1st · AM"]),
    ([["TTHS", "", "NA"]], ["Tue, Thu, Sat"]),
    ([["DATES:2026-10-13,2026-10-14,2026-10-16,2026-11-20", "", ""]], ["Oct 14, 16"]),
    ([["DATES:2026-12-30,2027-01-02", "", ""]], ["Dec 30; Jan 2"]),
    ([["DATES:2026-10-01", "", ""]], []),  # season over
]


@needs_node
@pytest.mark.parametrize("entries,lines", DISPLAY_CASES)
def test_format_schedule_side(entries, lines):
    assert js(("formatScheduleSide", [entries, now(WED)])) == [lines]


DAY_TEXT_CASES = [
    ([["even", "9:00AM-12:00PM"], ["odd", "9AM–12PM"]], "9AM–12PM"),
    ([["even", "9AM-12PM"]], "Even side 9AM–12PM"),
    ([["odd", "9AM-12PM"]], "Odd side 9AM–12PM"),
    ([["even", "8AM-10AM"], ["odd", "12PM-2PM"]], "Even 8AM–10AM, Odd 12PM–2PM"),
    ([["even", "8AM-10AM"], ["even", "12PM-2PM"]], "Even side 8AM–10AM, 12PM–2PM"),
    ([["even", ""], ["odd", ""]], "time n/a"),
]


@needs_node
@pytest.mark.parametrize("items,text", DAY_TEXT_CASES)
def test_calendar_day_text(items, text):
    items = [{"side": s, "time": t} for s, t in items]
    assert js(("dayText", [items, ["Even", "Odd"]])) == [text]


@needs_node
def test_day_label_and_iso_day():
    days = [datetime.date(2026, 10, 14), datetime.date(2027, 1, 1), datetime.date(2028, 2, 29)]
    got = js(*[("dayLabel", [ymd(d)]) for d in days], *[("isoDay", [ymd(d)]) for d in days])
    assert got == [d.strftime("%a %b %-d") for d in days] + [d.isoformat() for d in days]


# ── Frontend reads the clock only through the region-local helper ────────────

def test_frontend_uses_region_clock():
    """Browser-local calendar fields would put a Bay Area car on Chicago's day."""
    allowed = {("core.js", "_isNight"), ("urgency.js", "d.get")}  # unknown-zone fallback
    pat = re.compile(r"\.(getDate|getDay|getHours|getMinutes|getMonth|getFullYear|"
                     r"toDateString|toLocaleDateString|toLocaleTimeString)\(")
    bad = []
    for f in sorted(_FRONTEND.glob("*.js")):
        if f.name.startswith("maplibre"):
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if pat.search(line) and not any(f.name == a and k in line for a, k in allowed):
                bad.append(f"{f.name}:{n}: {line.strip()}")
            if "nowForTimeZone(" in line and f.name not in ("core.js", "urgency.js"):
                bad.append(f"{f.name}:{n}: use regionNow() — {line.strip()}")
    assert not bad, "\n".join(bad)


# ── Source normalisers ───────────────────────────────────────────────────────

def _sf(**kw):
    row = {"corridor": "MARKET ST", "blockside": "EVEN", "week_day": "1",
           "from_hour": 8, "to_hour": 10, **{f"week_{n}_of_month": 0 for n in range(1, 6)}}
    row.update(kw)
    gdf = geopandas.GeoDataFrame([row], geometry=[shapely.geometry.LineString([(0, 0), (1, 0)])],
                                 crs="EPSG:4326")
    return schemas._normalise_sf(gdf).iloc[0]


@pytest.mark.parametrize("fh,th,want", [
    (0, 2, "12AM–2AM"),
    (10, 12, "10AM–12PM"),
    (12, 14, "12PM–2PM"),
    (22, 24, "10PM–12AM"),   # hour 24 is midnight, not noon
    (0, 24, "12AM–12AM"),
    (6.5, 8, "6:30AM–8AM"),  # half hours are kept
    ("7", "9", "7AM–9AM"),
])
def test_sf_time_window(fh, th, want):
    r = _sf(from_hour=fh, to_hour=th)
    time, desc = r["TIME_EVEN"], r["DESC_EVEN"]
    assert time == want
    assert desc.endswith(", " + want)


def test_sf_missing_hours_are_untimed():
    r = _sf(from_hour=None, to_hour=10)
    assert not normalize.clean_text(r.TIME_EVEN)
    assert not normalize.clean_text(r.DESC_EVEN).endswith("N/A")


def _oakland(code, desc):
    gdf = geopandas.GeoDataFrame(
        [{"NAME": "Grand", "TYPE": "Ave", "DAY_EVEN": code, "DAY_ODD": code,
          "DescDayEve": desc, "DescDayOdd": desc, "DescTimeEv": "9:00AM-12:00PM",
          "DescTimeOd": "9:00AM-12:00PM"}],
        geometry=[shapely.geometry.LineString([(0, 0), (1, 0)])], crs="EPSG:4326")
    return schemas._normalise_oakland(gdf).iloc[0]


def test_oakland_mon_to_fri_code():
    """Oakland codes "Every Mon to Fri" as MF, which reads as Mon and Fri only."""
    assert _oakland("MF", "Every Mon to Fri").DAY_EVEN == "MTWTHF"
    assert _oakland("MFE", "Every Mon and Fri").DAY_EVEN == "MFE"


_DESC_DAY = {"Mon": 0, "Tue": 1, "Tues": 1, "Wed": 2, "Thu": 3, "Thurs": 3, "Fri": 4,
             "Sat": 5, "Sun": 6}


def _desc_days(desc: str):
    """Weekdays a weekly source description names ("Every Mon to Fri", "1st and 3rd Wed")."""
    if re.search(r"every\s*day", desc, re.I):
        return set(range(7))
    names = re.findall(r"\b(Mon|Tues?|Wed|Thu(?:rs)?|Fri|Sat|Sun)\b", desc)
    days = {_DESC_DAY[n] for n in names}
    if re.search(r"\bto\b", desc) and len(names) == 2:
        days = set(range(_DESC_DAY[names[0]], _DESC_DAY[names[1]] + 1))
    return days


def test_committed_codes_agree_with_their_descriptions(bay_area_gdf):
    """A code's weekdays are the ones its source description names."""
    bad = set()
    for side in ("EVEN", "ODD"):
        for city, code, desc in zip(bay_area_gdf["_city"], bay_area_gdf[f"DAY_{side}"],
                                    bay_area_gdf[f"DESC_{side}"], strict=True):
            parsed = oracle_days(code) if normalize.is_text(code) else None
            if parsed and normalize.clean_text(desc) and parsed[0] != _desc_days(desc):
                bad.add((city, code, desc))
    assert not bad, sorted(bad)


def test_chicago_dates_are_weekdays_in_season(chicago_gdf):
    for code in chicago_gdf["DAY_EVEN"].dropna().unique():
        for d in code[6:].split(","):
            day = datetime.date.fromisoformat(d)
            assert day.weekday() < 5 and 4 <= day.month <= 11, (code[:40], d)


def test_format_dates_by_month_across_years():
    days = [datetime.date(2026, 12, 30), datetime.date(2026, 12, 31), datetime.date(2027, 1, 4)]
    assert analysis.format_dates_by_month(days) == "Dec 30, 31; Jan 4"
