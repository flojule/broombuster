"""City holidays (data/holidays), the Python sweep-date rules the calendar feed uses
(schedule.py, checked against urgency.js), and weekly trash days."""

import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from broombuster import analysis, holidays, normalize, schedule, trash
from broombuster.cities import CITIES

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
    meta = holidays.source(city)
    assert meta["source"].startswith("https://") and meta["last_verified"]


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
    assert set(reg) == {"oakland", "san_francisco", "berkeley", "alameda"}


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
