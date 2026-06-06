"""
tests/test_car_panel_consistency.py

Verifies that the five fields returned by /check and rendered in the car panel
are mutually consistent:

    urgency        — "today" | "tomorrow" | False  (union of both sides)
    car_side       — "even" | "odd" | None
    schedule_even  — list of (code, desc, time) for even side
    schedule_odd   — list of (code, desc, time) for odd side
    message        — plain text; ► marker must be on car_side's line

Pipeline under test:

    GDF row ──► get_schedule(row, 0/1) ──► schedules_for_segment()
             ──► compute_urgency()          ──► urgency
             ──► compose_message()          ──► message (► highlights car_side)
             ──► _parity() / _determine_side() ──► car_side

Cross-field invariants explicitly tested:
  1. ► in message always marks the same side as car_side.
  2. If urgency="today", at least one of schedule_even / schedule_odd contains
     a date that is today.  (urgency is the UNION of both sides.)
  3. schedules_for_segment() and get_schedule() agree.
  4. Past-end-time → urgency=False even when today is a sweep day.
  5. urgency="today" does NOT guarantee car's own side sweeps today (union
     semantics) — documented as an explicit edge case.
  6. When DAY_EVEN/ODD are missing or empty, schedules are empty and urgency is False.
"""

import os

os.environ.setdefault("DEV_MODE", "1")

import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from broombuster import analysis, resolve
from broombuster.domains.sweeping import compose_message

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
# C. compute_urgency() — pure urgency from segment + datetime
# ---------------------------------------------------------------------------

class TestComputeUrgency:
    def test_today_when_today_in_dates(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) == "today"

    def test_tomorrow_when_only_tomorrow_in_dates(self):
        seg = _seg(DAY_EVEN=_dates(_TOMORROW))
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) == "tomorrow"

    def test_false_when_neither_today_nor_tomorrow(self):
        seg = _seg(DAY_EVEN=_dates(_AFTER))
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) is False

    def test_false_past_end_time(self):
        # 8AM-10AM, local_now is 12:00 → window closed → False
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        assert analysis.compute_urgency(seg, local_now=_NOW_NOON) is False

    def test_today_within_time_window(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="10AM-12PM")
        # 12:00 exactly is still within "10AM-12PM" (end inclusive per implementation)
        result = analysis.compute_urgency(seg, local_now=_NOW_NOON)
        # noon ≤ noon end → still "today"
        assert result == "today"

    def test_today_via_odd_side_only(self):
        # urgency is UNION: car_side="even" but only ODD sweeps today → still "today"
        seg = _seg(DAY_ODD=_dates(_TODAY), TIME_ODD="8AM-10AM")
        result = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert result == "today", (
            "urgency is union of both sides — should be 'today' even when "
            "only the odd side sweeps"
        )

    def test_both_sides_sweep_today_returns_today_not_doubled(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_TODAY),  TIME_ODD="8AM-10AM")
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) == "today"

    def test_even_today_odd_tomorrow(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY),    TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_TOMORROW))
        result = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert result == "today"  # even side wins

    def test_none_segment_returns_false(self):
        assert analysis.compute_urgency(None, local_now=_NOW_MORNING) is False

    def test_no_day_columns_returns_false(self):
        seg = _seg()  # both DAY_EVEN and DAY_ODD are None
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) is False

    def test_unknown_code_returns_false(self):
        seg = _seg(DAY_EVEN="XYZZY")
        assert analysis.compute_urgency(seg, local_now=_NOW_MORNING) is False

    def test_tomorrow_no_time_constraint(self):
        # tomorrow with no time info → "tomorrow" (time check doesn't apply)
        seg = _seg(DAY_ODD=_dates(_TOMORROW))
        assert analysis.compute_urgency(seg, local_now=_NOW_NOON) == "tomorrow"


# ---------------------------------------------------------------------------
# D. _parse_time_range() — time-boundary building block
# ---------------------------------------------------------------------------

