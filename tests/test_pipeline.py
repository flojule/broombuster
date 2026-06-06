"""
End-to-end pipeline tests for the programmatic resolver path.

Full stack exercised per test:
  load_region_data → to_crs("EPSG:3857") → analysis.analyze_car
  → check_day_street_sweeping → compose_message

`analysis.analyze_car` wraps `resolve.resolve_car_segment` plus
`schedules_for_all_matching_rows`/`compose_message` — the same resolver the
HTTP /check endpoint and the CLI use, so there is one code path. The car's
side is geometric (resolved.side), not house-number parity.

No network calls: the resolver works purely on the in-memory GeoDataFrame.
For Chicago the polygon-zone containment path is exercised automatically.
"""
import datetime

import pytest

from broombuster import analysis, data_loader, resolve
from broombuster.domains.sweeping import compose_message

# ---------------------------------------------------------------------------
# Module-scoped fixtures — region data loaded once for the whole module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bay_area_gdf():
    return data_loader.load_region_data("bay_area")


@pytest.fixture(scope="module")
def bay_area_3857(bay_area_gdf):
    return bay_area_gdf.to_crs("EPSG:3857")


@pytest.fixture(scope="module")
def chicago_gdf():
    return data_loader.load_region_data("chicago")


@pytest.fixture(scope="module")
def chicago_3857(chicago_gdf):
    return chicago_gdf.to_crs("EPSG:3857")


def _resolved_side(gdf_3857, lat, lon, city):
    try:
        r = resolve.resolve_car_segment(gdf_3857, lat, lon, city_key=city, max_distance_m=50.0)
    except resolve.NoSegmentNearby:
        return "odd"
    return r.side or "odd"


# ---------------------------------------------------------------------------
# Bay Area region — full pipeline
# ---------------------------------------------------------------------------

class TestBayAreaPipeline:
    """Pipeline tests using a known Oakland address (2931 Chestnut St)."""

    LAT, LON = 37.821326, -122.280705   # 2931 Chestnut St, Oakland
    CITY     = "oakland"

    # -- Return type contract ------------------------------------------------

    def test_returns_four_tuple(self, bay_area_3857):
        result = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(result, tuple) and len(result) == 4

    def test_schedule_is_list(self, bay_area_3857):
        schedule, *_ = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(schedule, list)

    def test_schedule_even_odd_are_lists(self, bay_area_3857):
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, self.LAT, self.LON, city_key=self.CITY
        )
        assert isinstance(schedule_even, list)
        assert isinstance(schedule_odd, list)

    def test_message_is_string(self, bay_area_3857):
        *_, message = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(message, str)

    # -- Data correctness ----------------------------------------------------

    def test_schedule_found_at_known_address(self, bay_area_3857):
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, self.LAT, self.LON, city_key=self.CITY
        )
        assert schedule_even or schedule_odd, (
            "Expected a schedule near 2931 Chestnut St (Oakland)"
        )

    def test_schedule_entries_have_three_fields(self, bay_area_3857):
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, self.LAT, self.LON, city_key=self.CITY
        )
        for entry in schedule_even + schedule_odd:
            assert len(entry) >= 3, f"Schedule entry should be (code, desc, time), got {entry!r}"

    def test_schedule_codes_parseable(self, bay_area_3857):
        schedule, *_ = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        for entry in schedule:
            result = analysis.parse_sweeping_code(entry[0])
            assert isinstance(result, list), f"parse_sweeping_code failed on {entry[0]!r}"

    def test_schedule_codes_yield_dates(self, bay_area_3857):
        schedule, *_ = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        for entry in schedule:
            dates = analysis.parse_sweeping_code(entry[0])
            assert all(isinstance(d, datetime.date) for d in dates)

    def test_schedule_is_union_of_both_sides(self, bay_area_3857):
        """schedule must be the union of both sides so the car is warned
        regardless of which side of the street is being swept."""
        schedule, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, self.LAT, self.LON, city_key=self.CITY
        )
        expected = set(schedule_even) | set(schedule_odd)
        assert set(schedule) == expected

    # -- check_day_street_sweeping -------------------------------------------

    def test_check_day_returns_valid_urgency(self, bay_area_3857):
        schedule, *_ = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False or urgency in ("today", "tomorrow")

    def test_check_day_consistent_with_parsed_dates(self, bay_area_3857):
        """If urgency is 'today', today must appear in the parsed schedule dates."""
        schedule, *_ = analysis.analyze_car(bay_area_3857, self.LAT, self.LON, city_key=self.CITY)
        urgency = analysis.check_day_street_sweeping(schedule)
        all_dates = {d for entry in schedule for d in analysis.parse_sweeping_code(entry[0])}
        today     = datetime.date.today()
        tomorrow  = today + datetime.timedelta(days=1)
        if urgency == "today":
            assert today in all_dates
        elif urgency == "tomorrow":
            assert tomorrow in all_dates
            assert today not in all_dates

    # -- compose_message / notification --------------------------------------

    def test_compose_message_matches_pipeline_message(self, bay_area_3857):
        _, schedule_even, schedule_odd, message = analysis.analyze_car(
            bay_area_3857, self.LAT, self.LON, city_key=self.CITY
        )
        car_side = _resolved_side(bay_area_3857, self.LAT, self.LON, self.CITY)
        assert compose_message(schedule_even, schedule_odd, car_side) == message

    # -- SF sub-test (different city, same region GDF) -----------------------

    def test_sf_street_found_in_region(self, bay_area_3857):
        """SF coordinates within the same bay_area GDF should also resolve."""
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, 37.7597, -122.4212, city_key="san_francisco"
        )
        assert schedule_even or schedule_odd, (
            "Expected a schedule near 450 Guerrero St, San Francisco"
        )


