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
import build_albany_geojson as albany
import build_benicia_geojson as benicia
import build_berkeley_geojson as berkeley
import build_emeryville_geojson as emeryville
import build_fairfield_geojson as fairfield
import build_hayward_geojson as hayward
import build_milpitas_geojson as milpitas
import build_napa_geojson as napa
import build_oakley_geojson as oakley
import build_pittsburg_geojson as pittsburg
import build_san_jose_geojson as san_jose
import build_san_leandro_geojson as san_leandro
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


# ── San Jose / Milpitas ──────────────────────────────────────────────────────


def _sj(**row):
    base = {"DAYSWEPT": "Tuesday", "WEEKSWEPT": "Odd", "ROUTETYPE": "Signed Route",
            "POSTEDTIME": "", "SIDEOFROAD": "Both"}  # fmt: skip
    return pd.Series({**base, **row})


def test_san_jose_signs_give_days_and_hours():
    res = _sj(WEEKSWEPT=2, POSTEDTIME="7AM to NOON  12:30PM to 5PM")
    assert san_jose.residential_entry(res) == ("T2", "2nd Tue", "7AM–5PM")
    assert san_jose.span("8AM to NOON  1PM to 5PM  9AM to NOON 1PM to 4PM") == "8AM–5PM"
    assert san_jose.residential_entry(_sj(WEEKSWEPT=2, ROUTETYPE="Non-Signed Route"))[0] == "NT"
    assert san_jose.major_entry(_sj(POSTEDTIME="No Parking Tuesday 1am-6am")) == (
        "TE", "Every Tue", "1AM–6AM")  # fmt: skip
    assert san_jose.major_entry(_sj(POSTEDTIME="1am to 6am"))[:2] == ("T13", "1st & 3rd Tue")
    assert san_jose.major_entry(_sj(POSTEDTIME="No Parking 3am-6am Any Day"))[0] == "E"
    stop = san_jose.major_entry(_sj(POSTEDTIME="No Stopping Anytime"))
    assert stop[0] == "NT" and "No Stopping Anytime" in stop[1]


def test_san_jose_compass_side_lands_on_its_parity():
    east_west = LineString([(0, 0), (1, 0)])  # drawn eastward: left is north
    assert san_jose.compass_side(east_west, "North") == "L"
    assert san_jose.compass_side(east_west, "South") == "R"
    rec = {"L_F_ADD": 101.0, "L_T_ADD": 199.0, "R_F_ADD": None, "R_T_ADD": None}
    assert san_jose._parity(rec, "L") == "ODD" and san_jose._parity(rec, "R") == "EVEN"


def test_milpitas_route_labels_and_boundary_streets():
    routes = pd.DataFrame({"ResSweepDay": ["B- Tuesday 1st & 3rd Week",
                                           "D- Thursday 1st & 3rd Week",
                                           "H- Wednsday 2nd & 4th Week",
                                           "B-Tuesday Left D-Tuesday Right"]})  # fmt: skip
    sched = milpitas.route_schedules(routes)
    assert sched == {"B": "Tuesday 1st & 3rd", "D": "Thursday 1st & 3rd",
                     "H": "Wednesday 2nd & 4th"}  # fmt: skip


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


# ── Marin: Mill Valley Refuse lists, Corte Madera DPW PDF, Sausalito table ───


def test_mvrs_lists_read_town_tabs_headings_and_items():
    import mvrs

    page = (
        '<div class="elementor-tab-title elementor-tab-mobile-title" data-tab="1">Tiburon</div>'
        '<span class="data-table-header-text">1st Tuesday of the Month</span>'
        '<li>Andrew Dr.</li><li class="li_col2">Ned&#8217;s Way</li>'
        '<span class="data-table-header-text">1st Friday the Month</span>'
        "<li>Via Paraiso E. &amp; W.</li><li>Hilary Dr. (Avenida Miraflores to Howard)</li>"
    )
    got = mvrs.lists(page)["Tiburon"]
    assert got[0] == ("Andrew Dr.", "1st Tuesday of the Month") and len(got) == 4
    assert mvrs.street_parts(got[1][0]) == (["NEDS WAY"], "")
    assert mvrs.street_parts(got[2][0]) == (["VIA PARAISO E", "VIA PARAISO W"], "")
    assert mvrs.street_parts(got[3][0]) == (["HILARY DR"], "Avenida Miraflores to Howard")


