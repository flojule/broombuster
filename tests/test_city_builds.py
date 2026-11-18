"""The city build scripts (scripts/build_*_geojson.py) and the Oakland normalizer, on
synthetic rows: how a published schedule row lands on a block and a side."""

import datetime
import sys
from pathlib import Path

import geopandas
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, box

from broombuster import holidays, normalize, schedule, schemas

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
pytest.importorskip("pdfplumber")  # the `scripts` extra

import build_alameda_geojson as alameda
import build_benicia_geojson as benicia
import build_berkeley_geojson as berkeley
import build_fairfield_geojson as fairfield
import build_napa_geojson as napa
import build_oakley_geojson as oakley
import build_pittsburg_geojson as pittsburg
import build_sonoma_county_geojson as sonoma_county
import build_vacaville_geojson as vacaville

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
    assert pd.isna(out.loc[("WEST ST", 1700)].DAY_EVEN)  # enforced, schedule unknown
    assert out.loc[("ACTON CRES", 1400)].DAY_EVEN == "F1"
    assert out.loc[("ACTON ST", 1400)].DAY_EVEN == "M2"
    assert out.loc[("MC GEE AVE", 1300)].DAY_EVEN == "NS"  # opted out: nobody ticketed


# ── Alameda ──────────────────────────────────────────────────────────────────


def test_alameda_only_signed_sides_are_enforced():
    assert alameda.side_entry("Thursday", 1, "10:00 AM - 1:00 PM") == (
        "THE",
        "Every Thu",
        "10AM–1PM",
    )
    assert alameda.side_entry("All", 1, "4:00 AM - 5:30 AM") == ("E", "Every day", "4AM–5:30AM")
    assert alameda.side_entry("Monday", 1, "")[2] is None  # signed, hours not on file
    assert alameda.side_entry("Monday", 0, "8:00 AM - 11:00 AM")[0] == "NS"
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


# ── Contra Costa (TIGER/Line centerlines) ────────────────────────────────────


def _street(name, y, ranges=(None, None, None, None), x=-121.9):
    """A centerline row (citykit.centerlines columns) running east 90 m at latitude y."""
    keys = ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")
    return {"STREET_NAME": name, "STREET_KEY": normalize.street_name(name),
            **dict(zip(keys, ranges, strict=True)),
            "geometry": LineString([(x, y), (x + 0.001, y)])}  # fmt: skip


def _gdf(rows):
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def test_pittsburg_commercial_route_wins_and_unenforced_zones_are_nt():
    lines = _gdf([
        _street("RAILROAD AVE", 38.0, (100, 198, 101, 199)),
        _street("ELM ST", 38.002, (198, 100, 199, 101)),  # TIGER: high to low
        _street("OAK ST", 38.002, x=-121.895),
    ])  # fmt: skip
    zone = {"SweepingTime": "6:00 am - 4:00 pm", "geometry": None}
    residential = _gdf([
        {**zone, "Schedule": "1st and 3rd Thrusday", "Code_Enforcement": "No",
         "geometry": box(-121.9005, 37.9995, -121.8985, 38.0025)},
        {**zone, "Schedule": "2nd and 4th Monday", "Code_Enforcement": "Yes",
         "geometry": box(-121.8955, 37.9995, -121.8935, 38.0025)},
    ])  # fmt: skip
    commercial = _gdf(
        [
            {
                "Schedule": "A - Every Tuesday & Thursday",
                "geometry": _street("", 38.00001)["geometry"],
            }
        ]
    )
    out = pittsburg.build(residential, commercial, lines).set_index("STREET_NAME")
    assert out.loc["RAILROAD AVE", "DAY_EVEN"] == "TTHE" and pd.isna(
        out.loc["RAILROAD AVE", "TIME_ODD"]
    )
    elm = out.loc["ELM ST"]
    assert (elm.DAY_EVEN, elm.DESC_ODD) == ("NT", "1st & 3rd Thu, 6AM–4PM, no tickets")
    assert (elm.L_F_ADD, elm.L_T_ADD) == (100, 198)
    assert tuple(out.loc["OAK ST", ["DAY_ODD", "TIME_EVEN"]]) == ("M24", "6AM–4PM")


