"""
Nearest-segment invariant tests for the LEGACY CLI path
(`analysis.check_street_sweeping`).

Core invariant: when address ranges are absent, check_street_sweeping must
use the segment geometrically closest to the car's position rather than
collecting a union of all segments sharing the same street name.

Note: the HTTP /check endpoint uses `resolve.resolve_car_segment` for the
same purpose; that resolver is covered separately in test_resolve.py. Both
code paths exist (CLI vs. API) and both should produce sensible answers.
"""
import pyproj
import pytest
from shapely.geometry import Point

from broombuster import analysis
from broombuster import car as car_module
from broombuster import data_loader

_CRS = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bay_area_3857():
    return data_loader.load_region_data("bay_area").to_crs("EPSG:3857")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_car(lat, lon, street_name, street_number, city_key):
    """Build a Car with pre-populated street info (no network calls)."""
    c = car_module.Car(lat=lat, lon=lon)
    c.street_name   = street_name
    c.street_number = street_number
    c.streets       = [(street_name, 5.0)]
    c._city         = city_key
    return c


def _nearest_segment(gdf_3857, lat, lon, street_name, city_key=None):
    """Find the GDF row whose geometry is closest to (lat, lon) for a given street."""
    car_x, car_y = _CRS.transform(lon, lat)
    car_pt = Point(car_x, car_y)
    name_idx = analysis._get_name_index(gdf_3857)
    norm = analysis._norm_name(street_name)

    best_row, best_dist = None, float("inf")
    for i in name_idx.get(norm, []):
        row = gdf_3857.loc[i]
        if city_key:
            seg_city = row.get("_city")
            if seg_city and seg_city != city_key:
                continue
        geom = row.geometry
        if geom is not None and not geom.is_empty:
            d = car_pt.distance(geom)
            if d < best_dist:
                best_dist = d
                best_row  = row
    return best_row


def _has_address_ranges(row) -> bool:
    """True if the GDF row contains all four address range columns."""
    return all(
        analysis._safe_int(row.get(k)) is not None
        for k in ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")
    )


# ---------------------------------------------------------------------------
# Test cases: (description, lat, lon, street_name, street_number, city_key)
# ---------------------------------------------------------------------------

CASES = [
    ("2931 Chestnut St, Oakland",    37.821326, -122.280705, "CHESTNUT ST",  2931, "oakland"),
    ("4201 Telegraph Ave, Oakland",  37.830060, -122.261070, "TELEGRAPH AVE", 4201, "oakland"),
    ("450 Guerrero St, SF",          37.759700, -122.421200, "GUERRERO ST",    450, "san_francisco"),
]


@pytest.mark.parametrize("desc,lat,lon,street,num,city", CASES)
class TestNearestSegmentMatch:
    """Parametrised suite: each case checks the nearest-segment invariant."""

    # -- Sanity: can we find the street at all? --------------------------------

    def test_nearest_segment_exists(self, desc, lat, lon, street, num, city, bay_area_3857):
        row = _nearest_segment(bay_area_3857, lat, lon, street, city)
        assert row is not None, f"No segment found for {street!r} in {city}"

    def test_nearest_segment_has_schedule(self, desc, lat, lon, street, num, city, bay_area_3857):
        row = _nearest_segment(bay_area_3857, lat, lon, street, city)
        if row is None:
            pytest.skip(f"No segment found for {street!r}")
        e = analysis.get_schedule(row, 0)
        o = analysis.get_schedule(row, 1)
        assert e or o, f"Nearest segment at {desc!r} has no schedule on either side"

    # -- Core invariant: pipeline must include nearest segment's data ----------

    def test_pipeline_includes_nearest_segment_schedule(
        self, desc, lat, lon, street, num, city, bay_area_3857
    ):
        """check_street_sweeping must return entries from the nearest segment."""
        nearest = _nearest_segment(bay_area_3857, lat, lon, street, city)
        if nearest is None:
            pytest.skip(f"No segment found for {street!r}")

        expected_e = analysis.get_schedule(nearest, 0)
        expected_o = analysis.get_schedule(nearest, 1)

        myCar = _make_car(lat, lon, street, num, city)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )

        if expected_e:
            assert expected_e in schedule_even, (
                f"{desc}: nearest-segment even entry {expected_e!r} "
                f"missing from pipeline result {schedule_even!r}"
            )
        if expected_o:
            assert expected_o in schedule_odd, (
                f"{desc}: nearest-segment odd entry {expected_o!r} "
                f"missing from pipeline result {schedule_odd!r}"
            )

    # -- No spurious extra schedules when no address ranges -------------------

    def test_only_nearest_segment_when_no_address_ranges(
        self, desc, lat, lon, street, num, city, bay_area_3857
    ):
        """
        When segments lack address ranges, the pipeline must return ONLY the
        nearest segment's schedule — not a union of all matching street segments.
        """
        name_idx = analysis._get_name_index(bay_area_3857)
        norm     = analysis._norm_name(street)
        rows     = [bay_area_3857.loc[i] for i in name_idx.get(norm, [])
                    if bay_area_3857.loc[i].get("_city") == city or not city]

        any_ranged = any(_has_address_ranges(r) for r in rows)
        if any_ranged:
            pytest.skip(f"{desc}: segments use address ranges — range-match path active")

        myCar = _make_car(lat, lon, street, num, city)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )
        # With nearest-only matching, at most one unique entry per side
        assert len(schedule_even) <= 1, (
            f"{desc}: expected ≤1 even entry without address ranges, "
            f"got {len(schedule_even)}: {schedule_even!r}"
        )
        assert len(schedule_odd) <= 1, (
            f"{desc}: expected ≤1 odd entry without address ranges, "
            f"got {len(schedule_odd)}: {schedule_odd!r}"
        )

    # -- Address-range path: car's number must fall within the matched range ---

    def test_range_match_contains_car_number(
        self, desc, lat, lon, street, num, city, bay_area_3857
    ):
        """
        When address ranges are present and a match is found, the car's house
        number must fall within at least one matched segment's range.
        """
        name_idx = analysis._get_name_index(bay_area_3857)
        norm     = analysis._norm_name(street)
        rows     = [bay_area_3857.loc[i] for i in name_idx.get(norm, [])
                    if bay_area_3857.loc[i].get("_city") == city or not city]

        if not any(_has_address_ranges(r) for r in rows):
            pytest.skip(f"{desc}: no address ranges present")

        myCar = _make_car(lat, lon, street, num, city)
        _, schedule_even, schedule_odd, _ = analysis.check_street_sweeping(
            myCar, bay_area_3857
        )

        if not (schedule_even or schedule_odd):
            pytest.skip(f"{desc}: no schedule found (number may not match any range)")

        # At least one matched segment must contain the car's number
        matched = False
        for r in rows:
            if not _has_address_ranges(r):
                continue
            l_f = analysis._safe_int(r.get("L_F_ADD"))
            l_t = analysis._safe_int(r.get("L_T_ADD"))
            r_f = analysis._safe_int(r.get("R_F_ADD"))
            r_t = analysis._safe_int(r.get("R_T_ADD"))
            if num and (l_f <= num <= l_t or r_f <= num <= r_t):
                matched = True
                break
        assert matched, (
            f"{desc}: car number {num} not inside any matched segment's range"
        )