def test_corte_madera_dpw_rejoins_wrapped_limits():
    import build_corte_madera_geojson as cm

    rows = cm.dpw_rows(
        [
            "Town of Corte Department of Public Works Fall and Winter",
            "Streets Swept on the 3rd Monday of the Month",
            " Walnut Ave.",
            " Willow Ave. (from Tamalpais",
            " Fifer Ave.  Palm Ave.",
            "Ave. to Baltimore Ave.)",
        ]
    )
    assert [r[0] for r in rows] == [
        "Walnut Ave.",
        "Willow Ave. (from Tamalpais Ave. to Baltimore Ave.)",
        "Fifer Ave.",
        "Palm Ave.",
    ]
    assert rows[0][1] == "3rd Monday of the Month (Town crew, fall and winter)"


def test_sausalito_rows_split_streets_and_limits():
    import build_sausalito_geojson as sausalito

    page = "".join(
        f"<tr><td>{a}</td><td>{b}</td></tr>"
        for a, b in [
            ("Name of Street", "Sweeping Schedule"),
            ("Cazneau - Marie to Platt", "Monday - Week 1"),
            ("Alexander and South", "Tuesday - Weekly"),
            ("Buchanan Dr. and Ct.", "Wednesday - Week 4"),
            ("Fourth and Third (200 and 300 Blocks)", "Wednesday - Week 3"),
            ("Sweep Parking Lots", "Thursday - Week 2"),
            ("Kendall Ct.", "Wednesday - Week 1"),
        ]
    )
    assert sausalito.rows(page) == [
        ("Cazneau", "Marie to Platt", "Monday - Week 1"),
        ("Alexander", "", "Tuesday - Weekly"),
        ("South", "", "Tuesday - Weekly"),
        ("Buchanan Dr", "", "Wednesday - Week 4"),
        ("Buchanan Ct", "", "Wednesday - Week 4"),
        ("Fourth", "200 and 300 Blocks", "Wednesday - Week 3"),
        ("Third", "200 and 300 Blocks", "Wednesday - Week 3"),
        ("Kendell Ct", "", "Wednesday - Week 1"),
    ]


def test_san_rafael_zones_courtesy_but_the_signed_canal_zone():
    import build_san_rafael_geojson as sr

    assert sr.zone_entry("Canal Residential", "Weekly (Mondays)") == ("ME", "Every Mon", "8AM–10AM")
    assert sr.zone_entry("29", "1st Tuesday")[1] == "1st Tue, Apr–Oct 2026, no tickets"
    assert sr.zone_entry("PSPD", "Fridays 4/10, 4/17")[1] == "Fridays 4/10, 4/17 2026, no tickets"


# ── San Mateo County cities ──────────────────────────────────────────────────

import build_daly_city_geojson as daly_city  # noqa: E402
import build_san_bruno_geojson as san_bruno  # noqa: E402
import build_san_mateo_county_geojson as smc  # noqa: E402
import build_south_san_francisco_geojson as ssf  # noqa: E402
import citykit  # noqa: E402


