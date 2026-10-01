"""
tests/test_car_panel_consistency.py

Verifies that the sweeping fields /check returns for the car panel are
mutually consistent:

    urgency        — "today" | "tomorrow" | "safe"  (union of both sides)
    car_side       — "even" | "odd" | None
    schedule_even  — list of (code, desc, time) for even side
    schedule_odd   — list of (code, desc, time) for odd side

Pipeline under test:

    GDF row ──► get_schedule(row, 0/1) ──► schedules_for_segment()
             ──► check_day_street_sweeping(even + odd) ──► urgency
             ──► _parity() / _determine_side()        ──► car_side

Cross-field invariants explicitly tested:
  1. If urgency="today", at least one of schedule_even / schedule_odd contains
     a date that is today.  (urgency is the UNION of both sides.)
  2. schedules_for_segment() and get_schedule() agree.
  3. Past-end-time → urgency="safe" even when today is a sweep day.
  4. urgency="today" does NOT guarantee car's own side sweeps today (union
     semantics) — documented as an explicit edge case.
  5. When DAY_EVEN/ODD are missing or empty, schedules are empty and urgency is "safe".
"""

import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from broombuster import analysis, normalize, resolve

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("America/Los_Angeles")

# Fixed reference: 2026-04-18 09:00 LA (Saturday)
_NOW_MORNING = datetime.datetime(2026, 4, 18, 9, 0, tzinfo=_TZ)
_NOW_NOON    = datetime.datetime(2026, 4, 18, 12, 0, tzinfo=_TZ)
_TODAY       = _NOW_MORNING.date()           # 2026-04-18
_TOMORROW    = _TODAY + datetime.timedelta(days=1)   # 2026-04-19
_AFTER       = _TODAY + datetime.timedelta(days=2)   # not today/tomorrow


def _dates(d: datetime.date) -> str:
    return f"DATES:{d.isoformat()}"


def _urgency(seg, local_now):
    """Both sides' urgency for one row, as the sweeping plugin computes it."""
    even, odd = analysis.schedules_for_segment(seg)
    return analysis.check_day_street_sweeping(even + odd, local_now=local_now)


def _seg(**kw) -> pd.Series:
    """Minimal fake GDF row (pandas Series) with only the columns we set."""
    defaults = {
        "STREET_NAME": "TEST ST",
        "STREET_DISPLAY": "Test St",
        "DAY_EVEN": None,
        "DAY_ODD": None,
        "DESC_EVEN": "",
        "DESC_ODD": "",
        "TIME_EVEN": "",
        "TIME_ODD": "",
        "L_F_ADD": None, "L_T_ADD": None,
        "R_F_ADD": None, "R_T_ADD": None,
    }
    defaults.update(kw)
    return pd.Series(defaults)


