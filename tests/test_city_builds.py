"""The city build scripts (scripts/build_*_geojson.py) and the Oakland normalizer, on
synthetic rows: how a published schedule row lands on a block and a side."""

import sys
from pathlib import Path

import geopandas
import pandas as pd
import pytest
from shapely.geometry import LineString

from broombuster import holidays, schedule, schemas

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
pytest.importorskip("pdfplumber")  # the `scripts` extra

import build_alameda_geojson as alameda
import build_berkeley_geojson as berkeley

_LINE = LineString([(0, 0), (1, 0)])

# ── Berkeley ─────────────────────────────────────────────────────────────────


def test_berkeley_rows_read_thurs_both_sides_and_pdf_slips():
    rows = berkeley.rows_from(
        [
            "51 Acton W 1330 1599 1st Thurs PM Hopkins Keoncrest 1500",
            "32 Bonita E/W 1900 1923 2nd Tue PM Hearst Berkeley",
            "64 Etna W 2500 2698 4thFri AM Dwight Derby",
            "34 Dwight N 2801 4th Tue PM Piedmont Fernwald",
            "Rte Street Name Side From To Day of AM From To Opt-Out",
        ]
    )
    assert [r["entry"][0] for r in rows] == ["TH1", "T2", "F4", "T4"]
    assert rows[0]["entry"][2] == "12:30PM–3:30PM" and rows[2]["entry"][2] == "9AM–12PM"
    assert rows[1]["parities"] == {0, 1} and rows[0]["parities"] == {0}
    assert (rows[3]["lo"], rows[3]["hi"]) == (2801, 2801)


def _centerline(name, kind, left, right, sweep="enforced"):
    return {
        "STR_NAM": name,
        "STR_TYP": kind,
        "F_ADDL": left[0],
        "T_ADDL": left[1],
        "F_ADDR": right[0],
        "T_ADDR": right[1],
        "mech_sweep": sweep,
        "geometry": _LINE,
    }


def test_berkeley_rows_land_on_their_block_and_side():
    lines = geopandas.GeoDataFrame(
        [
            _centerline("WEST", "ST", (2301, 2399), (2300, 2398)),
            _centerline("WEST", "ST", (1701, 1749), (1700, 1748)),  # no PDF row
            _centerline("ACTON", "CR", (1401, 1499), (1400, 1498)),
            _centerline("ACTON", "ST", (1401, 1499), (1400, 1498)),
            _centerline("MC GEE", "AV", (1301, 1399), (1300, 1398), sweep="exempt"),
        ],
        crs="EPSG:4326",
    )
    rows = berkeley.rows_from(
        [
            "61 West W 2300 2498 1st Fri AM Bancroft Dwight",
            "22 West E 2301 2499 2nd Mon AM Bancroft Dwight",
            "61 Acton Crescent S 1400 1498 1st Fri AM Acton Terminus",
            "22 Acton W 1330 1599 2nd Mon PM Gilman Hopkins",
            "52 McGee W 1300 1398 2nd Thurs PM Hopkins Rose",
        ]
    )
    out = berkeley.join(rows, lines).set_index(["STREET_NAME", "R_F_ADD"])
    west = out.loc[("WEST ST", 2300)]
    assert (west.DAY_EVEN, west.DAY_ODD) == ("F1", "M2")  # the sign read on the street
    assert out.loc[("WEST ST", 1700)].DAY_EVEN == "POSTED"  # enforced, schedule unknown
    assert out.loc[("ACTON CRES", 1400)].DAY_EVEN == "F1"
    assert out.loc[("ACTON ST", 1400)].DAY_EVEN == "M2"
    assert out.loc[("MC GEE AVE", 1300)].DAY_EVEN == "NS"  # opted out: not swept


def test_berkeley_side_letter_beats_slipped_numbers_and_renumbered_streets_still_land():
    # Drawn west to east: the left side faces north.
    lines = geopandas.GeoDataFrame(
        [
            _centerline("HOPKINS", "ST", (1101, 1199), (1100, 1198)),
            _centerline("EUNICE", "ST", (1101, 1199), (1100, 1198), sweep="industry"),
            _centerline("ETON", "CT", (1, 25), (2, 24)),
        ],
        crs="EPSG:4326",
    )
    rows = berkeley.rows_from(
        [
            "22 Hopkins S 1100 1498 2nd Mon PM Cedar Sacramento",
            "61 Hopkins N 1100 1498 1st Fri PM Sacramento San Pablo",  # N side, even numbers
            "43 Eunice S 1100 1198 3rd Wed AM Sutter Terminus",
            "63 Eton Ct W 3200 3298 3rd Fri AM Claremont Cul De Sac",
        ]
    )
    out = berkeley.join(rows, lines).set_index("STREET_NAME")
    assert (out.loc["HOPKINS ST"].DAY_EVEN, out.loc["HOPKINS ST"].DAY_ODD) == ("M2", "F1")
    eunice = out.loc["EUNICE ST"]  # not enforced: swept, no tickets
    assert (eunice.DAY_EVEN, eunice.DESC_EVEN) == ("NT", "3rd Wed, 9AM–12PM, no tickets")
    assert out.loc["ETON CT"].DAY_EVEN == "F3"  # the city's centerline numbers it 1-25