def test_daly_city_page_rows_split_parts_and_fix_hours():
    page = (
        "<tr><td><p>Bellevue Avenue</p></td>"
        "<td><ul><li>South Side - Wednesday<br>Between 6 <abbr>am</abbr> and 8 am</li>"
        "<li>South Side - Wednesday<br>Between 11 am and 1 p m</li></ul></td>"
        "<td><ul><li>North Side - Tuesday<br>Between Noon and 2 pm</li>"
        "<li>North Side - Tuesday<br>Between 9:30 am and 11:</li></ul></td>"
        "<td><ul><li>Peoria Street to Pope Street</li>"
        "<li>Pope Street to South Hill</li></ul></td></tr>"
        "<tr><td><p>Amhurst Court</p></td><td><p>Both Sides - Wednesday Between 11 pm and 1 pm</p>"
        "</td><td><p>City of Colma</p></td><td><p>N/A</p></td></tr>"
    )
    rows = daly_city.rows_from(page)
    assert [(r["name"], r["part"]) for r in rows] == [
        ("BELLEVUE AVENUE", "Peoria Street to Pope Street"),
        ("BELLEVUE AVENUE", "Pope Street to South Hill"),
        ("AMHURST COURT", ""),
    ]
    assert rows[0]["ODD"] == ("WE", "Every Wed", "6AM–8AM")
    assert rows[0]["EVEN"] == ("TE", "Every Tue", "12PM–2PM")
    assert rows[1]["ODD"][2] == "11AM–1PM" and rows[1]["EVEN"][2] == "9:30AM–11AM"
    assert rows[2]["ODD"][2] == "11AM–1PM" and rows[2]["EVEN"] == (None, None, None)
    assert daly_city.street("90 th (Ninetieth) Street") == "90TH STREET"


def test_daly_city_page_cell_variants_and_unreadable_side():
    page = (
        "<tr><td>Oliver Street</td><td>North Side 0 Wednesday Between 8 and 10 am</td>"
        "<td>West Side - Wednesday Between 11 am an 1 pm</td><td>N/A</td></tr>"
        "<tr><td>Higate Drive</td><td>East side - Thursday<br>Between 12 pm and 2 pm<br><br>"
        "North side - Monday Through Wednesday<br>Between 7 am - 9 am</td>"
        "<td>South Side - Friday Bet 6 AM &amp; 7:30 AM<br><br>"
        "South Side Tue and Thurs Between 5 am and 6 am</td>"
        "<td><ul><li>Edgemont to South end</li><li>Christopher to St. Francis</li></ul></td></tr>"
        "<tr><td><br></td><td><ul><li>East Side -Thursday</li><li>Between 7 am and 9 am</li>"
        "</ul></td><td>We don&#39;t sweep</td><td>Sylvan to Abbot</td></tr>"
    )
    rows = daly_city.rows_from(page)
    assert [(r["ODD"][0], r["ODD"][2], r["EVEN"][0], r["EVEN"][2]) for r in rows] == [
        ("WE", "8AM–10AM", "WE", "11AM–1PM"),
        ("THE", "12PM–2PM", "FE", "6AM–7:30AM"),
        ("MTWE", "7AM–9AM", "TTHE", "5AM–6AM"),
        ("THE", "7AM–9AM", None, None),
    ]
    assert rows[3]["name"] == "HIGATE DRIVE"  # a blank name cell continues the street above
    with pytest.raises(ValueError, match="1 listed sides"):
        daly_city.rows_from(page.replace(" Between 5 am and 6 am", ""))


def test_san_bruno_routes_posted_or_courtesy_and_two_cycles():
    assert san_bruno.entry("1st & 3rd Tuesday", "9 a.m. - 11 a.m.", None) == (
        "T13", "1st & 3rd Tue", "9AM–11AM")  # fmt: skip
    assert san_bruno.entry("2nd & 4th Monday", "No specified time", "curbs")[0] == "NT"
    assert san_bruno.entry("Monday - Thursday", "6 a.m. - 7 a.m.", None)[0] == "MTWTHE"
    assert san_bruno.entry("3rd Thursday", "9 a.m. - 11 a.m.", "Eastside 3rd Friday ONLY")[0] == (
        "THF3")  # fmt: skip
    code, desc, _ = san_bruno.entry("1st & 3rd Monday / 2nd & 4th Thursday", "9 - 11 a.m.", None)
    days = code.removeprefix("DATES:").split(",")
    assert "2026-11-02" in days and "2026-11-12" in days and "2026-11-09" not in days
    assert desc == "1st & 3rd Mon and 2nd & 4th Thu"


