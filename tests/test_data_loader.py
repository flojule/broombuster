"""
Tests for data_loader normalizers and load_city_data / load_region_data.

Integration tests use the bundled / already-downloaded data files.
Unit tests use synthetic GeoDataFrames so they never hit the network.
"""
import datetime

import geopandas
import pandas as pd
import shapely.geometry

from broombuster import analysis, data_loader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_COLUMNS = {
    "STREET_NAME",
    "DAY_EVEN", "DAY_ODD",
    "DESC_EVEN", "DESC_ODD",
    "TIME_EVEN", "TIME_ODD",
    "L_F_ADD",   "L_T_ADD",
    "R_F_ADD",   "R_T_ADD",
}

_LINE = shapely.geometry.LineString([(0, 0), (1, 0)])
_POLY = shapely.geometry.Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])


def _assert_schema(gdf: geopandas.GeoDataFrame, label: str) -> None:
    missing = _REQUIRED_COLUMNS - set(gdf.columns)
    assert not missing, f"{label}: missing columns {missing}"
    assert len(gdf) > 0, f"{label}: GeoDataFrame is empty"


# ---------------------------------------------------------------------------
# Integration – load real data files (no network calls required)
# ---------------------------------------------------------------------------

class TestLoadCityDataIntegration:
    """Load real bundled / cached data files and validate the schema contract."""

    def test_oakland_schema(self):
        gdf = data_loader.load_city_data("oakland")
        _assert_schema(gdf, "oakland")

    def test_oakland_street_names_not_empty(self):
        gdf = data_loader.load_city_data("oakland")
        non_empty = gdf["STREET_NAME"].str.strip().str.len() > 0
        assert non_empty.any(), "Oakland: expected at least some non-empty STREET_NAME values"

    def test_oakland_day_codes_parseable(self):
        gdf = data_loader.load_city_data("oakland")
        codes = gdf["DAY_EVEN"].dropna().unique()
        for code in codes[:20]:  # sample to keep test fast
            result = analysis.parse_sweeping_code(str(code))
            assert isinstance(result, list), f"Oakland: parse_sweeping_code crashed on {code!r}"

    def test_san_francisco_schema(self):
        gdf = data_loader.load_city_data("san_francisco")
        _assert_schema(gdf, "san_francisco")

    def test_san_francisco_side_split(self):
        gdf = data_loader.load_city_data("san_francisco")
        # EVEN rows should have DAY_EVEN set; ODD rows should have DAY_ODD set
        even_rows = gdf[gdf["DAY_EVEN"].notna() & gdf["DAY_ODD"].isna()]
        odd_rows  = gdf[gdf["DAY_ODD"].notna()  & gdf["DAY_EVEN"].isna()]
        both_rows = gdf[gdf["DAY_EVEN"].notna()  & gdf["DAY_ODD"].notna()]
        assert len(even_rows) + len(odd_rows) + len(both_rows) > 0

    def test_san_francisco_day_codes_parseable(self):
        gdf = data_loader.load_city_data("san_francisco")
        codes = gdf["DAY_EVEN"].dropna().unique()
        for code in codes[:20]:
            result = analysis.parse_sweeping_code(str(code))
            assert isinstance(result, list), f"SF: parse_sweeping_code crashed on {code!r}"

    def test_berkeley_schema(self):
        gdf = data_loader.load_city_data("berkeley")
        _assert_schema(gdf, "berkeley")

    def test_alameda_schema(self):
        gdf = data_loader.load_city_data("alameda")
        _assert_schema(gdf, "alameda")

    def test_chicago_schema(self):
        gdf = data_loader.load_city_data("chicago_all")
        _assert_schema(gdf, "chicago_all")

    def test_chicago_street_name_format(self):
        gdf = data_loader.load_city_data("chicago_all")
        sample = gdf["STREET_NAME"].dropna().head(20)
        assert all(s.startswith("Ward ") for s in sample), \
            "Chicago STREET_NAME should start with 'Ward '"

    def test_chicago_day_codes_parseable(self):
        gdf = data_loader.load_city_data("chicago_all")
        codes = gdf["DAY_EVEN"].dropna().unique()
        for code in codes[:10]:
            result = analysis.parse_sweeping_code(str(code))
            assert isinstance(result, list), f"Chicago: parse_sweeping_code crashed on {code!r}"
            assert len(result) > 0, f"Chicago: code {code!r} parsed to empty list"

    def test_chicago_even_odd_identical(self):
        """Chicago zones apply the same schedule to every address, so DAY_EVEN == DAY_ODD."""
        gdf = data_loader.load_city_data("chicago_all")
        rows_with_data = gdf[gdf["DAY_EVEN"].notna()]
        assert (rows_with_data["DAY_EVEN"] == rows_with_data["DAY_ODD"]).all()