class TestParseTimeRange:
    def _p(self, s):
        return analysis._parse_time_range(s)

    def test_basic_am_range(self):
        start, end = self._p("8AM-10AM")
        assert start == datetime.time(8, 0)
        assert end   == datetime.time(10, 0)

    def test_en_dash_separator(self):
        # U+2013 en-dash
        start, end = self._p("8AM\u201310AM")
        assert start == datetime.time(8, 0)
        assert end   == datetime.time(10, 0)

    def test_with_minutes(self):
        start, end = self._p("7:30AM-9AM")
        assert start == datetime.time(7, 30)
        assert end   == datetime.time(9, 0)

    def test_to_keyword(self):
        start, end = self._p("8AM to 10AM")
        assert start == datetime.time(8, 0)
        assert end   == datetime.time(10, 0)

    def test_pm_range(self):
        start, end = self._p("1PM-3PM")
        assert start == datetime.time(13, 0)
        assert end   == datetime.time(15, 0)

    def test_12pm_is_noon(self):
        _, end = self._p("11AM-12PM")
        assert end == datetime.time(12, 0)

    def test_12am_is_midnight(self):
        start, _ = self._p("12AM-1AM")
        assert start == datetime.time(0, 0)

    def test_empty_string_returns_none_pair(self):
        assert self._p("") == (None, None)

    def test_none_returns_none_pair(self):
        assert self._p(None) == (None, None)

    def test_garbage_string_returns_none_pair(self):
        assert self._p("no time info") == (None, None)

    def test_number_only_returns_none_pair(self):
        assert self._p("12345") == (None, None)


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
# F. compose_message() — ► placement invariants
# ---------------------------------------------------------------------------

class TestComposeMessage:
    def _make(self, code, desc, time_str):
        return [(code, desc, time_str)]

    def test_car_side_even_highlights_even(self):
        se = self._make("ME", "Mon sweeping", "8AM-10AM")
        msg = compose_message(se, [], car_side="even")
        lines = msg.splitlines()
        even_line = next(ln for ln in lines if "Even" in ln)
        odd_line  = next(ln for ln in lines if "Odd" in ln)
        assert even_line.startswith("►"), f"Even line not highlighted: {msg!r}"
        assert not odd_line.startswith("►"), f"Odd line incorrectly highlighted: {msg!r}"

    def test_car_side_odd_highlights_odd(self):
        so = self._make("WE", "Wed sweeping", "9AM-11AM")
        msg = compose_message([], so, car_side="odd")
        lines = msg.splitlines()
        even_line = next(ln for ln in lines if "Even" in ln)
        odd_line  = next(ln for ln in lines if "Odd" in ln)
        assert odd_line.startswith("►"), f"Odd line not highlighted: {msg!r}"
        assert not even_line.startswith("►"), f"Even line incorrectly highlighted: {msg!r}"

    def test_both_sides_same_produces_single_street_line(self):
        entry = self._make("ME", "Mon sweeping", "8AM-10AM")
        msg = compose_message(entry, entry, car_side="even")
        assert msg.startswith("► Street:"), f"Expected single street line, got: {msg!r}"
        assert "\n" not in msg, "Single-street message should not have newlines"

    def test_both_sides_different_produces_two_lines(self):
        se = self._make("ME", "Mon sweeping", "8AM-10AM")
        so = self._make("WE", "Wed sweeping", "9AM-11AM")
        msg = compose_message(se, so, car_side="even")
        assert "\n" in msg, "Two-schedule message should have a newline"

    def test_no_schedule_either_side(self):
        msg = compose_message([], [], car_side="even")
        assert "no sweeping" in msg.lower()
        lines = msg.splitlines()
        even_line = next(ln for ln in lines if "Even" in ln)
        assert even_line.startswith("►"), "Even side should still be highlighted even when no sweep"

    def test_car_side_none_no_highlight_anywhere(self):
        se = self._make("ME", "Mon", "8AM-10AM")
        so = self._make("WE", "Wed", "9AM-11AM")
        msg = compose_message(se, so, car_side=None)
        lines = msg.splitlines()
        assert not any(ln.startswith("►") for ln in lines), (
            f"No side should be highlighted when car_side=None: {msg!r}"
        )

    def test_dedup_identical_entries_on_same_side(self):
        entry = ("ME", "Mon sweeping", "8AM-10AM")
        se = [entry, entry]  # duplicated
        msg = compose_message(se, [], car_side="even")
        even_line = next(ln for ln in msg.splitlines() if "Even" in ln)
        # Should not have " / Mon sweeping / Mon sweeping"
        assert even_line.count("Mon sweeping") == 1, f"Duplicate not deduped: {even_line!r}"

    def test_desc_and_time_joined_with_dash(self):
        se = self._make("ME", "Mon sweeping", "8AM-10AM")
        msg = compose_message(se, [], car_side="even")
        assert "Mon sweeping" in msg
        assert "8AM-10AM" in msg
        assert "—" in msg  # em-dash separator

    def test_desc_only_no_dash_when_no_time(self):
        se = self._make("ME", "Mon sweeping", "")
        msg = compose_message(se, [], car_side="even")
        assert "Mon sweeping" in msg
        assert "—" not in msg


