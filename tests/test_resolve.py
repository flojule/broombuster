"""
Consistency regression suite for src/resolve.py.

Covers:
  - Side determination on synthetic lines (cardinal cross-product cases).
  - max_distance_m cut-off raises NoSegmentNearby.
  - Polygon containment (Chicago-style zone datasets).
  - Per-region smoke: a known Oakland coord snaps to CHESTNUT ST.

The side logic uses both geometry (cross product) and the segment's
L_/R_ address-range parity. These tests lock in the combined rule.
"""
import geopandas
import pandas as pd
import pyproj
import pytest
from shapely.geometry import LineString, Polygon

from broombuster import data_loader
from broombuster.resolve import NoSegmentNearby, resolve_car_segment


_TO_3857 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TO_4326 = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _gdf_from_rows(rows):
    """Build a minimal EPSG:3857 GeoDataFrame from a list of row dicts."""
    gdf = geopandas.GeoDataFrame(
        pd.DataFrame(rows),
        geometry="geometry",
        crs="EPSG:3857",
    )
    # Trigger spatial index build
    _ = gdf.sindex
    return gdf


def _xy(lon, lat):
    return _TO_3857.transform(lon, lat)


def _ll(x, y):
    return _TO_4326.transform(x, y)  # returns (lon, lat)


# ---------------------------------------------------------------------------
# Cardinal-direction side tests
# ---------------------------------------------------------------------------
#
# A single east-west street segment running west→east at y = 0 in 3857 space.
# Left side (driver-left when walking A→B) is y > 0, i.e. NORTH.
# We pin the north side as EVEN (L_F_ADD/L_T_ADD both even),
# and the south side as ODD (R_F_ADD/R_T_ADD both odd).
#
#      NORTH (even)
#           ▲
#   A ────────────── B          (y = 0, centerline)
#           ▼
#      SOUTH (odd)
#


@pytest.fixture
def east_west_street():
    # 100 m segment at equator-ish projected coords.
    # (any plausible 3857 coords work; pick small numbers for readability)
    a = (0.0, 0.0)
    b = (100.0, 0.0)
    line = LineString([a, b])
    rows = [
        {
            "geometry": line,
            "STREET_NAME": "TEST ST",
            "STREET_DISPLAY": "Test St",
            "STREET_KEY": "TEST",
            "L_F_ADD": 100, "L_T_ADD": 198,  # north = even
            "R_F_ADD": 101, "R_T_ADD": 199,  # south = odd
            "DAY_EVEN": "ME", "DAY_ODD": "WE",
            "DESC_EVEN": "Mon sweep", "DESC_ODD": "Wed sweep",
            "TIME_EVEN": "8AM-10AM", "TIME_ODD": "10AM-12PM",
            "_city": "oakland",
        }
    ]
    return _gdf_from_rows(rows)


def test_car_north_of_line_resolves_to_even(east_west_street):
    # Car 10 m north of the centerline → LEFT → EVEN.
    car_lon, car_lat = _ll(50.0, 10.0)
    resolved = resolve_car_segment(east_west_street, car_lat, car_lon)
    assert resolved.side == "even"
    assert resolved.street_name == "TEST ST"
    assert resolved.is_polygon is False
    assert resolved.distance_m == pytest.approx(10.0, abs=0.5)


def test_car_south_of_line_resolves_to_odd(east_west_street):
    car_lon, car_lat = _ll(50.0, -10.0)
    resolved = resolve_car_segment(east_west_street, car_lat, car_lon)
    assert resolved.side == "odd"


def test_car_north_at_start_of_line(east_west_street):
    car_lon, car_lat = _ll(0.5, 10.0)
    resolved = resolve_car_segment(east_west_street, car_lat, car_lon)
    assert resolved.side == "even"


def test_car_south_at_end_of_line(east_west_street):
    car_lon, car_lat = _ll(99.5, -10.0)
    resolved = resolve_car_segment(east_west_street, car_lat, car_lon)
    assert resolved.side == "odd"