# ── Alameda ──────────────────────────────────────────────────────────────────


def test_alameda_only_signed_sides_are_enforced():
    assert alameda.side_entry("Thursday", 1, "10:00 AM - 1:00 PM") == (
        "THE",
        "Every Thu",
        "10AM–1PM",
    )
    assert alameda.side_entry("All", 1, "4:00 AM - 5:30 AM") == ("E", "Every day", "4AM–5:30AM")
    assert alameda.side_entry("Monday", 1, "")[2] is None  # signed, hours not on file
    assert alameda.side_entry("Monday", 0, "8:00 AM - 11:00 AM") == (
        "NT",
        "Every Mon, 8AM–11AM, no tickets",
        None,
    )
    assert alameda.side_entry("None", 0, "N/A") == (None, None, None)


def test_alameda_block_takes_the_centerline_of_its_street_and_range():
    sched = pd.DataFrame(
        [
            {
                "FullStreetName": "TAYLOR AV",
                "FrAddressLeft": 401,
                "ToAddressLeft": 499,
                "FrAddressRight": 400,
                "ToAddressRight": 498,
                "SweepEven": "Thursday",
                "SignEven": 1,
                "PostedTime_Even": "10:00 AM - 1:00 PM",
                "SweepOdd": "Monday",
                "SignOdd": 1,
                "PostedTime_Odd": "8:00 AM - 11:00 AM",
            },
            {  # the county splits this block and digitises it the other way round
                "FullStreetName": "KITTYHAWK RD",
                "FrAddressLeft": 300,
                "ToAddressLeft": 498,
                "FrAddressRight": 301,
                "ToAddressRight": 499,
                "SweepEven": "Friday",
                "SignEven": 1,
                "PostedTime_Even": "8:00 AM - 11:00 AM",
                "SweepOdd": "None",
                "SignOdd": 0,
                "PostedTime_Odd": None,
            },
        ]
    )
    far = LineString([(5, 5), (6, 5)])

    def county(street, left, right, geom):
        keys = ("FRADDL", "TOADDL", "FRADDR", "TOADDR")
        return {"STREET": street, **dict(zip(keys, (*left, *right), strict=True)), "geometry": geom}

    lines = geopandas.GeoDataFrame(
        [
            county("TAYLOR AV", ("401", "499"), ("400", "498"), _LINE),
            county("TAYLOR AV", ("501", "599"), ("500", "598"), far),
            county("KITTY HAWK RD", ("301", "399"), ("300", "398"), far),
        ],
        crs="EPSG:4326",
    )
    out = alameda.join(sched, lines).set_index("STREET_NAME")
    taylor = out.loc["TAYLOR AVE"]
    assert taylor.geometry.equals(_LINE)
    assert (taylor.DAY_EVEN, taylor.TIME_EVEN, taylor.DAY_ODD) == ("THE", "10AM–1PM", "ME")
    kitty = out.loc["KITTYHAWK RD"]
    assert kitty.geometry.equals(far) and kitty.DAY_EVEN == "FE"
    assert (kitty.L_F_ADD, kitty.R_F_ADD) == (301, 300)  # the centerline's own left / right


