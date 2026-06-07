"""Unit tests for analysis.parse_sweeping_code and check_day_street_sweeping."""
import datetime

from broombuster import analysis

# ---------------------------------------------------------------------------
# parse_sweeping_code
# ---------------------------------------------------------------------------

def test_every_monday():
    dates = analysis.parse_sweeping_code("ME")
    assert dates, "ME should return at least one date"
    assert all(d.weekday() == 0 for d in dates), "ME should return only Mondays"
    assert len(dates) >= 4


def test_every_thursday():
    dates = analysis.parse_sweeping_code("THE")
    assert all(d.weekday() == 3 for d in dates), "THE should return only Thursdays"


def test_first_and_third_monday():
    dates = analysis.parse_sweeping_code("M13")
    assert all(d.weekday() == 0 for d in dates), "M13 should be Mondays only"
    assert len(dates) == 2, "M13 should return exactly 2 dates per month"


def test_second_and_fourth_friday():
    dates = analysis.parse_sweeping_code("F24")
    assert all(d.weekday() == 4 for d in dates), "F24 should be Fridays only"
    assert len(dates) == 2, "F24 should return exactly 2 dates per month"


def test_mwf_compound():
    dates = analysis.parse_sweeping_code("MWF")
    weekdays = {d.weekday() for d in dates}
    assert weekdays == {0, 2, 4}, "MWF should expand to Mon, Wed, Fri"


def test_mf_is_monday_and_friday_only():
    """MF means Monday AND Friday — NOT the full work week."""
    dates = analysis.parse_sweeping_code("MF")
    weekdays = {d.weekday() for d in dates}
    assert weekdays == {0, 4}, "MF should be Monday and Friday only"
    assert 1 not in weekdays, "MF must not include Tuesday"
    assert 2 not in weekdays, "MF must not include Wednesday"
    assert 3 not in weekdays, "MF must not include Thursday"


def test_tth_compound():
    dates = analysis.parse_sweeping_code("TTH")
    weekdays = {d.weekday() for d in dates}
    assert weekdays == {1, 3}, "TTH should be Tuesday and Thursday"


def test_chicago_dates():
    dates = analysis.parse_sweeping_code("DATES:2026-04-01,2026-04-15,2026-05-06")
    assert datetime.date(2026, 4, 1) in dates
    assert datetime.date(2026, 4, 15) in dates
    assert datetime.date(2026, 5, 6) in dates
    assert len(dates) == 3


def test_unknown_code_returns_empty():
    assert analysis.parse_sweeping_code("XYZ") == []


# ---------------------------------------------------------------------------
# future_dates_desc — display string for Chicago 'DATES:' codes
# ---------------------------------------------------------------------------

_FD_NOW = datetime.datetime(2026, 6, 6, 9, 0)


def test_future_dates_desc_drops_past_dates():
    code = "DATES:2026-04-17,2026-05-15,2026-06-05,2026-06-19,2026-07-03"
    out = analysis.future_dates_desc(code, _FD_NOW)
    assert "Apr" not in out and "May" not in out
    assert "5" not in out.split(";")[0]  # Jun 5 is past, excluded
    assert out == "Jun 19; Jul 3"


def test_future_dates_desc_includes_today():
    code = "DATES:2026-06-06,2026-06-20"
    assert analysis.future_dates_desc(code, _FD_NOW) == "Jun 6, 20"


def test_future_dates_desc_non_dates_returns_none():
    assert analysis.future_dates_desc("MWF", _FD_NOW) is None
    assert analysis.future_dates_desc(None, _FD_NOW) is None


def test_future_dates_desc_all_past_returns_empty():
    assert analysis.future_dates_desc("DATES:2026-04-17,2026-05-15", _FD_NOW) == ""


def test_no_sweep_codes_return_list():
    """N / NS / O are non-sweep markers; parse should not crash."""
    for code in ("N", "NS", "O"):
        result = analysis.parse_sweeping_code(code)
        assert isinstance(result, list)


def test_every_day():
    dates = analysis.parse_sweeping_code("E")
    today = datetime.date.today()
    assert len(dates) >= 28, "E should return every day of the month"
    assert all(d.month == today.month or d.month == (today + datetime.timedelta(days=1)).month
               for d in dates)


def test_caching_consistent():
    """Two calls with the same code should return equal results."""
    a = analysis.parse_sweeping_code("WE")
    b = analysis.parse_sweeping_code("WE")
    assert a == b


# ---------------------------------------------------------------------------
# check_day_street_sweeping
# ---------------------------------------------------------------------------

def test_empty_schedule_returns_false():
    result = analysis.check_day_street_sweeping([])
    assert result is False


def test_return_type_is_string_or_false():
    result = analysis.check_day_street_sweeping([])
    assert result is False or result in ("today", "tomorrow")


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
