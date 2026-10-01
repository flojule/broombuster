"""Tests for Chicago zone click-detail: full upcoming schedule + ward PDF link."""
import datetime

import pandas as pd

from broombuster import analysis, maps

_NOW = datetime.datetime(2026, 6, 6, 9, 0)


def _detail(row, now):
    """zone_detail_html for one GDF row, as the sweeping plugin builds it."""
    even, odd = analysis.schedules_for_segment(row)
    return maps.zone_detail_html(row.get("STREET_DISPLAY"), even, odd, row.get("_city"), now)


# ---------------------------------------------------------------------------
# _ward_ordinal
# ---------------------------------------------------------------------------

def test_ward_ordinal_single_digit_zero_padded():
    assert maps._ward_ordinal(1) == "01st"
    assert maps._ward_ordinal(2) == "02nd"
    assert maps._ward_ordinal(3) == "03rd"
    assert maps._ward_ordinal(7) == "07th"


def test_ward_ordinal_teens_are_th():
    assert maps._ward_ordinal(11) == "11th"
    assert maps._ward_ordinal(12) == "12th"
    assert maps._ward_ordinal(13) == "13th"


def test_ward_ordinal_two_digit():
    assert maps._ward_ordinal(21) == "21st"
    assert maps._ward_ordinal(22) == "22nd"
    assert maps._ward_ordinal(23) == "23rd"
    assert maps._ward_ordinal(47) == "47th"


# ---------------------------------------------------------------------------
# _pdf_url
# ---------------------------------------------------------------------------

def test_pdf_url_from_name():
    url = maps._pdf_url("Ward 05, Section 03", "chicago_all")
    assert url is not None
    assert url.endswith("05th-Ward-Sweeping-Schedule-2026.pdf")


def test_pdf_url_none_without_city():
    assert maps._pdf_url("Ward 05, Section 03", None) is None


def test_pdf_url_none_when_no_ward_in_name():
    assert maps._pdf_url("5th St", "chicago_all") is None


# ---------------------------------------------------------------------------
# zone_detail_html
# ---------------------------------------------------------------------------

def test_zone_detail_shows_full_year_with_pdf_link():
    code = "DATES:2026-04-17,2026-06-19,2026-07-03"
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": code, "DESC_EVEN": "stale",
        "TIME_EVEN": None,
    })
    html = _detail(row, _NOW)
    # Full year, one cluster per line (<br>); fully-past rows dimmed whole.
    assert "<br>" in html
    assert "<span class='zd-past'>Apr 17</span>" in html  # past row dimmed incl. month
    assert "Jun 19" in html and "Jul 3" in html
    assert "<span class='zd-past'>Jun 19</span>" not in html  # future row not dimmed
    assert "Street sweeping 2026:" not in html  # redundant label removed
    assert "2026 schedule" in html               # renamed PDF link
    assert "05th-Ward-Sweeping-Schedule-2026.pdf" in html
    assert "Ward 05, Section 03" in html


def test_zone_detail_all_past_rows_dimmed_whole():
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-04-17,2026-05-15",
        "DESC_EVEN": "stale", "TIME_EVEN": None,
    })
    html = _detail(row, _NOW)
    assert "<span class='zd-past'>Apr 17</span>" in html
    assert "<span class='zd-past'>May 15</span>" in html


def test_zone_detail_back_to_back_pair_on_one_line():
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-19,2026-06-20,2026-07-03",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = _detail(row, _NOW)
    assert "Jun 19, 20" in html  # consecutive days share one line


def test_zone_detail_mixed_cluster_dims_only_past_day():
    # today is Jun 6; cluster [Jun 5, Jun 6] straddles today.
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-05,2026-06-06",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = _detail(row, _NOW)
    assert "Jun <span class='zd-past'>5</span>, 6" in html


# ---------------------------------------------------------------------------
# format_schedule_side — hover shows only the next date / back-to-back cluster
# ---------------------------------------------------------------------------

def _next(code):
    from broombuster import analysis
    lines = analysis.format_schedule_side([(code, "", "")], _NOW)
    return lines[0] if lines else ""


def test_next_dates_single():
    assert _next("DATES:2026-06-19,2026-07-03") == "Jun 19"


def test_next_dates_back_to_back_pair():
    out = _next("DATES:2026-06-19,2026-06-20,2026-07-03")
    assert out == "Jun 19, 20"


def test_next_dates_clusters_a_few_days_apart():
    # Two sides swept a few days apart (Jun 13 & 16) are one occurrence.
    out = _next("DATES:2026-06-13,2026-06-16,2026-07-03")
    assert out == "Jun 13, 16"


def test_full_schedule_clusters_a_few_days_apart():
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-13,2026-06-16,2026-07-11,2026-07-14",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = _detail(row, _NOW)
    assert "Jun 13, 16" in html
    assert "Jul 11, 14" in html
    # Two separate occurrences -> two lines.
    assert html.count("<br>") >= 1


def test_next_dates_caps_at_three():
    out = _next("DATES:2026-06-18,2026-06-19,2026-06-20,2026-06-21")
    assert out == "Jun 18, 19, 20"


def test_next_dates_skips_past():
    assert _next("DATES:2026-04-01,2026-06-19") == "Jun 19"


# ---------------------------------------------------------------------------
# GET /zone/detail (tile click popup)
# ---------------------------------------------------------------------------

def test_zone_detail_endpoint_returns_same_html(app_client):
    """GET /zone/detail returns the popup HTML for a clicked tile's codes."""
    resp = app_client.get("/zone/detail", params={
        "code": "DATES:2026-06-19,2026-07-03",
        "street": "Ward 05, Section 03",
        "city": "chicago_all",
        "region": "chicago",
    })
    assert resp.status_code == 200, resp.text
    html = resp.json()["detail_html"]
    assert "Jun 19" in html and "Jul 3" in html
    assert "2026 schedule" in html
    assert "Street sweeping 2026:" not in html
    assert "05th-Ward-Sweeping-Schedule-2026.pdf" in html
    assert "Ward 05, Section 03" in html