# ---------------------------------------------------------------------------
# A. get_schedule() — the primitive that feeds all downstream fields
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_even_side_returns_tuple(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Every Mon", TIME_EVEN="8AM-10AM")
        result = analysis.get_schedule(seg, 0)
        assert result == ("ME", "Every Mon", "8AM-10AM")

    def test_odd_side_returns_tuple(self):
        seg = _seg(DAY_ODD="WE", DESC_ODD="Every Wed", TIME_ODD="7AM-9AM")
        result = analysis.get_schedule(seg, 1)
        assert result == ("WE", "Every Wed", "7AM-9AM")

    def test_missing_day_even_returns_none(self):
        seg = _seg(DAY_EVEN=None, DESC_EVEN="Mon", TIME_EVEN="8AM-10AM")
        assert analysis.get_schedule(seg, 0) is None

    def test_empty_string_day_even_returns_none(self):
        seg = _seg(DAY_EVEN="", DESC_EVEN="Mon")
        assert analysis.get_schedule(seg, 0) is None

    def test_whitespace_only_day_even_returns_none(self):
        seg = _seg(DAY_EVEN="   ")
        assert analysis.get_schedule(seg, 0) is None

    def test_missing_desc_defaults_to_empty(self):
        seg = _seg(DAY_EVEN="ME")  # DESC_EVEN and TIME_EVEN absent defaults
        result = analysis.get_schedule(seg, 0)
        assert result is not None
        assert result[1] == ""
        assert result[2] == ""

    def test_missing_time_defaults_to_empty(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon")
        result = analysis.get_schedule(seg, 0)
        assert result[2] == ""

    def test_odd_side_when_only_even_present(self):
        seg = _seg(DAY_EVEN="ME", DAY_ODD=None)
        assert analysis.get_schedule(seg, 1) is None

    def test_even_side_when_only_odd_present(self):
        seg = _seg(DAY_ODD="WE", DAY_EVEN=None)
        assert analysis.get_schedule(seg, 0) is None


# ---------------------------------------------------------------------------
# B. schedules_for_segment() — wraps get_schedule for both sides
# ---------------------------------------------------------------------------

class TestSchedulesForSegment:
    def test_both_sides_present(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM",
                   DAY_ODD="WE",  DESC_ODD="Wed",  TIME_ODD="9AM-11AM")
        se, so = analysis.schedules_for_segment(seg)
        assert se == [("ME", "Mon", "8AM-10AM")]
        assert so == [("WE", "Wed", "9AM-11AM")]

    def test_even_only(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon")
        se, so = analysis.schedules_for_segment(seg)
        assert len(se) == 1
        assert so == []

    def test_odd_only(self):
        seg = _seg(DAY_ODD="WE", DESC_ODD="Wed")
        se, so = analysis.schedules_for_segment(seg)
        assert se == []
        assert len(so) == 1

    def test_neither_side(self):
        seg = _seg()
        se, so = analysis.schedules_for_segment(seg)
        assert se == []
        assert so == []

    def test_none_segment_returns_empty(self):
        se, so = analysis.schedules_for_segment(None)
        assert se == []
        assert so == []

    def test_agrees_with_get_schedule(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM",
                   DAY_ODD="WE",  DESC_ODD="Wed",  TIME_ODD="9AM-11AM")
        se, so = analysis.schedules_for_segment(seg)
        assert se[0] == analysis.get_schedule(seg, 0)
        assert so[0] == analysis.get_schedule(seg, 1)

    def test_tuple_structure_has_three_elements(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM")
        se, _ = analysis.schedules_for_segment(seg)
        assert len(se[0]) == 3
        code, desc, time = se[0]
        assert code == "ME"
        assert desc == "Mon"
        assert time == "8AM-10AM"


# ---------------------------------------------------------------------------
# C. Urgency — both sides of one segment at a given datetime
# ---------------------------------------------------------------------------

class TestComputeUrgency:
    def test_today_when_today_in_dates(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        assert _urgency(seg, local_now=_NOW_MORNING) == "today"

    def test_tomorrow_when_only_tomorrow_in_dates(self):
        seg = _seg(DAY_EVEN=_dates(_TOMORROW))
        assert _urgency(seg, local_now=_NOW_MORNING) == "tomorrow"

    def test_false_when_neither_today_nor_tomorrow(self):
        seg = _seg(DAY_EVEN=_dates(_AFTER))
        assert _urgency(seg, local_now=_NOW_MORNING) == "safe"

    def test_false_past_end_time(self):
        # 8AM-10AM, local_now is 12:00 → window closed → safe
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        assert _urgency(seg, local_now=_NOW_NOON) == "safe"

    def test_today_within_time_window(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="10AM-12PM")
        # 12:00 exactly is still within "10AM-12PM" (end inclusive per implementation)
        result = _urgency(seg, local_now=_NOW_NOON)
        # noon ≤ noon end → still "today"
        assert result == "today"

    def test_today_via_odd_side_only(self):
        # urgency is UNION: car_side="even" but only ODD sweeps today → still "today"
        seg = _seg(DAY_ODD=_dates(_TODAY), TIME_ODD="8AM-10AM")
        result = _urgency(seg, local_now=_NOW_MORNING)
        assert result == "today", (
            "urgency is union of both sides — should be 'today' even when "
            "only the odd side sweeps"
        )

    def test_both_sides_sweep_today_returns_today_not_doubled(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_TODAY),  TIME_ODD="8AM-10AM")
        assert _urgency(seg, local_now=_NOW_MORNING) == "today"

    def test_even_today_odd_tomorrow(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY),    TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_TOMORROW))
        result = _urgency(seg, local_now=_NOW_MORNING)
        assert result == "today"  # even side wins

    def test_none_segment_returns_false(self):
        assert _urgency(None, local_now=_NOW_MORNING) == "safe"

    def test_no_day_columns_returns_false(self):
        seg = _seg()  # both DAY_EVEN and DAY_ODD are None
        assert _urgency(seg, local_now=_NOW_MORNING) == "safe"

    def test_unknown_code_returns_false(self):
        seg = _seg(DAY_EVEN="XYZZY")
        assert _urgency(seg, local_now=_NOW_MORNING) == "safe"

    def test_tomorrow_no_time_constraint(self):
        # tomorrow with no time info → "tomorrow" (time check doesn't apply)
        seg = _seg(DAY_ODD=_dates(_TOMORROW))
        assert _urgency(seg, local_now=_NOW_NOON) == "tomorrow"


# ---------------------------------------------------------------------------
# D. normalize.time_window() end — time-boundary building block
# ---------------------------------------------------------------------------

class TestParseEndTime:
    def _p(self, s):
        w = normalize.time_window(s)
        return w[1] if w else None

    def test_basic_am_range(self):
        assert self._p("8AM-10AM") == datetime.time(10, 0)

    def test_en_dash_separator(self):
        assert self._p("8AM\u201310AM") == datetime.time(10, 0)

    def test_with_minutes(self):
        assert self._p("7:30AM-9:15AM") == datetime.time(9, 15)

    def test_to_keyword(self):
        assert self._p("8AM to 10AM") == datetime.time(10, 0)

    def test_pm_range(self):
        assert self._p("1PM-3PM") == datetime.time(15, 0)

    def test_12pm_is_noon(self):
        assert self._p("11AM-12PM") == datetime.time(12, 0)

    def test_12am_end_is_end_of_day(self):
        assert self._p("10PM-12AM") == datetime.time(23, 59, 59)

    def test_empty_string_returns_none(self):
        assert self._p("") is None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_garbage_string_returns_none(self):
        assert self._p("no time info") is None

    def test_number_only_returns_none(self):
        assert self._p("12345") is None


# ---------------------------------------------------------------------------
# E. _parity() — address-range parity for side determination
# ---------------------------------------------------------------------------

class TestParity:
    def test_both_even_returns_even(self):
        assert resolve._parity(100, 200) == "even"

    def test_both_odd_returns_odd(self):
        assert resolve._parity(101, 201) == "odd"

    def test_mixed_returns_none(self):
        assert resolve._parity(100, 201) is None

    def test_zero_is_even(self):
        assert resolve._parity(0, 100) == "even"

    def test_one_none_returns_none(self):
        assert resolve._parity(None, 200) is None

    def test_both_none_returns_none(self):
        assert resolve._parity(None, None) is None

    def test_r_from_r_to_variant(self):
        assert resolve._parity(r_from=200, r_to=400) == "even"
        assert resolve._parity(r_from=201, r_to=401) == "odd"
        assert resolve._parity(r_from=200, r_to=401) is None

    def test_float_strings_coerced(self):
        # address ranges often arrive as floats from shapefile
        assert resolve._parity("100.0", "200.0") == "even"
        assert resolve._parity("101.0", "201.0") == "odd"

    def test_nan_string_returns_none(self):
        assert resolve._parity("nan", "200") is None

    def test_large_range_both_odd(self):
        # 1 and 9999 are both odd → "odd"
        assert resolve._parity(1, 9999) == "odd"

    def test_large_range_mixed(self):
        # 2 and 9999 are mixed → None
        assert resolve._parity(2, 9999) is None


# ---------------------------------------------------------------------------
# G. Cross-field consistency invariants
# ---------------------------------------------------------------------------

class TestCrossFieldConsistency:
    """
    These tests verify that the fields produced by the pipeline are mutually
    coherent — the bugs the user observed were caused by these invariants
    being violated silently.
    """

    def test_urgency_today_iff_at_least_one_side_has_today(self):
        """
        urgency='today' ↔ at least one of schedule_even / schedule_odd
        contains a date that is today (in local_now).  This is the union.
        """
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_AFTER))
        se, so = analysis.schedules_for_segment(seg)
        urgency = _urgency(seg, local_now=_NOW_MORNING)
        assert urgency == "today"

        # Confirm: the even side contains today, odd side does not
        from broombuster.analysis import sweeps_on
        today_in_even = sweeps_on(se[0][0], _TODAY)
        today_in_odd  = sweeps_on(so[0][0], _TODAY)
        assert today_in_even,  "Even side should contain today"
        assert not today_in_odd, "Odd side should not contain today"

    def test_urgency_today_but_car_own_side_safe_union_semantics(self):
        """
        KNOWN EDGE CASE: urgency='today' but the car's own side (odd) does
        not sweep today — only the even side does.  This is intentional union
        behaviour (conservative: warn if either side sweeps).
        """
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_AFTER))
        urgency = _urgency(seg, local_now=_NOW_MORNING)
        se, so = analysis.schedules_for_segment(seg)

        # urgency="today" even though the ODD side (the car's side here) is safe
        assert urgency == "today"
        assert not analysis.sweeps_on(so[0][0], _TODAY)

    def test_schedules_for_segment_agrees_with_urgency_when_today(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        se, _ = analysis.schedules_for_segment(seg)
        assert len(se) == 1
        urgency = _urgency(seg, local_now=_NOW_MORNING)
        assert urgency == "today"
        # The code driving urgency is the same code in the schedule tuple
        assert se[0][0] == _dates(_TODAY)

    def test_schedules_for_segment_agrees_when_false(self):
        seg = _seg(DAY_EVEN=_dates(_AFTER))
        se, _ = analysis.schedules_for_segment(seg)
        urgency = _urgency(seg, local_now=_NOW_MORNING)
        assert urgency == "safe"
        assert len(se) == 1  # schedule exists; it's just not today/tomorrow

    def test_empty_schedules_produce_false_urgency(self):
        seg = _seg()
        se, so = analysis.schedules_for_segment(seg)
        urgency = _urgency(seg, local_now=_NOW_MORNING)
        assert se == []
        assert so == []
        assert urgency == "safe"

# ---------------------------------------------------------------------------
# H. API /check integration — all five fields, real GDF data
# ---------------------------------------------------------------------------

class TestApiCheckIntegration:
    """
    Uses a real GeoDataFrame (Bay Area) and the FastAPI TestClient.
    These tests verify that the five car-panel fields in the /check response
    are present and mutually coherent.
    """

    # Oakland, Chestnut St — known to resolve to a valid segment
    LAT, LON = 37.821326, -122.280705

    @pytest.fixture(scope="class")
    @classmethod
    def check_data(cls, app_client):
        resp = app_client.post("/check", json={
            "lat": cls.LAT, "lon": cls.LON, "region": "bay_area"
        })
        assert resp.status_code == 200, resp.text
        (sweeping,) = [d for d in resp.json()["domains"] if d["id"] == "sweeping"]
        return {**sweeping["extras"], "urgency": sweeping["urgency"]}

    def test_all_fields_present(self, check_data):
        for field in ("urgency", "car_side", "schedule_even", "schedule_odd"):
            assert field in check_data, f"Field '{field}' missing from /check response"

    def test_urgency_is_valid_value(self, check_data):
        assert check_data["urgency"] in ("today", "tomorrow", "safe"), (
            f"Unexpected urgency value: {check_data['urgency']!r}"
        )

    def test_car_side_is_valid(self, check_data):
        car_side = check_data["car_side"]
        assert car_side in ("even", "odd", None), (
            f"car_side must be 'even', 'odd', or None — got {car_side!r}"
        )

    def test_schedule_lists_are_lists(self, check_data):
        assert isinstance(check_data["schedule_even"], list)
        assert isinstance(check_data["schedule_odd"], list)

    def test_schedule_tuples_have_three_elements(self, check_data):
        for entry in check_data["schedule_even"] + check_data["schedule_odd"]:
            assert len(entry) == 3, f"Schedule entry should be (code, desc, time): {entry!r}"

    def test_urgency_consistent_with_at_least_one_schedule(self, check_data):
        """
        If urgency is 'today', at least one of schedule_even / schedule_odd
        must contain a code that resolves to today's date.
        """
        urgency = check_data["urgency"]
        if urgency != "today":
            pytest.skip("urgency is not 'today' — skip consistency check")

        from broombuster.analysis import sweeps_on
        today = datetime.date.today()
        all_sched = check_data["schedule_even"] + check_data["schedule_odd"]
        found_today = any(
            sweeps_on(entry[0], today)
            for entry in all_sched
            if entry and entry[0]
        )
        assert found_today, (
            f"urgency='today' but no schedule entry resolves to {today}.\n"
            f"schedules: {all_sched}"
        )


# ---------------------------------------------------------------------------
# I. Regression: missing columns must not crash the pipeline
# ---------------------------------------------------------------------------

class TestMissingColumnRobustness:
    """
    The GDF schema is not perfectly uniform across cities.  These tests ensure
    the pipeline handles missing or NaN-valued columns without raising exceptions.
    """

    @pytest.mark.parametrize("col", [
        "DESC_EVEN", "DESC_ODD", "TIME_EVEN", "TIME_ODD",
    ])
    def test_missing_optional_column_does_not_crash(self, col):
        row = {"STREET_NAME": "TEST ST", "DAY_EVEN": "ME", "DAY_ODD": "WE"}
        # col intentionally omitted — Series.get() should return None
        seg = pd.Series(row)
        result = analysis.get_schedule(seg, 0)
        assert result is not None  # DAY_EVEN is present

    def test_nan_in_day_column_returns_none(self):
        seg = _seg(DAY_EVEN=float("nan"))
        assert analysis.get_schedule(seg, 0) is None

    def test_integer_zero_in_day_column_returns_none(self):
        # 0 is falsy but is not a string — _is_str check should reject it
        seg = _seg(DAY_EVEN=0)
        assert analysis.get_schedule(seg, 0) is None

    def test_urgency_with_invalid_code_does_not_raise(self):
        seg = _seg(DAY_EVEN="INVALID_CODE_XYZY")
        result = _urgency(seg, local_now=_NOW_MORNING)
        assert result == "safe"  # unknown code → empty dates → not today/tomorrow
