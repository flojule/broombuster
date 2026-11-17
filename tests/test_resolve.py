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

import csv
import re
from pathlib import Path

import geopandas
import pandas as pd
import pyproj
import pytest
from shapely.geometry import LineString, Point, Polygon

from broombuster import analysis, normalize, resolve
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
            "L_F_ADD": 100,
            "L_T_ADD": 198,  # north = even
            "R_F_ADD": 101,
            "R_T_ADD": 199,  # south = odd
            "DAY_EVEN": "ME",
            "DAY_ODD": "WE",
            "DESC_EVEN": "Mon sweep",
            "DESC_ODD": "Wed sweep",
            "TIME_EVEN": "8AM-10AM",
            "TIME_ODD": "10AM-12PM",
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


def test_bent_line_uses_the_segment_under_the_car(east_west_street):
    """A line bending north just past the car: the side comes from the east-west segment
    the car is beside, not the northbound one that starts at the nearest vertex."""
    gdf = east_west_street.copy()
    gdf.geometry = [LineString([(0.0, 0.0), (100.0, 0.0), (102.0, 40.0)])]
    car_lon, car_lat = _ll(90.0, -5.0)  # 5 m south of the first segment
    assert resolve_car_segment(gdf, car_lat, car_lon).side == "odd"


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
            "L_F_ADD": 101,
            "L_T_ADD": 199,  # south = odd
            "R_F_ADD": 100,
            "R_T_ADD": 198,  # north = even
            "DAY_EVEN": None,
            "DAY_ODD": None,
            "DESC_EVEN": "",
            "DESC_ODD": "",
            "TIME_EVEN": "",
            "TIME_ODD": "",
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
            "L_F_ADD": None,
            "L_T_ADD": None,
            "R_F_ADD": None,
            "R_T_ADD": None,
            "DAY_EVEN": None,
            "DAY_ODD": None,
            "DESC_EVEN": "",
            "DESC_ODD": "",
            "TIME_EVEN": "",
            "TIME_ODD": "",
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
            "L_F_ADD": 100,
            "L_T_ADD": 198,
            "R_F_ADD": None,
            "R_T_ADD": None,
            "DAY_EVEN": None,
            "DAY_ODD": None,
            "DESC_EVEN": "",
            "DESC_ODD": "",
            "TIME_EVEN": "",
            "TIME_ODD": "",
            "_city": "oakland",
        }
    ]
    gdf = _gdf_from_rows(rows)
    car_lon, car_lat = _ll(50.0, -10.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon)
    assert resolved.side == "odd"  # inferred complement


# ---------------------------------------------------------------------------
# Out-of-range and polygon behavior
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
            "L_F_ADD": None,
            "L_T_ADD": None,
            "R_F_ADD": None,
            "R_T_ADD": None,
            "DAY_EVEN": "ME",
            "DAY_ODD": "ME",
            "DESC_EVEN": "Ward sweep",
            "DESC_ODD": "Ward sweep",
            "TIME_EVEN": "8AM-12PM",
            "TIME_ODD": "8AM-12PM",
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
# Border street drawn by two cities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("twin_y", "city"), [(-3.0, "berkeley"), (-8.0, "oakland")])
def test_border_twin_only_within_max_distance(twin_y, city):
    # The nearest line (Oakland, unswept) is 35 m off; Berkeley's swept twin wins only in range.
    def row(y, city, day):
        return {
            "geometry": LineString([(0, y), (100, y)]),
            "STREET_NAME": "WOOLSEY ST",
            "STREET_KEY": "WOOLSEY",
            "DAY_EVEN": day,
            "DAY_ODD": day,
            "_city": city,
        }

    gdf = _gdf_from_rows([row(0.0, "oakland", "N"), row(twin_y, "berkeley", "M1")])
    car_lon, car_lat = _ll(50.0, 35.0)
    resolved = resolve_car_segment(gdf, car_lat, car_lon, max_distance_m=40.0)
    assert resolved.segment["_city"] == city


# ---------------------------------------------------------------------------
# Per-region smoke — uses real Bay Area data, same coord as test_pipeline.py
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Real data: the card schedule (union over the physical segment) includes the
# resolved segment's own entries.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lat,lon,city",
    [
        (37.821326, -122.280705, "oakland"),
        (37.830060, -122.261070, "oakland"),
        (37.759700, -122.421200, "san_francisco"),
    ],
)
def test_union_keeps_resolved_schedule(bay_area_3857, lat, lon, city):
    from broombuster import analysis

    r = resolve_car_segment(bay_area_3857, lat, lon, max_distance_m=50.0)
    assert r.segment["_city"] == city
    even, odd = analysis.schedules_for_all_matching_rows(bay_area_3857, r)
    for side, union in ((0, even), (1, odd)):
        own = analysis.get_schedule(r.segment, side)
        assert own is None or own in union


def test_midblock_snaps_to_street(bay_area_3857):
    r = resolve_car_segment(bay_area_3857, 37.821326, -122.280705)
    assert r.street_name.upper().startswith("CHESTNUT")


# A validation point named on a street the data spells otherwise.
_SPELLED_OTHERWISE = {"oak-sw-0097": "GREENLY"}


def _validated_cars():
    """(id, street, lat, lon) of every Street View point whose signs matched the data and
    whose street is named (not "37.7653, -122.3924")."""
    out = []
    for path in sorted((Path(__file__).parent.parent / "data" / "validation").glob("*.csv")):
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name = row["street"].split(",")[0]
                if row["verdict"] == "match" and not re.fullmatch(r"-?[\d.]+", name):
                    out.append((row["id"], name, float(row["lat"]), float(row["lon"])))
    return out


def test_validated_cars_resolve_to_their_street(bay_area_3857):
    """Every car the signs confirmed stays on its street within CAR_MAX_DISTANCE_M."""
    cars = _validated_cars()
    assert len(cars) > 400
    for cid, name, lat, lon in cars:
        r = resolve.nearest_segment(bay_area_3857, lat, lon)
        assert r is not None, (cid, name)
        # Rows drawn on the resolved line, by any name (Doolittle Dr under State Hwy 61).
        near = bay_area_3857.iloc[bay_area_3857.sindex.query(Point(r.projected_point).buffer(1))]
        keys = {analysis.street_key(row) for row in near.to_dict("records")}
        want = _SPELLED_OTHERWISE.get(cid, normalize.street_name(name))
        assert want in keys, (cid, name, r.label)


def test_car_beside_a_street_without_data_is_not_covered(bay_area_3857):
    """Wilma Chan Way (Alameda) has no data row; Atlantic Ave is 24 m away."""
    assert resolve.CAR_MAX_DISTANCE_M == 20.0
    assert resolve.nearest_segment(bay_area_3857, 37.7800, -122.2750) is None
