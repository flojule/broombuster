"""scripts/citykit.py: schedule words to sweep codes, posted windows, zones onto blocks."""

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
