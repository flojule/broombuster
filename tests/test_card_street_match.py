"""
Nearest-segment invariants for the resolver path (resolve.resolve_car_segment),
used by both the /check endpoint and the CLI via analysis.analyze_car.

Core invariant: the schedule of the segment the resolver picks must appear in
analyze_car's output (the car card reflects the resolved segment). Snapping
accuracy for controlled mid-block coordinates is covered in test_resolve.py.
"""
import pytest

from broombuster import analysis, data_loader, normalize, resolve


@pytest.fixture(scope="module")
def bay_area_3857():
    return data_loader.load_region_data("bay_area").to_crs("EPSG:3857")


# (description, lat, lon, expected_street, city_key)
CASES = [
    ("2931 Chestnut St, Oakland",   37.821326, -122.280705, "CHESTNUT ST",   "oakland"),
    ("4201 Telegraph Ave, Oakland", 37.830060, -122.261070, "TELEGRAPH AVE", "oakland"),
    ("450 Guerrero St, SF",         37.759700, -122.421200, "GUERRERO ST",   "san_francisco"),
]


def _resolve(gdf, lat, lon, city):
    try:
        return resolve.resolve_car_segment(gdf, lat, lon, city_key=city, max_distance_m=50.0)
    except resolve.NoSegmentNearby:
        return None


@pytest.mark.parametrize("desc,lat,lon,street,city", CASES)
class TestNearestSegmentMatch:

    def test_resolver_finds_segment(self, desc, lat, lon, street, city, bay_area_3857):
        assert _resolve(bay_area_3857, lat, lon, city) is not None, (
            f"No segment within 50 m for {desc}"
        )

    def test_analyze_car_includes_resolved_schedule(
        self, desc, lat, lon, street, city, bay_area_3857
    ):
        """The resolved segment's own schedule must surface in analyze_car output."""
        resolved = _resolve(bay_area_3857, lat, lon, city)
        if resolved is None:
            pytest.skip(f"No segment resolved for {desc}")
        seg_e = analysis.get_schedule(resolved.segment, 0)
        seg_o = analysis.get_schedule(resolved.segment, 1)
        _, schedule_even, schedule_odd, _ = analysis.analyze_car(
            bay_area_3857, lat, lon, city_key=city
        )
        if seg_e:
            assert seg_e in schedule_even, (
                f"{desc}: resolved even entry {seg_e!r} missing from {schedule_even!r}"
            )
        if seg_o:
            assert seg_o in schedule_odd, (
                f"{desc}: resolved odd entry {seg_o!r} missing from {schedule_odd!r}"
            )


def test_midblock_snaps_to_expected_street(bay_area_3857):
    """A mid-block Oakland coordinate snaps to the expected centerline."""
    resolved = _resolve(bay_area_3857, 37.821326, -122.280705, "oakland")
    assert resolved is not None
    assert normalize.street_name(resolved.street_name) == normalize.street_name("CHESTNUT ST")