# ---------------------------------------------------------------------------
# G. Cross-field consistency invariants
# ---------------------------------------------------------------------------

class TestCrossFieldConsistency:
    """
    These tests verify that the fields produced by the pipeline are mutually
    coherent — the bugs the user observed were caused by these invariants
    being violated silently.
    """

    def test_highlight_matches_car_side_even(self):
        se = [("ME", "Mon", "8AM-10AM")]
        so = [("WE", "Wed", "9AM-11AM")]
        for car_side in ("even", "odd"):
            msg = compose_message(se, so, car_side=car_side)
            lines = msg.splitlines()
            highlighted = [ln for ln in lines if ln.startswith("►")]
            assert len(highlighted) == 1, f"Exactly one line should be highlighted: {msg!r}"
            labeled_side = "Even" if car_side == "even" else "Odd"
            assert labeled_side in highlighted[0], (
                f"Highlighted line should be for {car_side} side: {msg!r}"
            )

    def test_urgency_today_iff_at_least_one_side_has_today(self):
        """
        urgency='today' ↔ at least one of schedule_even / schedule_odd
        contains a date that is today (in local_now).  This is the union.
        """
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_AFTER))
        se, so = analysis.schedules_for_segment(seg)
        urgency = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert urgency == "today"

        # Confirm: the even side contains today, odd side does not
        from broombuster.analysis import parse_sweeping_code
        today_in_even = _TODAY in parse_sweeping_code(se[0][0])
        today_in_odd  = _TODAY in parse_sweeping_code(so[0][0])
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
        urgency = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        se, so = analysis.schedules_for_segment(seg)

        # urgency="today" even though the ODD side (the car's side here) is safe
        assert urgency == "today"

        # Now build message for a car parked on the odd side
        msg = compose_message(se, so, car_side="odd")
        odd_line = next(ln for ln in msg.splitlines() if "Odd" in ln)
        # The odd line is highlighted (car is there) but shows the odd schedule
        # which is NOT today — message correctly shows future date, not "today"
        assert "►" in odd_line, "Odd side should be highlighted (car is there)"
        # urgency is "today" from the even side — this can appear inconsistent
        # in the UI if urgency banner says "Move today" but the car's side shows
        # a future schedule.  This test documents the gap.

    def test_schedules_for_segment_agrees_with_compute_urgency_when_today(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        se, _ = analysis.schedules_for_segment(seg)
        assert len(se) == 1
        urgency = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert urgency == "today"
        # The code driving urgency is the same code in the schedule tuple
        assert se[0][0] == _dates(_TODAY)

    def test_schedules_for_segment_agrees_when_false(self):
        seg = _seg(DAY_EVEN=_dates(_AFTER))
        se, _ = analysis.schedules_for_segment(seg)
        urgency = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert urgency is False
        assert len(se) == 1  # schedule exists; it's just not today/tomorrow

    def test_empty_schedules_produce_false_urgency(self):
        seg = _seg()
        se, so = analysis.schedules_for_segment(seg)
        urgency = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert se == []
        assert so == []
        assert urgency is False

    def test_message_no_sweep_marker_consistent(self):
        """When neither side has sweeping, message still has ► on car_side line."""
        msg = compose_message([], [], car_side="even")
        highlighted = [ln for ln in msg.splitlines() if ln.startswith("►")]
        assert len(highlighted) == 1
        assert "Even" in highlighted[0]
        assert "no sweeping" in highlighted[0].lower()


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
    def client(self):
        from fastapi.testclient import TestClient

        from broombuster.api import app as api_mod
        with TestClient(api_mod.app) as c:
            yield c

    @pytest.fixture(scope="class")
    def check_data(self, client):
        resp = client.post("/check", json={
            "lat": self.LAT, "lon": self.LON, "region": "bay_area"
        })
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_all_five_fields_present(self, check_data):
        for field in ("urgency", "car_side", "schedule_even", "schedule_odd", "message"):
            assert field in check_data, f"Field '{field}' missing from /check response"

    def test_urgency_is_valid_value(self, check_data):
        # /check returns whatever compute_urgency() returned. Per
        # analysis.compute_urgency's contract the only legal values are
        # "today", "tomorrow", or False. Anything else is a bug — including
        # the string "safe" or None, which were previously tolerated here.
        assert check_data["urgency"] in ("today", "tomorrow", False), (
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

    def test_message_is_string(self, check_data):
        assert isinstance(check_data["message"], str)

    def test_message_highlights_car_side(self, check_data):
        car_side = check_data["car_side"]
        msg = check_data["message"]
        if car_side is None:
            pytest.skip("car_side is None — no side to highlight")
        labeled = "Even" if car_side == "even" else "Odd"
        highlighted_lines = [ln for ln in msg.splitlines() if ln.startswith("►")]
        if not highlighted_lines:
            # Single "► Street:" line — both sides same
            assert msg.startswith("► Street:"), f"Unexpected message format: {msg!r}"
        else:
            assert any(labeled in ln for ln in highlighted_lines), (
                f"car_side={car_side!r} but ► not on {labeled} side.\n"
                f"message:\n{msg}"
            )

    def test_snap_field_present_and_valid(self, check_data):
        snap = check_data.get("snap")
        assert snap is not None, "snap field missing — resolver failed to find a segment"
        assert "street_name" in snap
        assert "distance_m" in snap
        assert isinstance(snap["distance_m"], (int, float))

    def test_snap_street_name_matches_address(self, check_data):
        """address field should contain the same street name as snap.street_name."""
        from broombuster import normalize
        snap = check_data.get("snap") or {}
        snap_name = snap.get("street_name", "")
        address   = check_data.get("address", "")
        if not snap_name or not address:
            pytest.skip("snap or address not present")
        assert normalize.street_name(snap_name) in normalize.street_name(address) or \
               normalize.street_name(address)   in normalize.street_name(snap_name), (
            f"snap.street_name={snap_name!r} does not match address={address!r}"
        )

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

        from broombuster.analysis import parse_sweeping_code
        today = datetime.date.today()
        all_sched = check_data["schedule_even"] + check_data["schedule_odd"]
        found_today = any(
            today in parse_sweeping_code(entry[0])
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

    def test_compose_message_empty_entries_ignored(self):
        # Entries shorter than 3 elements or falsy should be silently skipped
        msg = compose_message([None, (), ("ME",)], [], car_side="even")
        assert "no sweeping" in msg.lower()

    def test_compute_urgency_with_invalid_code_does_not_raise(self):
        seg = _seg(DAY_EVEN="INVALID_CODE_XYZY")
        result = analysis.compute_urgency(seg, local_now=_NOW_MORNING)
        assert result is False  # unknown code → empty dates → not today/tomorrow
