"""
End-to-end pipeline tests for the LEGACY CLI path (src/main.py).

Full stack exercised per test:
  load_region_data  →  to_crs("EPSG:3857")  →  check_street_sweeping
  →  check_day_street_sweeping  →  compose_message

`analysis.check_street_sweeping` is the older, name-index-based resolver.
The HTTP /check endpoint uses `resolve.resolve_car_segment` instead, so
these tests cover a parallel code path that is currently used only by the
CLI in `src/main.py`. They remain useful for verifying that `analysis.py`
still produces sensible per-side schedules and time-window urgency.

No network calls are made: street info is injected directly onto the Car
object so the cached-info branch of check_street_sweeping is taken.
For Chicago the polygon-zone fallback is triggered automatically because
no Chicago zone is named after a street.
"""
import datetime

import pytest

from broombuster import analysis
from broombuster import car as car_module
from broombuster import data_loader
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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_car(lat, lon, street_name, street_number, city_key):
    """Build a Car with pre-populated street info to avoid any network calls."""
    c = car_module.Car(lat=lat, lon=lon)
    c.street_name   = street_name
    c.street_number = street_number
    # streets must be a non-empty list so check_street_sweeping takes the
    # cached-info branch instead of calling GPS/geocoding APIs.
    c.streets = [(street_name, 5.0)]
    c._city = city_key
    return c


# ---------------------------------------------------------------------------
# Bay Area region — full pipeline
# ---------------------------------------------------------------------------

class TestBayAreaPipeline:
    """Pipeline tests using a known Oakland address (2931 Chestnut St)."""

    LAT, LON       = 37.821326, -122.280705   # 2931 Chestnut St, Oakland
    STREET, NUMBER = "CHESTNUT ST", 2931       # odd number → odd side
    CITY           = "oakland"

    # -- Return type contract ------------------------------------------------

    def test_returns_four_tuple(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        result = analysis.check_street_sweeping(myCar, bay_area_3857)
        assert isinstance(result, tuple) and len(result) == 4

    def test_schedule_is_list(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, bay_area_3857)
        assert isinstance(schedule, list)

    def test_schedule_even_odd_are_lists(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, bay_area_3857)
        assert isinstance(schedule_even, list)
        assert isinstance(schedule_odd, list)

    def test_message_is_string(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        *_, message = analysis.check_street_sweeping(myCar, bay_area_3857)
        assert isinstance(message, str)

    # -- Data correctness ----------------------------------------------------

    def test_schedule_found_at_known_address(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, bay_area_3857)
        assert schedule_even or schedule_odd, (
            f"Expected a schedule at {self.NUMBER} {self.STREET} (Oakland)"
        )

    def test_schedule_entries_have_three_fields(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, bay_area_3857)
        for entry in schedule_even + schedule_odd:
            assert len(entry) >= 3, f"Schedule entry should be (code, desc, time), got {entry!r}"

    def test_schedule_codes_parseable(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, bay_area_3857)
        for entry in schedule:
            result = analysis.parse_sweeping_code(entry[0])
            assert isinstance(result, list), f"parse_sweeping_code failed on {entry[0]!r}"

    def test_schedule_codes_yield_dates(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, bay_area_3857)
        for entry in schedule:
            dates = analysis.parse_sweeping_code(entry[0])
            assert all(isinstance(d, datetime.date) for d in dates)

    def test_schedule_is_union_of_both_sides(self, bay_area_3857):
        """schedule must be the union of both sides so the car is warned
        regardless of which side of the street is being swept."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )
        expected = list(set(schedule_even) | set(schedule_odd))
        assert set(schedule) == set(expected)

    # -- check_day_street_sweeping -------------------------------------------

    def test_check_day_returns_valid_urgency(self, bay_area_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, bay_area_3857)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False or urgency in ("today", "tomorrow")

    def test_check_day_consistent_with_parsed_dates(self, bay_area_3857):
        """If urgency is 'today', today must appear in the parsed schedule dates."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, bay_area_3857)
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
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, message = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )
        car_side = "odd"   # 2931 is odd
        recomposed = compose_message(schedule_even, schedule_odd, car_side)
        assert recomposed == message

    # -- SF sub-test (different city, same region GDF) -----------------------

    def test_sf_street_found_in_region(self, bay_area_3857):
        """SF coordinates within the same bay_area GDF should also resolve."""
        myCar = _make_car(37.7597, -122.4212, "GUERRERO ST", 450, "san_francisco")
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )
        assert schedule_even or schedule_odd, (
            "Expected a schedule for 450 Guerrero St, San Francisco"
        )