# ---------------------------------------------------------------------------
# Chicago region — full pipeline (polygon-zone containment path)
# ---------------------------------------------------------------------------

class TestChicagoPipeline:
    """Pipeline tests using Rogers Park coordinates (inside a ward section zone)."""

    LAT, LON = 41.996593, -87.665282    # near N Glenwood Ave, Rogers Park
    CITY     = "chicago_all"

    # -- Return type contract ------------------------------------------------

    def test_returns_four_tuple(self, chicago_3857):
        result = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(result, tuple) and len(result) == 4

    def test_schedule_is_list(self, chicago_3857):
        schedule, *_ = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(schedule, list)

    def test_message_is_string(self, chicago_3857):
        *_, message = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        assert isinstance(message, str)

    # -- Polygon containment correctness -------------------------------------

    def test_schedule_found_via_polygon_zone(self, chicago_3857):
        """Car must land inside a ward section polygon."""
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        assert schedule_even or schedule_odd, (
            f"No Chicago zone resolved at {self.LAT}, {self.LON}"
        )

    def test_schedule_codes_start_with_DATES(self, chicago_3857):
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        for entry in schedule_even + schedule_odd:
            assert entry[0].startswith("DATES:"), (
                f"Chicago schedule code should start with 'DATES:', got {entry[0]!r}"
            )

    def test_schedule_dates_in_april_to_november(self, chicago_3857):
        """Chicago sweeping season is April–November; all dates must fall in that window."""
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        for entry in schedule_even + schedule_odd:
            dates = analysis.parse_sweeping_code(entry[0])
            assert dates, f"Expected non-empty date list for code {entry[0]!r}"
            for d in dates:
                assert 4 <= d.month <= 11, (
                    f"Chicago sweep date {d} is outside the April–November window"
                )

    def test_schedule_codes_parseable_to_dates(self, chicago_3857):
        schedule, *_ = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        for entry in schedule:
            dates = analysis.parse_sweeping_code(entry[0])
            assert isinstance(dates, list)
            assert all(isinstance(d, datetime.date) for d in dates)

    def test_zone_schedule_same_both_sides(self, chicago_3857):
        """Chicago zones apply the same dates to all addresses (even == odd)."""
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        assert schedule_even == schedule_odd

    # -- check_day_street_sweeping -------------------------------------------

    def test_check_day_returns_valid_urgency(self, chicago_3857):
        schedule, *_ = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False or urgency in ("today", "tomorrow")

    def test_check_day_false_outside_april_november(self, chicago_3857):
        """Outside the sweeping season urgency must be False."""
        today = datetime.date.today()
        if 4 <= today.month <= 11:
            pytest.skip("Today is within Chicago's sweeping season — skipping off-season check")
        schedule, *_ = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False, (
            f"Expected False outside sweeping season (month {today.month}), got {urgency!r}"
        )

    def test_check_day_consistent_with_parsed_dates(self, chicago_3857):
        schedule, *_ = analysis.analyze_car(chicago_3857, self.LAT, self.LON, city_key=self.CITY)
        urgency = analysis.check_day_street_sweeping(schedule)
        all_dates = {d for entry in schedule for d in analysis.parse_sweeping_code(entry[0])}
        today    = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        if urgency == "today":
            assert today in all_dates
        elif urgency == "tomorrow":
            assert tomorrow in all_dates
            assert today not in all_dates

    # -- compose_message / notification --------------------------------------

    def test_compose_message_matches_pipeline_message(self, chicago_3857):
        _, schedule_even, schedule_odd, message = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        car_side = _resolved_side(chicago_3857, self.LAT, self.LON, self.CITY)
        assert compose_message(schedule_even, schedule_odd, car_side) == message

    def test_message_non_empty_when_schedule_found(self, chicago_3857):
        _, schedule_even, schedule_odd, message = analysis.analyze_car(
            chicago_3857, self.LAT, self.LON, city_key=self.CITY
        )
        if schedule_even or schedule_odd:
            assert len(message.strip()) > 0