def test_car_on_centerline_tiebreaks_deterministically(east_west_street):
    car_lon, car_lat = _ll(50.0, 0.0)
    resolved = resolve_car_segment(east_west_street, car_lat, car_lon)
    # Exactly-on-centerline convention: treat as right side → odd.
    assert resolved.side == "odd"


def test_reversed_segment_still_assigns_sides_correctly():
    # Same physical street, but directed B→A. The cross-product sign flips,
    # but L_F_ADD / R_F_ADD flip with it — net side must stay consistent.
    line = LineString([(100.0, 0.0), (0.0, 0.0)])  # west-bound
    rows = [
        {
            "geometry": line,
            "STREET_NAME": "TEST ST",
            "STREET_DISPLAY": "Test St",
            "STREET_KEY": "TEST",
            # When direction flips, left and right swap. L is now south, R is now north.
            "L_F_ADD": 101, "L_T_ADD": 199,  # south = odd
            "R_F_ADD": 100, "R_T_ADD": 198,  # north = even
            "DAY_EVEN": None, "DAY_ODD": None,
            "DESC_EVEN": "", "DESC_ODD": "",
            "TIME_EVEN": "", "TIME_ODD": "",
            "_city": "oakland",
        }
    ]
    gdf = _gdf_from_rows(rows)
    # Car north → should still map to EVEN (address-range metadata is correct).
    car_lon, car_lat = _ll(50.0, 10.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon)
    assert resolved.side == "even"


# ---------------------------------------------------------------------------
# Missing / ambiguous address ranges
# ---------------------------------------------------------------------------


def test_no_address_ranges_returns_none_side():
    line = LineString([(0.0, 0.0), (100.0, 0.0)])
    rows = [
        {
            "geometry": line,
            "STREET_NAME": "MYSTERY ST",
            "STREET_DISPLAY": "Mystery St",
            "STREET_KEY": "MYSTERY",
            "L_F_ADD": None, "L_T_ADD": None,
            "R_F_ADD": None, "R_T_ADD": None,
            "DAY_EVEN": None, "DAY_ODD": None,
            "DESC_EVEN": "", "DESC_ODD": "",
            "TIME_EVEN": "", "TIME_ODD": "",
            "_city": "oakland",
        }
    ]
    gdf = _gdf_from_rows(rows)
    car_lon, car_lat = _ll(50.0, 10.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon)
    assert resolved.side is None  # unknown → caller falls back to union of both


def test_one_side_range_infers_other_side():
    # Only the left (north) side has ranges, marked even. Car on the south
    # side must therefore be odd (the complement).
    line = LineString([(0.0, 0.0), (100.0, 0.0)])
    rows = [
        {
            "geometry": line,
            "STREET_NAME": "HALF ST",
            "STREET_DISPLAY": "Half St",
            "STREET_KEY": "HALF",
            "L_F_ADD": 100, "L_T_ADD": 198,
            "R_F_ADD": None, "R_T_ADD": None,
            "DAY_EVEN": None, "DAY_ODD": None,
            "DESC_EVEN": "", "DESC_ODD": "",
            "TIME_EVEN": "", "TIME_ODD": "",
            "_city": "oakland",
        }
    ]
    gdf = _gdf_from_rows(rows)
    car_lon, car_lat = _ll(50.0, -10.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon)
    assert resolved.side == "odd"  # inferred complement


# ---------------------------------------------------------------------------
# Out-of-range and polygon behaviour
# ---------------------------------------------------------------------------


def test_far_coord_raises_no_segment_nearby(east_west_street):
    # 1 km north of the segment — well beyond max_distance_m.
    car_lon, car_lat = _ll(50.0, 1_000.0)
    with pytest.raises(NoSegmentNearby):
        resolve_car_segment(east_west_street, car_lat, car_lon, max_distance_m=40.0)