def test_ssf_signs_win_over_the_unposted_sweep():
    row = {"NoParkDay": "Monday-Tuesday", "NoParkTime": "8am to 11am", "NewDays": "", "DAY": ""}
    assert ssf.entry(row) == ("POSTED", "Mon on one side, Tue on the other", "8AM–11AM")
    row = {"NoParkDay": "Everyday", "NoParkTime": "3am to 5am", "NewDays": "", "DAY": ""}
    assert ssf.entry(row) == ("E", "Every day", "3AM–5AM")
    row = {"NoParkDay": "", "NoParkTime": "", "NewDays": "Wednesday 1/3", "DAY": "Wednesday"}
    assert ssf.entry(row)[:2] == ("NT", "1st & 3rd Wed, no tickets")


def test_smc_hours_mean_ordinance_schedule():
    assert smc.entry("2nd & 4th Thursday of the month 1pm - 3.30pm") == (
        "TH24", "2nd & 4th Thu", "1PM–3:30PM")  # fmt: skip
    assert smc.entry("1st & 3rd Thursday of the month")[0] == "NT"


def test_address_points_land_on_their_block_side():
    lines = geopandas.GeoDataFrame(
        {
            "STREET_NAME": ["ELM ST", "ELM ST"],
            "STREET_KEY": ["ELM", "ELM"],
            "L_F_ADD": [101, 201],
            "L_T_ADD": [199, 299],
            "R_F_ADD": [100, 200],
            "R_T_ADD": [198, 298],
        },
        geometry=[_LINE, LineString([(1, 0), (2, 0)])],
        crs="EPSG:4326",
    )
    pts = pd.DataFrame({"STREET_KEY": "ELM", "NUM": ["110", "112", "114", "131", "250"],
                        "SCHEDULE": ["Mon", "Mon", "Tue", "Fri", "Wed"]})  # fmt: skip
    out = citykit.address_blocks(lines, pts, lambda s: citykit.side_entry(s))
    assert list(out["DAY_EVEN"]) == ["ME", "WE"] and list(out["DAY_ODD"]) == ["FE", None]


# ── Alameda County cities ────────────────────────────────────────────────────

_EAST = LineString([(-122.290, 37.835), (-122.289, 37.835)])  # 88 m heading east


def _shifted(line, dlat):
    return LineString([(x, y + dlat) for x, y in line.coords])


def _block(geom, left=(1001, 1099), right=(1000, 1098), name="45TH ST"):
    keys = ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")
    return {"STREET_NAME": name, **dict(zip(keys, (*left, *right), strict=True)), "geometry": geom}


def test_emeryville_curb_lines_give_their_side_and_centerlines_both():
    north = _shifted(_EAST, 0.000045)  # about 5 m north: the left side heading east
    other = _shifted(_EAST, 0.001)
    cross = LineString([(-122.289, 37.834), (-122.289, 37.836)])
    rows = [
        ("1st", "Thursday", 2, north),
        ("1st or 2nd", "Thursday", 2, _EAST),
        ("2nd", "Thursday", 2, _shifted(_EAST, -0.000045)),
        ("1st or 2nd", "Thursday", 2, other),
        ("1st & 3rd", "Monday", 1, cross),
    ]
    cols = ["DayOfMonth", "DayOfWeek", "TimeOfDay", "geometry"]
    routes = geopandas.GeoDataFrame(pd.DataFrame(rows, columns=cols), crs="EPSG:4326")
    lines = geopandas.GeoDataFrame(
        [_block(_EAST), _block(other, (1101, 1199), (1100, 1198))], crs="EPSG:4326"
    )
    out = emeryville.join(routes, lines).set_index("L_F_ADD")
    assert (out.loc[1001].DAY_ODD, out.loc[1001].DAY_EVEN) == ("TH1", "TH2")  # north = left = odd
    assert out.loc[1001].TIME_ODD == "8AM–12PM"
    assert (out.loc[1101].DAY_ODD, out.loc[1101].DAY_EVEN) == ("TH12", "TH12")  # side not drawn