def test_alameda_renamed_and_unranged_blocks_find_their_centerline():
    def block(street, rng, even, odd, signed=1):
        keys = ("FrAddressLeft", "ToAddressLeft", "FrAddressRight", "ToAddressRight")
        return {"FullStreetName": street, **dict(zip(keys, rng, strict=True)),
                "SweepEven": even, "SignEven": signed, "PostedTime_Even": "",
                "SweepOdd": odd, "SignOdd": signed, "PostedTime_Odd": ""}  # fmt: skip

    sched = pd.DataFrame(
        [
            block("CONSTITUTION WY", (1601, 1699, 1600, 1698), "Thursday", "Thursday"),
            block("ENCINAL AV", (None,) * 4, "Wednesday", "Thursday"),
            block("ENCINAL AV", (None,) * 4, "Friday", "Thursday"),
            block("AUGHINBAUGH WY", (1100, 1198, 1101, 1199), "Tuesday", "Tuesday", signed=0),
        ]
    )
    short = LineString([(0, 0), (0.001, 0)])
    keys = ("FRADDL", "TOADDL", "FRADDR", "TOADDR")
    lines = geopandas.GeoDataFrame(
        [
            {"STREET": "WILMA CHAN WY", **dict(zip(keys, ("1601", "1699", "1600", "1698"),
                                                   strict=True)), "geometry": _LINE},
            {"STREET": "ENCINAL AV", **dict.fromkeys(keys), "geometry": short},
            {"STREET": "AUGHINBAUGH WY", **dict(zip(keys, ("1000", "1098", "1001", "1099"),
                                                    strict=True)), "geometry": _LINE},
        ],
        crs="EPSG:4326",
    )  # fmt: skip
    out = alameda.join(sched, lines).set_index("STREET_NAME")
    assert out.loc["WILMA CHAN WAY"].DAY_EVEN == "THE"
    encinal = out.loc["ENCINAL AVE"]  # two unranged blocks, told apart by nothing
    assert encinal.geometry.equals(short)
    assert (encinal.DAY_EVEN, encinal.DAY_ODD) == ("POSTED", "THE")
    assert "AUGHINBAUGH WAY" not in out.index  # no county block 1100: dropped, unsigned


# ── Oakland ──────────────────────────────────────────────────────────────────


def _oakland(**row):
    base = {"PREFIX": " ", "NAME": "Grand", "TYPE": "Ave", "SUFFIX": " ", "L_F_ADD": "401",
            "L_T_ADD": "499", "R_F_ADD": " ", "R_T_ADD": " ", "DAY_EVEN": "M24",
            "TIME_EVEN": "M3", "DAY_ODD": "NS", "TIME_ODD": "NA"}  # fmt: skip
    gdf = geopandas.GeoDataFrame([{**base, **row}], geometry=[_LINE], crs="EPSG:4326")
    return schemas._normalize_oakland(gdf).iloc[0]


def test_oakland_layer_codes_become_grammar_codes_and_windows():
    r = _oakland()
    assert (r.DAY_EVEN, r.DESC_EVEN, r.TIME_EVEN) == ("M24", "2nd and 4th Mon", "9AM–12PM")
    assert r.DAY_ODD == "NS" and pd.isna(r.TIME_ODD)
    assert r.L_F_ADD == 401 and pd.isna(r.R_F_ADD)
    for raw, code, desc in (
        ("FW", "W1", "1st Wed"),
        ("EEH", "E", "Everyday except Holidays"),
        ("EESSH", "MTWTHF", "Everyday except Saturday, Sunday & Holidays"),
        ("MF", "MTWTHF", "Every Mon to Fri"),
        ("TH1", "TH1", "1st Thurs"),
        ("missing", "POSTED", "Schedule not published: check the posted sign"),
    ):
        r = _oakland(DAY_EVEN=raw, TIME_EVEN="M2")
        assert (code, desc) == (r.DAY_EVEN, r.DESC_EVEN), raw
        assert schedule._rule(r.DAY_EVEN) is not None or raw == "missing"
    assert _oakland(DAY_EVEN="MWF", TIME_EVEN="M1").TIME_EVEN == "12AM–3AM"


def test_oakland_street_name_keeps_its_direction():
    assert _oakland(PREFIX="E", NAME="12th", TYPE="St").STREET_DISPLAY == "E 12th St"
    assert _oakland(NAME="Home", TYPE="Pl", SUFFIX="W").STREET_NAME == "HOME PL W"


# ── San Francisco: daytime-only holidays ─────────────────────────────────────


def test_sf_daytime_holiday_keeps_night_cleaning():
    import datetime

    vets, xmas = datetime.date(2026, 11, 11), datetime.date(2026, 12, 25)
    day, night = schedule.time_window("8AM–10AM"), schedule.time_window("2AM–6AM")
    assert holidays.sweep_suspended("san_francisco", vets, day) == "Veterans Day"
    assert holidays.sweep_suspended("san_francisco", vets, night) is None
    assert holidays.sweep_suspended("san_francisco", vets, None) is None  # untimed: assume on
    assert holidays.sweep_suspended("san_francisco", xmas, night) == "Christmas Day"
    entries = {"even": [["W", "", "8AM–10AM"], ["W", "", "2AM–6AM"]], "odd": []}
    (_, items), = schedule.sweep_days(entries, "san_francisco", vets, vets)  # fmt: skip
    assert items == [("even", "2AM–6AM")]