def test_polygon_containment_returns_zone():
    # Chicago-style zone polygon; car inside.
    poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    rows = [
        {
            "geometry": poly,
            "STREET_NAME": "ZONE 42",
            "STREET_DISPLAY": "Zone 42",
            "STREET_KEY": "ZONE42",
            "L_F_ADD": None, "L_T_ADD": None,
            "R_F_ADD": None, "R_T_ADD": None,
            "DAY_EVEN": "ME", "DAY_ODD": "ME",
            "DESC_EVEN": "Ward sweep", "DESC_ODD": "Ward sweep",
            "TIME_EVEN": "8AM-12PM", "TIME_ODD": "8AM-12PM",
            "_city": "chicago",
        }
    ]
    gdf = _gdf_from_rows(rows)
    car_lon, car_lat = _ll(50.0, 50.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon)
    assert resolved.is_polygon is True
    assert resolved.side is None
    assert resolved.distance_m == 0.0
    assert resolved.street_name == "ZONE 42"


# ---------------------------------------------------------------------------
# city_key filter
# ---------------------------------------------------------------------------


def test_city_key_filter_skips_wrong_city():
    # Two overlapping lines, each tagged with a different city. Resolver
    # with city_key="oakland" must ignore the SF-tagged one even if closer.
    near = LineString([(0, 0), (100, 0)])     # 5 m from car (SF, should be skipped)
    far  = LineString([(0, 50), (100, 50)])   # 45 m from car (oakland, pick this)
    rows = [
        {
            "geometry": near,
            "STREET_NAME": "SF ST",
            "STREET_DISPLAY": "SF St",
            "STREET_KEY": "SFST",
            "L_F_ADD": 100, "L_T_ADD": 198,
            "R_F_ADD": 101, "R_T_ADD": 199,
            "DAY_EVEN": None, "DAY_ODD": None,
            "DESC_EVEN": "", "DESC_ODD": "",
            "TIME_EVEN": "", "TIME_ODD": "",
            "_city": "san_francisco",
        },
        {
            "geometry": far,
            "STREET_NAME": "OAK ST",
            "STREET_DISPLAY": "Oak St",
            "STREET_KEY": "OAK",
            "L_F_ADD": 100, "L_T_ADD": 198,
            "R_F_ADD": 101, "R_T_ADD": 199,
            "DAY_EVEN": None, "DAY_ODD": None,
            "DESC_EVEN": "", "DESC_ODD": "",
            "TIME_EVEN": "", "TIME_ODD": "",
            "_city": "oakland",
        },
    ]
    gdf = _gdf_from_rows(rows)
    car_lon, car_lat = _ll(50.0, 5.0)
    resolved = resolve_car_segment(
        gdf, car_lat, car_lon, city_key="oakland", max_distance_m=60.0
    )
    assert resolved.street_name == "OAK ST"


# ---------------------------------------------------------------------------
# Per-region smoke — uses real Bay Area data, same coord as test_pipeline.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bay_area_3857():
    gdf = data_loader.load_region_data("bay_area")
    gdf3857 = gdf.to_crs("EPSG:3857")
    # _city column is added by api/api.py at load time; add it here so the
    # filter works in tests too.
    if "_city" not in gdf3857.columns:
        # Without the api-layer tag we leave it unset; city_key=None in the
        # call below skips filtering.
        pass
    return gdf3857


def test_oakland_known_coord_snaps_to_chestnut(bay_area_3857):
    # 2931 Chestnut St, Oakland — same fixture used in test_pipeline.py.
    lat, lon = 37.821326, -122.280705
    resolved = resolve_car_segment(bay_area_3857, lat, lon, max_distance_m=50.0)
    # Match is street-name only; we do not assert side here because the
    # Oakland shapefile's address-range parity convention is covered by the
    # synthetic tests above.
    from broombuster.normalize import street_name as _norm
    assert _norm(resolved.street_name) == _norm("CHESTNUT ST")
    assert resolved.is_polygon is False
    assert resolved.distance_m < 40.0
