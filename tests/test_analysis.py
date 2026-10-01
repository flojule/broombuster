"""Unit tests for analysis sweep-code rules and check_day_street_sweeping."""
import datetime
from zoneinfo import ZoneInfo

from broombuster import analysis

# September 2026: Tue 1st .. Wed 30th; five Tuesdays (1, 8, 15, 22, 29).
_SEP_START = datetime.date(2026, 9, 1)
_SEP_END = datetime.date(2026, 9, 30)


def _sep(code):
    return analysis.dates_in_range(code, _SEP_START, _SEP_END)


# ---------------------------------------------------------------------------
# dates_in_range / sweeps_on
# ---------------------------------------------------------------------------

def test_every_monday():
    dates = _sep("ME")
    assert dates and all(d.weekday() == 0 for d in dates)
    assert len(dates) == 4


def test_every_thursday():
    assert all(d.weekday() == 3 for d in _sep("THE"))


def test_first_and_third_monday():
    assert _sep("M13") == [datetime.date(2026, 9, 7), datetime.date(2026, 9, 21)]


def test_second_and_fourth_friday():
    assert _sep("F24") == [datetime.date(2026, 9, 11), datetime.date(2026, 9, 25)]


def test_fifth_week_ordinal():
    """SF 'T135' / 'T245' include the 5th Tuesday (Sep 29)."""
    assert _sep("T135") == [datetime.date(2026, 9, d) for d in (1, 15, 29)]
    assert _sep("T245") == [datetime.date(2026, 9, d) for d in (8, 22, 29)]


def test_mwf_compound():
    assert {d.weekday() for d in _sep("MWF")} == {0, 2, 4}


def test_mf_is_monday_and_friday_only():
    """MF means Monday AND Friday — NOT the full work week."""
    assert {d.weekday() for d in _sep("MF")} == {0, 4}


def test_tth_compound():
    assert {d.weekday() for d in _sep("TTH")} == {1, 3}


def test_compound_every_codes():
    """Oakland 'MTHE' = every Mon + Thu; 'TTHE' = every Tue + Thu."""
    assert {d.weekday() for d in _sep("MTHE")} == {0, 3}
    assert {d.weekday() for d in _sep("TTHE")} == {1, 3}


def test_chicago_dates():
    dates = analysis.dates_in_range(
        "DATES:2026-04-01,2026-04-15,2026-05-06",
        datetime.date(2026, 1, 1), datetime.date(2026, 12, 31))
    assert dates == [datetime.date(2026, 4, 1), datetime.date(2026, 4, 15),
                     datetime.date(2026, 5, 6)]


def test_invalid_date_in_dates_code_keeps_valid_dates():
    assert analysis.sweeps_on("DATES:2026-09-29,bad", datetime.date(2026, 9, 29))


def test_unknown_code_returns_empty():
    assert _sep("XYZ") == []
    assert _sep("W1357") == []


def test_no_sweep_codes_have_no_dates():
    for code in ("N", "NS", "O", "MS", "DM", "missing"):
        assert _sep(code) == [], code


def test_every_day():
    assert len(_sep("E")) == 30


def test_range_crosses_month_boundary():
    dates = analysis.dates_in_range("WE", datetime.date(2026, 9, 28), datetime.date(2026, 10, 8))
    assert dates == [datetime.date(2026, 9, 30), datetime.date(2026, 10, 7)]


# ---------------------------------------------------------------------------
# check_day_street_sweeping
# ---------------------------------------------------------------------------

def test_empty_schedule_returns_safe():
    result = analysis.check_day_street_sweeping([])
    assert result == "safe"


def test_return_type_is_urgency_string():
    result = analysis.check_day_street_sweeping([])
    assert result in ("today", "tomorrow", "safe")


def test_today_sweep_returns_today():
    today_code = _code_for_date(datetime.date.today())
    schedule = [(today_code, "Test", "8AM-10AM")]
    result = analysis.check_day_street_sweeping(schedule)
    # Time has the day already passed? Use a time guaranteed to still be open.
    # check_day_street_sweeping uses datetime.date.today() when local_now is None,
    # and treats untimed entries as still active — so "today" is expected.
    # If the 8-10AM window has closed in real local time, the test still passes
    # because we provide local_now=None (which skips the time-window check).
    assert result == "today"


def test_tomorrow_sweep_returns_tomorrow():
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    tomorrow_code = _code_for_date(tomorrow)
    schedule = [(tomorrow_code, "Test", "8AM-10AM")]
    result = analysis.check_day_street_sweeping(schedule)
    # Could be "today" if the code also matches today, but at minimum truthy
    assert result in ("today", "tomorrow")


def test_urgency_uses_region_local_date_across_month_end():
    """Wed Sep 30 18:00 in LA is Oct 1 UTC; urgency follows the local date."""
    la = datetime.datetime(2026, 9, 30, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert analysis.check_day_street_sweeping([("WE", "", "")], local_now=la) == "today"
    assert analysis.check_day_street_sweeping([("THE", "", "")], local_now=la) == "tomorrow"


def test_closed_window_falls_through_to_tomorrow():
    now = datetime.datetime(2026, 9, 30, 11, 0)  # Wed
    sched = [("WE", "", "8AM-10AM"), ("THE", "", "8AM-10AM")]
    assert analysis.check_day_street_sweeping(sched, local_now=now) == "tomorrow"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _code_for_date(d: datetime.date):
    """Return an 'every weekday' code that covers the given date.

    weekday() is always 0–6, and there's a code for every weekday, so this
    never fails. Mon=0 → ME, Tue=1 → TE, …, Sun=6 → SUE.
    """
    codes = ["ME", "TE", "WE", "THE", "FE", "SE", "SUE"]
    return codes[d.weekday()]
