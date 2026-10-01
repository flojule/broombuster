"""
The indexed schedule union (analysis.segment_index) and the vectorised resolver
match plain row-by-row scans, on the real Bay Area + Chicago data and synthetic
edge cases. Also: /check never waits on Nominatim, GET /address adds the house
number, and startup builds the indexes.
"""
import gc
import random

import geopandas
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from broombuster import analysis, data_loader, gps, normalize, resolve

# ---------------------------------------------------------------------------
# Reference implementations (plain row-by-row scans)
# ---------------------------------------------------------------------------


_REF_TABLES: dict = {}


def _ref_table(gdf):
    """Per-row (street key, endpoints, even, odd) for the reference scan."""
    if id(gdf) not in _REF_TABLES:
        table = []
        for _, row in gdf.iterrows():
            k = row.get("STREET_KEY")
            if not normalize.is_text(k):
                n = row.get("STREET_NAME")
                k = normalize.street_name(n) if normalize.is_text(n) else None
            g = row.geometry
            if k is None or g is None or g.is_empty or \
                    g.geom_type not in ("LineString", "MultiLineString"):
                continue
            table.append((k, analysis._segment_endpoints(g),
                          analysis.get_schedule(row, 0), analysis.get_schedule(row, 1)))
        _REF_TABLES[id(gdf)] = (gdf, table)  # holds gdf so the id stays unique
    return _REF_TABLES[id(gdf)][1]


def _ref_union(gdf, resolved):
    """schedules_for_all_matching_rows as a full scan of every row."""
    seg = resolved.segment
    if resolved.is_polygon:
        return analysis.schedules_for_segment(seg)
    tg = seg.geometry
    if tg is None or tg.is_empty or tg.geom_type not in ("LineString", "MultiLineString"):
        return analysis.schedules_for_segment(seg)
    tkey = seg.get("STREET_KEY") or normalize.street_name(seg.get("STREET_NAME") or "")
    teps = analysis._segment_endpoints(tg)
    if tkey == "" or not teps:
        return analysis.schedules_for_segment(seg)
    se, so, eo, oo = set(), set(), [], []
    for k, cand, e, o in _ref_table(gdf):
        if k != tkey or not cand or cand.isdisjoint(teps):
            continue
        if e and e not in se:
            se.add(e)
            eo.append(e)
        if o and o not in so:
            so.add(o)
            oo.append(o)
    eo.sort()
    oo.sort()
    return eo, oo


def _ref_resolve(gdf, lat, lon, city_key=None, max_distance_m=40.0):
    """(row position, is_polygon, distance) a per-row resolver loop picks."""
    x, y = resolve._TRANSFORMER_4326_TO_3857.transform(lon, lat)
    pt = Point(x, y)
    best, best_d = None, float("inf")
    for i in gdf.sindex.query(pt.buffer(max(max_distance_m * 3.0, 100.0))):
        row = gdf.iloc[i]
        if city_key:
            c = row.get("_city")
            if c and c != city_key:
                continue
        g = row.geometry
        if g is None or g.is_empty:
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            if g.contains(pt):
                return int(i), True, 0.0
            continue
        d = pt.distance(g)
        if d < best_d:
            best, best_d = int(i), d
    if best is None or best_d > max_distance_m:
        return None
    return best, False, best_d


def _position(gdf, row):
    return gdf.index.get_loc(row.name)


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_points():
    rng = random.Random(7)
    pts = [(rng.uniform(37.72, 37.88), rng.uniform(-122.51, -122.22)) for _ in range(250)]
    # Long multi-row streets: Market St, Broadway, Santa Clara Ave, Folsom St.
    pts += [(37.7749, -122.4194), (37.8044, -122.2712), (37.7652, -122.2416),
            (37.7599, -122.4148), (37.7858, -122.4064)]
    return pts


def test_union_matches_full_scan_on_bay_area(bay_area_3857, sample_points):
    checked = 0
    for lat, lon in sample_points:
        r = resolve.nearest_segment(bay_area_3857, lat, lon)
        if r is None:
            continue
        assert analysis.schedules_for_all_matching_rows(bay_area_3857, r) == \
            _ref_union(bay_area_3857, r), (lat, lon, r.label)
        checked += 1
    assert checked >= 50, "too few resolved points to be meaningful"