# ---------------------------------------------------------------------------
# Chicago region — full pipeline (polygon-zone fallback path)
# ---------------------------------------------------------------------------

class TestChicagoPipeline:
    """Pipeline tests using Rogers Park coordinates (inside a ward section zone)."""

    LAT, LON       = 41.996593, -87.665282    # near N Glenwood Ave, Rogers Park
    STREET, NUMBER = "N GLENWOOD AVE", 1616   # even number → even side
    CITY           = "chicago_all"

    # The street name won't match any "Ward XX, Section XX" entry in the index,
    # so check_street_sweeping automatically falls through to the polygon
    # containment check — which is the normal Chicago code path.

    # -- Return type contract ------------------------------------------------

    def test_returns_four_tuple(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        result = analysis.check_street_sweeping(myCar, chicago_3857)
        assert isinstance(result, tuple) and len(result) == 4

    def test_schedule_is_list(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, chicago_3857)
        assert isinstance(schedule, list)

    def test_message_is_string(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        *_, message = analysis.check_street_sweeping(myCar, chicago_3857)
        assert isinstance(message, str)

    # -- Polygon fallback correctness ----------------------------------------

    def test_schedule_found_via_polygon_fallback(self, chicago_3857):
        """Car must land inside a ward section polygon."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, chicago_3857)
        assert schedule_even or schedule_odd, (
            f"Polygon fallback found no Chicago zone at {self.LAT}, {self.LON}"
        )

    def test_schedule_codes_start_with_DATES(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, chicago_3857)
        for entry in schedule_even + schedule_odd:
            assert entry[0].startswith("DATES:"), (
                f"Chicago schedule code should start with 'DATES:', got {entry[0]!r}"
            )

    def test_schedule_dates_in_april_to_november(self, chicago_3857):
        """Chicago sweeping season is April–November; all dates must fall in that window."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, chicago_3857)
        for entry in schedule_even + schedule_odd:
            dates = analysis.parse_sweeping_code(entry[0])
            assert dates, f"Expected non-empty date list for code {entry[0]!r}"
            for d in dates:
                assert 4 <= d.month <= 11, (
                    f"Chicago sweep date {d} is outside the April–November window"
                )

    def test_schedule_codes_parseable_to_dates(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, chicago_3857)
        for entry in schedule:
            dates = analysis.parse_sweeping_code(entry[0])
            assert isinstance(dates, list)
            assert all(isinstance(d, datetime.date) for d in dates)

    def test_even_odd_schedules_identical(self, chicago_3857):
        """Chicago zones apply the same dates to all addresses."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(myCar, chicago_3857)
        assert schedule_even == schedule_odd

    def test_correct_side_for_even_number(self, chicago_3857):
        """1616 is even — schedule should draw from the even side."""
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, chicago_3857
        )
        assert schedule == schedule_even

    # -- check_day_street_sweeping -------------------------------------------

    def test_check_day_returns_valid_urgency(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, chicago_3857)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False or urgency in ("today", "tomorrow")

    def test_check_day_false_outside_april_november(self, chicago_3857):
        """Outside the sweeping season urgency must be False."""
        today = datetime.date.today()
        if 4 <= today.month <= 11:
            pytest.skip("Today is within Chicago's sweeping season — skipping off-season check")
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, chicago_3857)
        urgency = analysis.check_day_street_sweeping(schedule)
        assert urgency is False, (
            f"Expected False outside sweeping season (month {today.month}), got {urgency!r}"
        )

    def test_check_day_consistent_with_parsed_dates(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        schedule, *_ = analysis.check_street_sweeping(myCar, chicago_3857)
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
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, message = analysis.check_street_sweeping(
            myCar, chicago_3857
        )
        car_side = "even"  # 1616 is even
        recomposed = compose_message(schedule_even, schedule_odd, car_side)
        assert recomposed == message

    def test_message_non_empty_when_schedule_found(self, chicago_3857):
        myCar = _make_car(self.LAT, self.LON, self.STREET, self.NUMBER, self.CITY)
        _, schedule_even, schedule_odd, message = analysis.check_street_sweeping(
            myCar, chicago_3857
        )
        if schedule_even or schedule_odd:
            assert len(message.strip()) > 0