def test_oakley_list_rows_and_streets_it_gives_several_schedules():
    by_name = oakley.schedules([
        "STREET ROUTE SWEEP DAY COD FROM TO",
        "Acorn Dr 4 2nd & 4th Monday of each month R Solitude Dr Harvest Cir",
        "Main St Art 1st & 3rd Friday of each month A Vintage Parkway Empire Ave",
        "Carol Ln 1 1st & 3rd Tuesday of each month R Main St Oakley Rd",
        "Carol Ln 2 1st & 3rd Thursday of each month R Oakley Rd End",
        "Mailcoat Ave 2 1st & 3rd Thursday of each month R Main St End",
        "Lot",
    ])  # fmt: skip
    assert by_name["MAIN"] == {"1st & 3rd Friday"} and len(by_name["CAROL"]) == 2
    streets = [_street(n, 38.0 + 0.002 * i) for i, n in
               enumerate(("ACORN DR", "MALICOAT AVE", "CAROL LN", "SLOAN WAY"))]  # fmt: skip
    routes = _gdf([
        {"geometry": MultiLineString([s["geometry"] for s in streets[:3]])},
        {"geometry": streets[3]["geometry"]},
    ])  # fmt: skip
    out = oakley.build(by_name, routes, _gdf(streets)).set_index("STREET_NAME")["DAY_EVEN"]
    assert out["ACORN DR"] == "M24" and out["MALICOAT AVE"] == "TH13"  # spelled MAILCOAT
    assert out["CAROL LN"] == "TH13"  # the one its route's other streets have
    assert out["SLOAN WAY"] == "POSTED"  # not listed, nothing to go by


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
        ("missing", "MISSING", None),
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


# ── North Bay: rotations and seasonal schedules as DATES codes ───────────────


def _dates(code, month=None):
    days = [datetime.date.fromisoformat(d) for d in code.removeprefix("DATES:").split(",")]
    return [d for d in days if month is None or (d.year, d.month) == month]


def test_vacaville_weeks_1_and_3_are_every_other_week():
    code, desc = vacaville.entry("Monday", "Weeks 1 & 3", (2026,))
    assert _dates(code)[:2] == [datetime.date(2026, 1, 5), datetime.date(2026, 1, 19)]
    assert datetime.date(2026, 3, 30) in _dates(code)  # a 5th Monday: not a week of the month
    assert desc == "Every other Mon, green weeks"
    assert _dates(vacaville.entry("Friday", "Weeks 2 & 4", (2026,))[0])[0].day == 2
    assert vacaville.entry("Wednesday", "Weekly", (2026,))[0] == "WE"
    with pytest.raises(ValueError):
        vacaville.entry("Monday", "Biweekly", (2026,))


def test_sonoma_county_slots_repeat_every_4_weeks():
    code, desc = sonoma_county.entry("Tuesday", "1", (2026,))
    assert _dates(code)[:2] == [datetime.date(2026, 1, 13), datetime.date(2026, 2, 10)]
    assert desc == "Tue every 4 weeks"
    assert sonoma_county.entry("Thursday", "1234", (2026,))[0] == "THE"


def test_fairfield_fall_months_take_the_published_exceptions():
    zone = {
        "OBJECTID": 1,
        "Regular_Schedule": "4th Friday",
        "Base_Fall_Schedule": "2nd and 4th Friday",
        "FallSweep_Nov": "2nd Friday and 5th Monday",
        "FallSweep_Dec": "2nd Friday and 4th Monday",
        "FallSweep_Jan": None,
    }
    off = {datetime.date(2026, 11, 26), datetime.date(2026, 12, 25)}
    code, desc = fairfield.residential_entry(zone, (2026, 2027), off)
    assert [d.day for d in _dates(code, (2026, 11))] == [13, 30]
    assert [d.day for d in _dates(code, (2026, 12))] == [11, 28]
    assert [d.day for d in _dates(code, (2027, 1))] == [8, 22]  # no exception: base fall
    assert [d.day for d in _dates(code, (2026, 2))] == [27]
    assert desc == "4th Fri; Nov–Jan 2nd & 4th Fri"


def test_napa_leaf_season_doubles_and_holidays_move():
    route = {
        "RouteName": "Th4",
        "StandardRt": "Fourth Thursday",
        "Leaf_Rte": "Second and Fourth Thursday",
    }
    code, _ = napa.entry(route, (2026,), {datetime.date(2026, 11, 26)})
    assert [d.day for d in _dates(code, (2026, 9))] == [24]
    assert [d.day for d in _dates(code, (2026, 11))] == [12, 27]  # Thanksgiving -> Friday
    assert napa.entry({"RouteName": "MF"}, (2026,), set())[0] == "MFE"


def test_benicia_brochure_overrides_the_city_layer():
    assert benicia.entry("Third Tuesday") == ("TH24", "2nd & 4th Thu", None)
    assert benicia.entry("Every Monday and Friday Morning") == ("FE", "Every Fri", "AM")
    assert benicia.entry("Every Thursday Night")[:2] == ("THE", "Every Thu night")
    assert benicia.entry("First Wednesday")[0] == "W1"