@pytest.mark.parametrize("city_key", [None, "oakland", "san_francisco"])
def test_resolver_matches_row_scan_on_bay_area(bay_area_3857, sample_points, city_key):
    for lat, lon in sample_points:
        ref = _ref_resolve(bay_area_3857, lat, lon, city_key)
        try:
            got = resolve.resolve_car_segment(bay_area_3857, lat, lon, city_key=city_key)
        except resolve.NoSegmentNearby:
            assert ref is None, (lat, lon)
            continue
        assert ref is not None, (lat, lon)
        pos, is_poly, dist = ref
        assert _position(bay_area_3857, got.segment) == pos, (lat, lon)
        assert got.is_polygon == is_poly
        assert got.distance_m == pytest.approx(dist, abs=1e-9)


def test_resolver_matches_row_scan_on_chicago_zones():
    gdf = data_loader.load_region_data("chicago").to_crs("EPSG:3857")
    rng = random.Random(3)
    hits = 0
    for _ in range(80):
        lat, lon = rng.uniform(41.70, 42.00), rng.uniform(-87.80, -87.55)
        ref = _ref_resolve(gdf, lat, lon, max_distance_m=0.0)
        try:
            got = resolve.resolve_car_segment(gdf, lat, lon, max_distance_m=0.0)
        except resolve.NoSegmentNearby:
            assert ref is None
            continue
        hits += 1
        assert ref is not None and got.is_polygon
        assert _position(gdf, got.segment) == ref[0]
    assert hits, "expected some points inside Chicago zones"


# ---------------------------------------------------------------------------
# Synthetic edge cases
# ---------------------------------------------------------------------------


def _synthetic():
    """3857 rows on a non-Range index: SF-style duplicates, a MultiLineString
    spanning blocks, NaN STREET_KEY, an empty geometry and a polygon."""
    a = LineString([(0, 0), (100, 0)])
    b = LineString([(100, 0), (200, 0)])
    rows = [
        # Same block, one row per weekday (SF shape); orientation reversed once.
        dict(STREET_KEY="ELM", STREET_NAME="ELM ST", DAY_EVEN="ME", DESC_EVEN="Mon",
             TIME_EVEN="8AM-10AM", DAY_ODD=None, geometry=a),
        dict(STREET_KEY="ELM", STREET_NAME="ELM ST", DAY_EVEN="WE", DESC_EVEN="Wed",
             TIME_EVEN="8AM-10AM", DAY_ODD="TE", geometry=LineString([(100, 0), (0, 0)])),
        # Whole street as one MultiLineString (Alameda shape) overlapping block a.
        dict(STREET_KEY="ELM", STREET_NAME="ELM ST", DAY_EVEN="F13", DESC_EVEN="Fri",
             TIME_EVEN="", DAY_ODD="F24", geometry=MultiLineString([a.coords, b.coords])),
        # Different street sharing block a's endpoints: never merged.
        dict(STREET_KEY="OAK", STREET_NAME="OAK ST", DAY_EVEN="SE", geometry=a),
        # NaN STREET_KEY falls back to the normalised STREET_NAME.
        dict(STREET_KEY=np.nan, STREET_NAME="Elm Street", DAY_EVEN="THE", geometry=a),
        # No-sweep code and an empty geometry contribute nothing.
        dict(STREET_KEY="ELM", STREET_NAME="ELM ST", DAY_EVEN="NS", geometry=a),
        dict(STREET_KEY="ELM", STREET_NAME="ELM ST", DAY_EVEN="SUE", geometry=LineString()),
        dict(STREET_KEY="ZONE", STREET_NAME="ZONE 1", DAY_EVEN="ME",
             geometry=Polygon([(0, 10), (50, 10), (50, 60), (0, 60)])),
    ]
    gdf = geopandas.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3857")
    gdf.index = pd.Index([10 * i + 5 for i in range(len(gdf))])
    return gdf


def _resolved(row, is_polygon=False):
    return resolve.ResolvedCar(segment=row, street_name=row["STREET_NAME"],
                               street_display="", side=None, distance_m=0.0,
                               projected_point=(0.0, 0.0), is_polygon=is_polygon)


@pytest.mark.parametrize("pos", [0, 1, 2, 3, 4])
def test_union_matches_full_scan_on_edge_cases(pos):
    gdf = _synthetic()
    r = _resolved(gdf.iloc[pos])
    assert analysis.schedules_for_all_matching_rows(gdf, r) == _ref_union(gdf, r)