def test_san_leandro_commercial_beats_posted_beats_zone():
    assert (
        san_leandro.schedule_words("Monday - Friday") == "Monday Tuesday Wednesday Thursday Friday"
    )
    a, b, c = _EAST, _shifted(_EAST, 0.001), _shifted(_EAST, 0.002)
    lines = geopandas.GeoDataFrame([_block(g) for g in (a, b, c)], crs="EPSG:4326")
    zone = geopandas.GeoDataFrame(
        {"SCHED": ["3rd Thursday"]}, geometry=[c.buffer(0.01)], crs="EPSG:4326"
    )
    posted = geopandas.GeoDataFrame(geometry=[b, c], crs="EPSG:4326")
    commercial = geopandas.GeoDataFrame(
        {"SWEEPING_SCHEDULE": ["Monday - Friday", "Friday, Every Other"]},
        geometry=[c, LineString([(-122.0, 37.0), (-122.001, 37.0)])],
        crs="EPSG:4326",
    )
    out = san_leandro.build(lines, zone, commercial, posted).set_index("DAY_EVEN")
    assert sorted(out.index) == ["MTWTHFE", "POSTED", "TH3"]


def test_hayward_rows_read_side_tokens_and_the_narrowest_range_wins():
    rows = hayward.rows_from(
        [
            "1 A ST 594 1197 BOTH EVERY M,W,F ANYTIME",
            "2 A ST 1128 1374 EVEN 1ST & 3RD WED AS POSTED",
            "4 A ST 584 957 (W Ast) BOTH EVERYOTHER THU ANYTIME",
            "467 SAINT ANDREWS ST 0 999999 BOTH 2ND & 4TH TUES ANYTIME",
            "96 BAHAMA AVE 27439 27597 S BOTH 1ST & 3RD FRI ANYTIME",
            "21 ALCAZAR AVE (PVT) 0 999999 BOTH NO SCHEDULED SWEEPING",
            "153 BERRY AV Mission Belmont NS 2ND & 4TH FRI AS POSTED",
        ]
    )
    assert [r["entry"][0] for r in rows] == ["MWFE", "W13", "POSTED", "T24", "F13"]
    lines = geopandas.GeoDataFrame(
        [
            _block(_EAST, (1101, 1199), (1100, 1198), "A ST"),
            _block(_EAST, (1, 99), (2, 98), "ST ANDREWS ST"),
        ],
        crs="EPSG:4326",
    )
    out = hayward.join(rows, lines).set_index("STREET_NAME")
    assert (out.loc["A ST"].DAY_EVEN, out.loc["A ST"].DAY_ODD) == ("W13", "MWFE")
    assert out.loc["ST ANDREWS ST"].DAY_ODD == "T24"


def test_albany_address_limit_beats_divider_beats_whole_street():
    solano, marin = _shifted(_EAST, 0.002), _EAST
    pablo = LineString([(-122.2905, 37.83), (-122.2905, 37.84)])
    north, middle = _shifted(_EAST, 0.003), _shifted(_EAST, 0.001)
    lines = geopandas.GeoDataFrame(
        [
            _block(solano, name="SOLANO AV"),
            _block(marin, name="MARIN AV"),
            _block(pablo, name="SAN PABLO AV"),
            _block(north, (1201, 1299), (1200, 1298), "RAMONA AV"),
            _block(middle, (901, 999), (900, 998), "RAMONA AV"),
            _block(_shifted(_EAST, -0.001), (1001, 1099), (1000, 1098), "RAMONA AV"),
        ],
        crs="EPSG:4326",
    )
    week = {"odd": "3rd Wednesday", "even": "4th Wednesday", "time": "10AM-2PM"}
    table = {
        "zones": {"A": week, "B": week | {"odd": "1st Monday"}, "C": week | {"odd": "2nd Monday"}},
        "streets": [
            ["A", "Ramona Avenue", "all"],
            ["B", "Ramona Avenue", "Solano to Marin"],
            ["C", "Ramona Avenue", "1000-1199"],
        ],
    }
    out = albany.build(table, lines).set_index("L_F_ADD")
    assert [out.loc[n].DAY_ODD for n in (1201, 901, 1001)] == ["W3", "M1", "M2"]
    assert out.loc[901].DAY_EVEN == "W4" and out.loc[901].TIME_ODD == "10AM–2PM"
