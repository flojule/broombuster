"""scripts/citykit.py: schedule words to sweep codes, posted windows, zones onto blocks."""

import datetime
import sys
from pathlib import Path

import geopandas
import pytest
from shapely.geometry import LineString, box

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import citykit


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("1st & 3rd Tuesday", "T13"),
        ("Weekly (Mondays)", "ME"),
        ("Monday,1", "M1"),
        ("Weeks 2 & 4 Thursday", "TH24"),
        ("Wednesday 1/3", "W13"),
        ("Second and Fourth Thursday", "TH24"),
        ("A- Monday 1st & 3rd Week", "M13"),
        ("Monday & Thursday", "MTHE"),
        ("1st 2nd 3rd 4th 5th Fri", "FE"),
    ],
)
def test_schedule_words_to_codes(text, code):
    assert citykit.code_from_text(text)[0] == code


@pytest.mark.parametrize("text", ["every other Tuesday", "Bi-weekly Monday", "Weeks 1 & 3", ""])
def test_cadences_the_grammar_cannot_hold_are_none(text):
    assert citykit.code_from_text(text) is None


@pytest.mark.parametrize(
    ("raw", "out"),
    [("9-10:30 AM", "9AM–10:30AM"), ("9AM to NOON", "9AM–12PM"), ("8pm-5am", "8PM–5AM")],
)
def test_posted_windows(raw, out):
    assert citykit.time_text(raw) == out


def test_side_entries_courtesy_and_unknown():
    courtesy = citykit.side_entry("1st Tue", None, enforced=False)
    assert courtesy == ("NT", "1st Tue, no tickets", None)
    assert citykit.side_entry("see map", "8AM to 11AM")[0] == citykit.UNKNOWN
    assert citykit.side_entry(None) == (None, None, None)


def test_zones_to_blocks_takes_the_zone_holding_most_of_a_line():
    lines = geopandas.GeoDataFrame(
        {"STREET_NAME": ["A ST", "B ST"]},
        geometry=[
            LineString([(-122.300, 37.800), (-122.296, 37.800)]),
            LineString([(-122.310, 37.810), (-122.309, 37.810)]),
        ],
        crs="EPSG:4326",
    )
    zones = geopandas.GeoDataFrame(
        {"Day": ["Mon", "Tue"]},
        geometry=[box(-122.301, 37.799, -122.297, 37.801), box(-122.297, 37.799, -122.290, 37.801)],
        crs="EPSG:4326",
    )
    out = citykit.zones_to_blocks(lines, zones)
    assert out["STREET_NAME"].tolist() == ["A ST"] and out["Day"].tolist() == ["Mon"]


def test_month_dates_give_each_weekday_its_own_ordinals():
    days = citykit.month_dates("2nd Friday and 5th Monday", 2026, 11)
    assert days == [datetime.date(2026, 11, 13), datetime.date(2026, 11, 30)]
    assert citykit.month_dates("Second and Fourth Thursday", 2026, 2) == [
        datetime.date(2026, 2, 12),
        datetime.date(2026, 2, 26),
    ]


def test_cycle_dates_keep_the_cycle_across_years():
    days = citykit.cycle_dates(datetime.date(2022, 1, 10), 2, (2026,))
    assert days[:2] == [datetime.date(2026, 1, 5), datetime.date(2026, 1, 19)]
    assert len(days) == 26 and {d.year for d in days} == {2026}


def test_dates_code_moves_holidays_to_the_next_weekday():
    thanksgiving, xmas = datetime.date(2026, 11, 26), datetime.date(2026, 12, 25)
    code = citykit.dates_code([thanksgiving, xmas], {thanksgiving, xmas})
    assert code == "DATES:2026-11-27,2026-12-28"


def test_courtesy_entry_merges_lists_and_keeps_notes():
    got = citykit.courtesy_entry(
        ["Friday - Week 1", "Friday - Week 2", "Friday - Week 3", "Friday - Week 4",
         "Monday - Week 2", "Monday - Week 4", "Tuesday - Weekly",
         "1st Monday of the Month (partial)", "Every Other Week (Thursday)"]
    )  # fmt: skip
    assert got == (
        "NT",
        "2nd & 4th Mon; Every Tue, Fri; 1st Mon (partial); Every Other Week (Thursday), no tickets",
        None,
    )
    assert (
        citykit.courtesy_entry(["1st Tuesday"], note="Apr–Oct")[1] == "1st Tue, Apr–Oct, no tickets"
    )
    assert citykit.courtesy_entry([]) == (None, None, None)


def test_between_keeps_the_blocks_from_one_cross_street_to_another():
    pts = [(-122.50 + 0.001 * i, 37.9) for i in range(5)]
    main = [LineString([pts[i], pts[i + 1]]) for i in range(4)]
    cross = [LineString([(p[0], 37.899), p]) for p in (pts[1], pts[3])]
    lines = geopandas.GeoDataFrame(
        {"STREET_NAME": ["MAIN ST"] * 4 + ["ELMA ST", "CORNWALL ST"]},
        geometry=main + cross,
        crs="EPSG:4326",
    )
    lines["STREET_KEY"] = lines["STREET_NAME"].map(citykit.normalize.street_name)
    assert sorted(citykit.between(lines, "Main Ave", "Elma", "Cornwall")) == [1, 2]
    assert citykit.blocks_named(lines, "Main Ave").index.tolist() == [0, 1, 2, 3]  # no MAIN AVE
    assert citykit.between(lines, "Main", "Elma", "Nowhere").empty
