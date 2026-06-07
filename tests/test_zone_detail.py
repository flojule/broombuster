"""Tests for Chicago zone click-detail: full upcoming schedule + ward PDF link."""
import datetime
import os

os.environ.setdefault("DEV_MODE", "1")

import geopandas
import pandas as pd
import shapely.geometry as sgeom

from broombuster import maps

_NOW = datetime.datetime(2026, 6, 6, 9, 0)


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
# _zone_pdf_url
# ---------------------------------------------------------------------------

def test_zone_pdf_url_from_name():
    row = pd.Series({"_city": "chicago_all", "STREET_DISPLAY": "Ward 05, Section 03"})
    url = maps._zone_pdf_url(row)
    assert url is not None
    assert url.endswith("05th-Ward-Sweeping-Schedule-2026.pdf")


def test_zone_pdf_url_none_without_city():
    row = pd.Series({"STREET_DISPLAY": "Ward 05, Section 03"})
    assert maps._zone_pdf_url(row) is None


def test_zone_pdf_url_none_when_no_ward_in_name():
    row = pd.Series({"_city": "chicago_all", "STREET_DISPLAY": "5th St"})
    assert maps._zone_pdf_url(row) is None


# ---------------------------------------------------------------------------
# _zone_detail
# ---------------------------------------------------------------------------

def test_zone_detail_shows_full_year_with_pdf_link():
    code = "DATES:2026-04-17,2026-06-19,2026-07-03"
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": code, "DESC_EVEN": "stale",
        "TIME_EVEN": None,
    })
    html = maps._zone_detail(row, _NOW)
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
    html = maps._zone_detail(row, _NOW)
    assert "<span class='zd-past'>Apr 17</span>" in html
    assert "<span class='zd-past'>May 15</span>" in html


def test_zone_detail_back_to_back_pair_on_one_line():
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-19,2026-06-20,2026-07-03",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = maps._zone_detail(row, _NOW)
    assert "Jun 19, 20" in html  # consecutive days share one line


def test_zone_detail_mixed_cluster_dims_only_past_day():
    # today is Jun 6; cluster [Jun 5, Jun 6] straddles today.
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-05,2026-06-06",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = maps._zone_detail(row, _NOW)
    assert "Jun <span class='zd-past'>5</span>, 6" in html


# ---------------------------------------------------------------------------
# next_dates_desc — hover shows only the next date / back-to-back cluster
# ---------------------------------------------------------------------------

def test_next_dates_single():
    from broombuster import analysis
    assert analysis.next_dates_desc("DATES:2026-06-19,2026-07-03", _NOW) == "Jun 19"


def test_next_dates_back_to_back_pair():
    from broombuster import analysis
    out = analysis.next_dates_desc("DATES:2026-06-19,2026-06-20,2026-07-03", _NOW)
    assert out == "Jun 19, 20"


def test_next_dates_clusters_a_few_days_apart():
    # Two sides swept a few days apart (Jun 13 & 16) are one occurrence.
    from broombuster import analysis
    out = analysis.next_dates_desc("DATES:2026-06-13,2026-06-16,2026-07-03", _NOW)
    assert out == "Jun 13, 16"


def test_full_schedule_clusters_a_few_days_apart():
    row = pd.Series({
        "_city": "chicago_all",
        "STREET_DISPLAY": "Ward 05, Section 03",
        "DAY_EVEN": "DATES:2026-06-13,2026-06-16,2026-07-11,2026-07-14",
        "DESC_EVEN": "", "TIME_EVEN": None,
    })
    html = maps._zone_detail(row, _NOW)
    assert "Jun 13, 16" in html
    assert "Jul 11, 14" in html
    # Two separate occurrences -> two lines.
    assert html.count("<br>") >= 1


def test_next_dates_caps_at_three():
    from broombuster import analysis
    out = analysis.next_dates_desc(
        "DATES:2026-06-18,2026-06-19,2026-06-20,2026-06-21", _NOW)
    assert out == "Jun 18, 19, 20"


def test_next_dates_skips_past():
    from broombuster import analysis
    assert analysis.next_dates_desc("DATES:2026-04-01,2026-06-19", _NOW) == "Jun 19"


# ---------------------------------------------------------------------------
# build_map_geojson: polygon features carry detail_html
# ---------------------------------------------------------------------------

class _Car:
    lat = 41.9
    lon = -87.66
    street_name = ""
    _city = "chicago_all"


def test_zone_detail_endpoint_returns_same_html():
    """GET /zone/detail (PMTILES mode) returns the same popup HTML as _zone_detail."""
    from fastapi.testclient import TestClient

    from broombuster.api import app as api_mod

    with TestClient(api_mod.app) as client:
        resp = client.get("/zone/detail", params={
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


def test_polygon_feature_includes_detail_html():
    poly = sgeom.Polygon([(-87.67, 41.90), (-87.66, 41.90),
                          (-87.66, 41.91), (-87.67, 41.91)])
    gdf = geopandas.GeoDataFrame(
        [{
            "_city": "chicago_all",
            "STREET_NAME": "Ward 05, Section 03",
            "STREET_DISPLAY": "Ward 05, Section 03",
            "DAY_EVEN": "DATES:2026-06-19,2026-07-03",
            "DAY_ODD": "DATES:2026-06-19,2026-07-03",
            "DESC_EVEN": "x", "DESC_ODD": "x",
            "TIME_EVEN": None, "TIME_ODD": None,
            "geometry": poly,
        }],
        crs="EPSG:4326",
    )
    gj = maps.build_map_geojson(_Car(), gdf, local_now=_NOW)
    polys = [f for f in gj["features"] if f["properties"]["render_type"] == "polygon"]
    assert len(polys) == 1
    props = polys[0]["properties"]
    assert "detail_html" in props
    assert "Jun 19" in props["detail_html"]
    assert "05th-Ward-Sweeping-Schedule-2026.pdf" in props["detail_html"]