def test_union_merges_sf_and_alameda_shapes():
    gdf = _synthetic()
    even, odd = analysis.schedules_for_all_matching_rows(gdf, _resolved(gdf.iloc[0]))
    assert [e[0] for e in even] == ["F13", "ME", "THE", "WE"]
    assert [o[0] for o in odd] == ["F24", "TE"]


@pytest.mark.parametrize("xy", [(20, 5), (150, -3), (25, 30), (500, 500)])
def test_resolver_matches_row_scan_on_edge_cases(xy):
    gdf = _synthetic()
    lon, lat = resolve._TRANSFORMER_4326_TO_3857.transform(*xy, direction="INVERSE")
    ref = _ref_resolve(gdf, lat, lon)
    try:
        got = resolve.resolve_car_segment(gdf, lat, lon)
    except resolve.NoSegmentNearby:
        assert ref is None
        return
    assert (_position(gdf, got.segment), got.is_polygon) == ref[:2]


def test_segment_index_is_cached_and_released():
    gdf = _synthetic()
    first = analysis.segment_index(gdf)
    assert analysis.segment_index(gdf) is first
    key = id(gdf)
    del gdf, first
    gc.collect()
    assert key not in analysis._segment_index_cache


# ---------------------------------------------------------------------------
# Startup indexing
# ---------------------------------------------------------------------------


def test_startup_prebuilds_indexes(app_client):
    from broombuster.api import state

    gdf = state.region_gdf("chicago")
    assert gdf.has_sindex
    assert analysis._segment_index_cache[id(gdf)][0]() is gdf


# ---------------------------------------------------------------------------
# Address: /check never waits on Nominatim; GET /address adds the number
# ---------------------------------------------------------------------------

_OAKLAND = {"lat": 37.821326, "lon": -122.280705, "region": "bay_area"}


@pytest.fixture
def client(app_client):
    gps._cache.clear()
    yield app_client
    gps._cache.clear()


def test_check_does_not_call_nominatim(client, monkeypatch):
    def _boom(lat, lon):
        raise AssertionError("/check must not call Nominatim")
    monkeypatch.setattr(gps, "_nominatim", _boom)
    data = client.post("/check", json=_OAKLAND).json()
    assert data["address_pending"] is True
    assert data["address"] == "Chestnut St, Oakland"


def test_address_endpoint_adds_house_number_then_check_uses_cache(client, monkeypatch):
    monkeypatch.setattr(gps, "_nominatim",
                        lambda lat, lon: {"road": "Chestnut Street", "house_number": "2931"})
    street = "Chestnut St"
    client.post("/check", json=_OAKLAND)
    resp = client.get("/address", params=_OAKLAND)
    assert resp.status_code == 200
    assert resp.json()["address"].startswith(f"2931 {street}")
    # Now cached: /check answers with the number and nothing pending.
    data = client.post("/check", json=_OAKLAND).json()
    assert data["address"].startswith(f"2931 {street}")
    assert data["address_pending"] is False


def test_zone_address_is_never_pending(client):
    data = client.post("/check", json={"lat": 41.8781, "lon": -87.6298,
                                       "region": "chicago"}).json()
    assert data["address"].startswith("Zone:")
    assert data["address_pending"] is False


def test_geocode_failure_is_not_cached(monkeypatch):
    gps._cache.clear()
    calls = []

    def _flaky(lat, lon):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError
        return {"road": "Grand Avenue", "house_number": "12"}
    monkeypatch.setattr(gps, "_nominatim", _flaky)
    assert gps.maybe_house_number(37.5, -122.5, "Grand Ave") is None
    assert not gps.house_number_cached(37.5, -122.5)
    assert gps.maybe_house_number(37.5, -122.5, "Grand Ave") == 12
    assert gps.house_number_cached(37.5, -122.5)
    gps._cache.clear()


def test_cache_only_lookup_skips_network(monkeypatch):
    gps._cache.clear()
    monkeypatch.setattr(gps, "_nominatim", lambda lat, lon: pytest.fail("network used"))
    assert gps.maybe_house_number(37.5, -122.5, "Grand Ave", network=False) is None


def test_warm_check_is_fast(app_client):
    """A /check on a long multi-row street (Market St, SF) stays well under 1 s
    (~5 ms on a Raspberry Pi 5)."""
    import time

    body = {"lat": 37.7749, "lon": -122.4194, "region": "bay_area"}
    t0 = time.perf_counter()
    resp = app_client.post("/check", json=body)
    assert resp.status_code == 200, resp.text
    assert time.perf_counter() - t0 < 1.0