class TestLoadRegionData:
    def test_bay_area_schema(self):
        gdf = data_loader.load_region_data("bay_area")
        _assert_schema(gdf, "bay_area region")

    def test_bay_area_city_column(self):
        gdf = data_loader.load_region_data("bay_area")
        assert "_city" in gdf.columns
        assert set(gdf["_city"].unique()) >= {"oakland"}

    def test_bay_area_crs(self):
        gdf = data_loader.load_region_data("bay_area")
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == 4326

    def test_chicago_region_schema(self):
        gdf = data_loader.load_region_data("chicago")
        _assert_schema(gdf, "chicago region")

    def test_chicago_region_city_column(self):
        gdf = data_loader.load_region_data("chicago")
        assert "_city" in gdf.columns
        assert "chicago_all" in gdf["_city"].unique()


# ---------------------------------------------------------------------------
# Unit – SF normalizer with a synthetic GeoDataFrame
# ---------------------------------------------------------------------------

def _make_sf_gdf(rows: list[dict]) -> geopandas.GeoDataFrame:
    """Build a minimal DataSF-shaped GeoDataFrame from a list of row dicts."""
    df = pd.DataFrame(rows)
    df["geometry"] = _LINE
    return geopandas.GeoDataFrame(df, crs="EPSG:4326")


