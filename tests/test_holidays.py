"""City holidays (data/holidays), the Python sweep-date rules the calendar feed uses
(schedule.py, checked against urgency.js), and weekly trash days."""

import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
import yaml

from broombuster import analysis, holidays, normalize, schedule, trash
from broombuster.cities import CITIES, REGIONS

_DIR = Path(__file__).parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def js(*calls):
    out = subprocess.run(
        ["node", str(_DIR / "_js_call.js")],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def ymd(d):
    return {"y": d.year, "m": d.month, "d": d.day}


# ── Holiday files ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("city", sorted(CITIES))
def test_every_city_has_a_holiday_file_with_this_and_next_year(city):
    days = holidays.for_city(city)
    assert days, f"{city}: no holidays file"
    today = datetime.date.today()
    years = {d.year for d in days}
    assert today.year in years
    # From December, next year's dates must be in (the lists change every year).
    if today.month == 12:
        assert today.year + 1 in years, f"{city}: add {today.year + 1}'s holidays"
    meta = yaml.safe_load((_DIR.parent / CITIES[city]["holidays"]).read_text(encoding="utf-8"))
    assert meta["source"].startswith("https://") and meta["last_verified"]


def _nth(month, weekday, n):
    """The nth `weekday` (Mon=0) of `month`; n=-1 the last."""

    def rule(year):
        if n > 0:
            first = datetime.date(year, month, 1)
            return first + datetime.timedelta((weekday - first.weekday()) % 7 + 7 * (n - 1))
        last = datetime.date(year, month + 1, 1) - datetime.timedelta(1)
        return last - datetime.timedelta((last.weekday() - weekday) % 7)

    return rule


def _on(month, day):
    return lambda year: datetime.date(year, month, day)


_NEW_YEAR = ("New Year's Day", _on(1, 1))
_MLK = ("Martin Luther King Jr. Day", _nth(1, 0, 3))
_MEMORIAL = ("Memorial Day", _nth(5, 0, -1))
_JUNETEENTH = ("Juneteenth", _on(6, 19))
_JULY_4 = ("Independence Day", _on(7, 4))
_LABOR = ("Labor Day", _nth(9, 0, 1))
_VETERANS = ("Veterans Day", _on(11, 11))
_THANKSGIVING = ("Thanksgiving Day", _nth(11, 3, 4))
_DAY_AFTER = ("Day after Thanksgiving", lambda y: _nth(11, 3, 4)(y) + datetime.timedelta(1))
_CHRISTMAS = ("Christmas Day", _on(12, 25))
_TRASH = [_NEW_YEAR, _THANKSGIVING, _CHRISTMAS]

_OAKLAND = [_NEW_YEAR, _MLK, ("Presidents Day", _nth(2, 0, 3)), ("Farmworkers Day", _on(3, 31)),
            _MEMORIAL, _JUNETEENTH, _JULY_4, _LABOR, ("California Admission Day", _on(9, 9)),
            _VETERANS, _THANKSGIVING, _DAY_AFTER, _CHRISTMAS]  # fmt: skip
_SF_DAYTIME = [_MLK, ("Presidents' Day", _nth(2, 0, 3)), _MEMORIAL, _JUNETEENTH, _JULY_4, _LABOR,
               ("Indigenous Peoples Day", _nth(10, 0, 2)), _VETERANS, _DAY_AFTER]  # fmt: skip
_CHICAGO = [_NEW_YEAR, _MEMORIAL, _JULY_4, _LABOR, _THANKSGIVING, _CHRISTMAS]
_XMAS_NEW_YEAR = [_NEW_YEAR, _CHRISTMAS]  # most haulers: Republic, Recology, MVRS, SSF Scavenger
_DALY = [_NEW_YEAR, _MLK, ("Presidents' Day", _nth(2, 0, 3)), _MEMORIAL, _JUNETEENTH, _JULY_4,
         _LABOR, _VETERANS, _THANKSGIVING, _DAY_AFTER, _CHRISTMAS]  # fmt: skip
_SSF = [*_DALY[:7], ("Indigenous Peoples Day", _nth(10, 0, 2)), *_DALY[7:]]

# city: [(holidays, suspends, observed)]; observed: "date" (the date only), "both" (plus the
# weekday a weekend date is observed on), "weekday" (only the observed weekday).
_EXPECTED = {
    "oakland": [(_OAKLAND, ["sweeping"], "both")],
    "san_francisco": [(_TRASH, ["sweeping"], "date"), (_SF_DAYTIME, ["sweeping_day"], "weekday")],
    "berkeley": [(_TRASH, ["trash"], "date")],
    "alameda": [(_TRASH, ["trash"], "date")],
    "chicago_all": [(_CHICAGO, ["trash"], "date")],
    "vacaville": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "sonoma_county": [
        ([_NEW_YEAR, _LABOR, _THANKSGIVING, _CHRISTMAS], ["trash"], "date"),
        ([_JULY_4], ["trash"], "weekday"),
    ],
    "fairfield": [(_TRASH, ["sweeping", "trash"], "date")],
    "benicia": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "napa": [(_TRASH, ["sweeping", "trash"], "date")],
    "san_ramon": [(_TRASH, ["trash"], "date")],
    "danville": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "oakley": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "pittsburg": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "san_rafael": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "mill_valley": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "corte_madera": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "tiburon": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "sausalito": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "belvedere": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "south_san_francisco": [(_XMAS_NEW_YEAR, ["trash"], "date"), (_SSF, ["sweeping"], "weekday")],
    "daly_city": [(_XMAS_NEW_YEAR, ["trash"], "date"), (_DALY, ["sweeping"], "weekday")],
    "millbrae": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "san_mateo": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "san_bruno": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "san_mateo_county": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "burlingame": [(_XMAS_NEW_YEAR, ["trash"], "date")],
    "menlo_park": [(_XMAS_NEW_YEAR, ["trash"], "date")],
}
_SF_DAYTIME_YEARS = {2026}  # SFMTA has not published 2027's daytime-only holidays
_ALSO_SUSPENDED = {("san_francisco", datetime.date(2026, 1, 1)): "trash"}  # Recology moved pickups
_ALSO_SUSPENDED |= {(city, day): "trash" for city in ("south_san_francisco", "daly_city")
                    for day in (datetime.date(2026, 1, 1), datetime.date(2026, 12, 25),
                                datetime.date(2027, 1, 1))}  # fmt: skip


def _expected(city, year):
    """{date: {"name", "suspends"}} the rules give for a city's year. An observed day in the
    year before (Oakland: no Fri Dec 31 2027 for New Year's Day 2028) is not expected."""
    out = {}
    for days, suspends, observed in _EXPECTED[city]:
        if suspends == ["sweeping_day"] and year not in _SF_DAYTIME_YEARS:
            continue
        for name, rule in days:
            day = rule(year)
            weekday = day + datetime.timedelta({5: -1, 6: 1}.get(day.weekday(), 0))
            if observed != "weekday" or weekday == day:
                out[day] = {"name": name, "suspends": list(suspends)}
            if observed != "date" and weekday != day and weekday.year == year:
                out[weekday] = {"name": f"{name} (observed)", "suspends": list(suspends)}
    for (ck, day), service in _ALSO_SUSPENDED.items():
        if ck == city and day.year == year:
            out[day]["suspends"].append(service)
    return out


@pytest.mark.parametrize("city", sorted(CITIES))
def test_holiday_files_list_every_observed_holiday(city):
    days = holidays.for_city(city)
    for year in sorted({d.year for d in days}):
        got = {d: h for d, h in days.items() if d.year == year}
        assert got == _expected(city, year), f"{city} {year}"


def test_oakland_and_sf_suspend_sweeping_others_only_trash():
    vets = datetime.date(2026, 11, 11)
    assert holidays.suspended("oakland", "sweeping", vets) == "Veterans Day"
    assert holidays.suspended("san_francisco", "sweeping", vets) is None  # night cleaning runs
    xmas = datetime.date(2026, 12, 25)
    assert holidays.suspended("san_francisco", "sweeping", xmas) == "Christmas Day"
    for city in ("berkeley", "alameda", "chicago_all"):
        assert not any("sweeping" in h["suspends"] for h in holidays.for_city(city).values())
    assert holidays.suspended("berkeley", "trash", xmas) == "Christmas Day"


def test_for_region_is_the_client_copy():
    reg = holidays.for_region("bay_area")
    assert reg["oakland"]["2026-11-11"] == {"name": "Veterans Day", "suspends": ["sweeping"]}
    assert set(reg) == set(REGIONS["bay_area"]["cities"])


# ── Sweep dates: Python vs urgency.js ────────────────────────────────────────


@pytest.fixture(scope="module")
def data_codes(bay_area_gdf, chicago_gdf):
    rows = pd.concat([bay_area_gdf, chicago_gdf], ignore_index=True)
    codes = pd.concat([rows["DAY_EVEN"], rows["DAY_ODD"]]).dropna().unique()
    return sorted(c for c in codes if normalize.is_text(c) and not analysis.is_no_sweep_code(c))


@needs_node
def test_sweeps_on_matches_urgency_js_for_every_code(data_codes):
    start, end = datetime.date(2026, 1, 1), datetime.date(2027, 12, 31)
    codes = [*data_codes, "E", "M13", "TTHS", "SUE", "TH5", " the ", "N", "garbage"]
    got = js(*[("datesInRange", [c, ymd(start), ymd(end)]) for c in codes])
    days = [start + datetime.timedelta(n) for n in range((end - start).days + 1)]
    for code, dates in zip(codes, got, strict=True):
        assert dates == [ymd(d) for d in days if schedule.sweeps_on(code, d)], code


@needs_node
def test_time_window_matches_urgency_js(bay_area_gdf):
    raw = pd.concat([bay_area_gdf["TIME_EVEN"], bay_area_gdf["TIME_ODD"]]).dropna()
    times = sorted({t for t in raw if normalize.clean_text(t)} | {"12AM-6AM", "AM", "pm", "junk"})
    got = js(*[("timeWindow", [t]) for t in times])
    for t, w in zip(times, got, strict=True):
        assert schedule.time_window(t) == (tuple(w) if w else None), t


@needs_node
def test_holiday_skip_in_js():
    hol = {
        "2026-11-11": {"name": "Veterans Day", "suspends": ["sweeping"]},
        "2026-11-26": {"name": "Thanksgiving", "suspends": ["trash"]},
    }
    wed = {"y": 2026, "m": 11, "d": 11, "min": 300}
    wed_8am, vets = [{"code": "W", "time": "8AM-10AM"}], {"20261111": "Veterans Day"}
    skip, verdict, plain, dates = js(
        ("holidaySkip", [hol, "sweeping"]),
        ("checkDaySweeping", [wed_8am, wed, vets]),
        ("checkDaySweeping", [wed_8am, wed]),
        (
            "datesInRange",
            ["W", ymd(datetime.date(2026, 11, 4)), ymd(datetime.date(2026, 11, 18)), vets],
        ),
    )
    assert skip == {"20261111": "Veterans Day"}
    assert (verdict, plain) == ("safe", "today")
    assert dates == [ymd(datetime.date(2026, 11, 4)), ymd(datetime.date(2026, 11, 18))]


@needs_node
def test_daytime_only_holiday_in_js():
    """A 'sweeping_day' holiday (San Francisco) drops daytime sweeps, not night ones."""
    hol = {"2026-11-11": {"name": "Veterans Day", "suspends": ["sweeping_day"]}}
    skip = {"daytime": {"20261111": "Veterans Day"}}
    tue_9pm = {"y": 2026, "m": 11, "d": 10, "min": 21 * 60}
    nov11 = ymd(datetime.date(2026, 11, 11))
    listed, day, night, untimed, days = js(
        ("holidaySkip", [hol, "sweeping"]),
        ("checkDaySweeping", [[{"code": "W", "time": "8AM–10AM"}], tue_9pm, skip]),
        ("checkDaySweeping", [[{"code": "W", "time": "2AM–6AM"}], tue_9pm, skip]),
        ("checkDaySweeping", [[{"code": "W", "time": ""}], tue_9pm, skip]),
        ("sweepDays", [[["W", "", "8AM–10AM"], ["W", "", "12AM–6AM"]], [], nov11, nov11, skip]),
    )
    assert listed == {}  # not a no-sweep day on the calendar
    assert (day, night, untimed) == ("safe", "today", "today")  # untimed: assumed on
    assert [it["time"] for it in days[0]["items"]] == ["12AM–6AM"]


# On a daytime-only holiday (SFMTA): a window with any night cleaning (12AM-6AM) is on.
DAYTIME_CASES = [
    ("12AM-6AM", False),
    ("5AM-7AM", False),  # starts in night cleaning
    ("5:59AM-8AM", False),
    ("6AM-8AM", True),
    ("9AM-11AM", True),
    ("PM", True),  # afternoon route
    ("AM", False),  # morning route: from midnight
    ("10PM-12AM", True),  # ends at midnight: all of it that day's daytime
    ("10PM-2AM", False),  # runs into 12AM-2AM night cleaning
    ("11:59PM-12:30AM", False),
    ("", False),  # untimed: assumed on
]


@pytest.mark.parametrize("time,off", DAYTIME_CASES)
def test_daytime_only_holiday_window_boundaries(time, off):
    vets = datetime.date(2026, 11, 11)  # SF Veterans Day: sweeping_day
    assert (holidays.sweep_suspended("san_francisco", vets, schedule.time_window(time))) == (
        "Veterans Day" if off else None
    )


@needs_node
def test_daytime_only_holiday_rule_is_the_same_in_js():
    skip = {"daytime": {"20261111": "Veterans Day"}}
    got = js(*[("sweepsOn", ["W", 2026, 11, 11, skip, t]) for t, _ in DAYTIME_CASES])
    assert got == [not off for _, off in DAYTIME_CASES]


def test_sweep_days_skip_city_holidays():
    entries = {"even": [["W", "", "8AM-10AM"]], "odd": []}
    nov4, nov18 = datetime.date(2026, 11, 4), datetime.date(2026, 11, 18)
    days = schedule.sweep_days(entries, "oakland", nov4, nov18)
    assert [d for d, _ in days] == [nov4, nov18]
    days = schedule.sweep_days(entries, "berkeley", nov4, nov18)
    assert len(days) == 3


# ── Weekly trash days ────────────────────────────────────────────────────────


def _days(city, weekday, start, n=21):
    return schedule.weekly_pickups(city, weekday, start, n)


def test_berkeley_thanksgiving_week_shifts_from_the_holiday_on():
    mon = datetime.date(2026, 11, 23)  # Thanksgiving is Thursday the 26th
    assert _days("berkeley", 1, mon, 7) == [datetime.date(2026, 11, 24)]  # Tue: before, unchanged
    assert _days("berkeley", 3, mon, 7) == [datetime.date(2026, 11, 27)]  # Thu -> Fri
    assert _days("berkeley", 4, mon, 7) == [datetime.date(2026, 11, 28)]  # Fri -> Sat
    assert _days("berkeley", 4, datetime.date(2026, 11, 30), 7) == [datetime.date(2026, 12, 4)]


def test_a_shift_into_the_window_is_kept():
    # Friday Dec 25 pickup moves to Saturday Dec 26: in range when the window starts then.
    assert _days("berkeley", 4, datetime.date(2026, 12, 26), 3) == [datetime.date(2026, 12, 26)]


def test_chicago_moves_only_the_holiday_pickup():
    mon = datetime.date(2026, 11, 23)
    assert _days("chicago_all", 3, mon, 7) == [datetime.date(2026, 11, 27)]
    assert _days("chicago_all", 4, mon, 7) == [datetime.date(2026, 11, 27)]


def test_sf_weekly_has_no_holiday_rule():
    thanksgiving = datetime.date(2026, 11, 26)
    assert _days("san_francisco", 3, datetime.date(2026, 11, 23), 7) == [thanksgiving]


def test_trash_pickups_weekly_and_kinds():
    today = datetime.date(2026, 11, 23)
    assert trash.kind("oakland") == "recollect" and trash.kind("berkeley") == "weekly"
    assert trash.pickups("berkeley", "", today) == {}  # no day picked yet
    got = trash.pickups("berkeley", "", today, collection_day=3)
    dates = got[trash.WEEKLY_LABEL]
    assert dates[0] == "2026-11-27" and len(dates) == 8