class TestNormaliseSF:
    def test_even_side_only_sets_day_even(self):
        gdf = _make_sf_gdf([{
            "corridor": "MARKET ST", "blockside": "EVEN",
            "week_day": "1", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 0,
            "week_3_of_month": 1, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["DAY_EVEN"].iloc[0] == "M13"
        assert pd.isna(out["DAY_ODD"].iloc[0])

    def test_odd_side_only_sets_day_odd(self):
        gdf = _make_sf_gdf([{
            "corridor": "MARKET ST", "blockside": "ODD",
            "week_day": "5", "from_hour": 9, "to_hour": 11,
            "week_1_of_month": 0, "week_2_of_month": 1,
            "week_3_of_month": 0, "week_4_of_month": 1, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["DAY_ODD"].iloc[0] == "F24"
        assert pd.isna(out["DAY_EVEN"].iloc[0])

    def test_both_side_sets_both_columns(self):
        gdf = _make_sf_gdf([{
            "corridor": "VALENCIA ST", "blockside": "BOTH",
            "week_day": "3", "from_hour": 7, "to_hour": 9,
            "week_1_of_month": 1, "week_2_of_month": 1,
            "week_3_of_month": 1, "week_4_of_month": 1, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["DAY_EVEN"].iloc[0] == "WE"
        assert out["DAY_ODD"].iloc[0] == "WE"

    def test_every_week_flags_produce_E_suffix(self):
        gdf = _make_sf_gdf([{
            "corridor": "HAYES ST", "blockside": "EVEN",
            "week_day": "4", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 1,
            "week_3_of_month": 1, "week_4_of_month": 1, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["DAY_EVEN"].iloc[0] == "THE"

    def test_no_flags_produces_E_suffix(self):
        gdf = _make_sf_gdf([{
            "corridor": "PINE ST", "blockside": "EVEN",
            "week_day": "2", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 0, "week_2_of_month": 0,
            "week_3_of_month": 0, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["DAY_EVEN"].iloc[0] == "TE"

    def test_time_string_format(self):
        gdf = _make_sf_gdf([{
            "corridor": "OAK ST", "blockside": "EVEN",
            "week_day": "1", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 0,
            "week_3_of_month": 0, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["TIME_EVEN"].iloc[0] == "8AM–10AM"

    def test_street_name_uppercased(self):
        gdf = _make_sf_gdf([{
            "corridor": "market st", "blockside": "EVEN",
            "week_day": "1", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 0,
            "week_3_of_month": 0, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert out["STREET_NAME"].iloc[0] == "MARKET ST"

    def test_unknown_day_produces_null_code(self):
        gdf = _make_sf_gdf([{
            "corridor": "UNKNOWN ST", "blockside": "EVEN",
            "week_day": "99", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 0,
            "week_3_of_month": 0, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        assert pd.isna(out["DAY_EVEN"].iloc[0])

    def test_schema_contract(self):
        gdf = _make_sf_gdf([{
            "corridor": "TEST ST", "blockside": "BOTH",
            "week_day": "1", "from_hour": 8, "to_hour": 10,
            "week_1_of_month": 1, "week_2_of_month": 0,
            "week_3_of_month": 0, "week_4_of_month": 0, "week_5_of_month": 0,
        }])
        out = data_loader._normalise_sf(gdf)
        _assert_schema(out, "_normalise_sf")


# ---------------------------------------------------------------------------
# Unit – Chicago normalizer with a synthetic GeoDataFrame
# ---------------------------------------------------------------------------

def _make_chicago_gdf(rows: list[dict]) -> geopandas.GeoDataFrame:
    df = pd.DataFrame(rows)
    df["geometry"] = _POLY
    return geopandas.GeoDataFrame(df, crs="EPSG:4326")


class TestNormaliseChicago:
    def test_street_name_ward_section_format(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18", "may": "15,16"}])
        out = data_loader._normalise_chicago(gdf)
        assert out["STREET_NAME"].iloc[0] == "Ward 05, Section 03"

    def test_street_name_zero_padded(self):
        gdf = _make_chicago_gdf([{"ward": 1, "section": 1, "april": "1"}])
        out = data_loader._normalise_chicago(gdf)
        assert out["STREET_NAME"].iloc[0] == "Ward 01, Section 01"

    def test_day_code_starts_with_DATES(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18", "may": "15,16"}])
        out = data_loader._normalise_chicago(gdf)
        code = out["DAY_EVEN"].iloc[0]
        assert isinstance(code, str) and code.startswith("DATES:")

    def test_day_code_contains_valid_iso_dates(self):
        # Year is inferred from the data, so assert the month/day are present
        # regardless of which calendar year inference selects.
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17", "may": "15"}])
        out = data_loader._normalise_chicago(gdf)
        code = out["DAY_EVEN"].iloc[0]
        assert "-04-17" in code
        assert "-05-15" in code

    def test_day_code_parseable_by_analysis(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18", "may": "15,16"}])
        out = data_loader._normalise_chicago(gdf)
        code = out["DAY_EVEN"].iloc[0]
        result = analysis.parse_sweeping_code(code)
        # Year-agnostic: every requested month/day is expanded, and inference
        # lands them all on weekdays (Mon-Fri), never weekends.
        md = {(d.month, d.day) for d in result}
        assert {(4, 17), (4, 18), (5, 15), (5, 16)} <= md
        assert all(d.weekday() < 5 for d in result)

    def test_even_odd_identical(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18"}])
        out = data_loader._normalise_chicago(gdf)
        assert out["DAY_EVEN"].iloc[0] == out["DAY_ODD"].iloc[0]

    def test_empty_schedule_produces_null_code(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3}])
        out = data_loader._normalise_chicago(gdf)
        assert pd.isna(out["DAY_EVEN"].iloc[0])

    def test_invalid_day_number_skipped(self):
        gdf = _make_chicago_gdf([{"ward": 1, "section": 1, "april": "99,17"}])
        out = data_loader._normalise_chicago(gdf)
        code = out["DAY_EVEN"].iloc[0]
        # day 99 is invalid so it should be absent; day 17 should be present
        assert "-04-17" in code
        assert "-04-99" not in code

    def test_desc_contains_month_abbreviation(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18", "may": "15,16"}])
        out = data_loader._normalise_chicago(gdf)
        desc = out["DESC_EVEN"].iloc[0]
        assert desc and ("Apr" in desc or "May" in desc)

    def test_schema_contract(self):
        gdf = _make_chicago_gdf([{"ward": 5, "section": 3, "april": "17,18"}])
        out = data_loader._normalise_chicago(gdf)
        _assert_schema(out, "_normalise_chicago")


def _weekday_pairs_for(year: int) -> list:
    """(month, day) pairs that all fall on weekdays in `year` (Apr-Jun sample)."""
    import calendar
    pairs = []
    for m in (4, 5, 6):
        for d in range(1, calendar.monthrange(year, m)[1] + 1):
            if datetime.date(year, m, d).weekday() < 5:
                pairs.append((m, d))
    return pairs


class TestInferChicagoYear:
    def test_picks_year_with_weekday_alignment(self):
        # Days that are all weekdays in 2025 (Wed-start) should resolve to 2025
        # even when the reference year is 2026 (2025 is in the ±1 window).
        pairs = _weekday_pairs_for(2025)
        assert data_loader._infer_chicago_year(pairs, 2026) == 2025

    def test_prefers_ref_year_on_tie(self):
        # A single mid-week date is a weekday in several adjacent years; ties
        # resolve to the reference year.
        pairs = [(6, 15)]  # Jun 15: weekday in 2026 (Mon) — also other years
        assert data_loader._infer_chicago_year(pairs, 2026) == 2026

    def test_empty_pairs_returns_ref_year(self):
        assert data_loader._infer_chicago_year([], 2026) == 2026

    def test_raises_when_no_recent_year_fits(self):
        # Every day of a month always includes ~2/7 weekend days in any year,
        # so no candidate year clears the threshold -> fail fast, not silent.
        import pytest
        pairs = [(4, d) for d in range(1, 31)]  # all of April
        with pytest.raises(ValueError, match="stale"):
            data_loader._infer_chicago_year(pairs, 2026)


# ---------------------------------------------------------------------------
# Unit – Oakland normalizer with a synthetic GeoDataFrame
# ---------------------------------------------------------------------------

class TestNormaliseOakland:
    def _make_gdf(self, rows):
        df = pd.DataFrame(rows)
        df["geometry"] = _LINE
        return geopandas.GeoDataFrame(df, crs="EPSG:4326")

    def test_street_name_combines_name_and_type(self):
        gdf = self._make_gdf([{
            "NAME": "TELEGRAPH", "TYPE": "AVE",
            "DAY_EVEN": "ME", "DAY_ODD": "ME",
            "L_F_ADD": 1000, "L_T_ADD": 1100,
            "R_F_ADD": 1001, "R_T_ADD": 1099,
        }])
        out = data_loader._normalise_oakland(gdf)
        assert out["STREET_NAME"].iloc[0] == "TELEGRAPH AVE"

    def test_schema_contract(self):
        gdf = self._make_gdf([{
            "NAME": "BROADWAY", "TYPE": "ST",
            "DAY_EVEN": "WE", "DAY_ODD": "WE",
            "L_F_ADD": 1, "L_T_ADD": 99, "R_F_ADD": 2, "R_T_ADD": 98,
        }])
        out = data_loader._normalise_oakland(gdf)
        _assert_schema(out, "_normalise_oakland")


# ---------------------------------------------------------------------------
# Unit – prebuilt normalizer (Berkeley / Alameda)
# ---------------------------------------------------------------------------

class TestNormalisePrebuilt:
    def _make_gdf(self, rows):
        df = pd.DataFrame(rows)
        df["geometry"] = _LINE
        return geopandas.GeoDataFrame(df, crs="EPSG:4326")

    def test_passthrough_with_all_columns_present(self):
        gdf = self._make_gdf([{
            "STREET_NAME": "SHATTUCK AVE",
            "DAY_EVEN": "ME", "DAY_ODD": "TE",
            "DESC_EVEN": "Every Mon, 8AM–10AM", "DESC_ODD": "Every Tue, 8AM–10AM",
            "TIME_EVEN": "8AM–10AM", "TIME_ODD": "8AM–10AM",
            "L_F_ADD": 1000, "L_T_ADD": 1100,
            "R_F_ADD": 1001, "R_T_ADD": 1099,
        }])
        out = data_loader._normalise_prebuilt(gdf)
        assert out["STREET_NAME"].iloc[0] == "SHATTUCK AVE"
        assert out["DAY_EVEN"].iloc[0] == "ME"

    def test_missing_columns_filled_with_none(self):
        gdf = self._make_gdf([{"STREET_NAME": "PARK ST"}])
        out = data_loader._normalise_prebuilt(gdf)
        for col in ("DAY_EVEN", "DAY_ODD", "DESC_EVEN", "DESC_ODD", "TIME_EVEN", "TIME_ODD"):
            assert col in out.columns
            assert pd.isna(out[col].iloc[0])

    def test_schema_contract(self):
        gdf = self._make_gdf([{
            "STREET_NAME": "PARK ST",
            "DAY_EVEN": "FE", "DAY_ODD": "FE",
            "DESC_EVEN": "Fri", "DESC_ODD": "Fri",
            "TIME_EVEN": "8AM–10AM", "TIME_ODD": "8AM–10AM",
            "L_F_ADD": 1, "L_T_ADD": 99, "R_F_ADD": 2, "R_T_ADD": 98,
        }])
        _assert_schema(data_loader._normalise_prebuilt(gdf), "_normalise_prebuilt")
