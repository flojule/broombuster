"""
The schedule union (analysis.schedules_for_all_matching_rows) and the vectorised
resolver match plain row-by-row scans, on the real Bay Area + Chicago data and
synthetic edge cases; the map tiles merge rows as the card does. Also: /check never
waits on Nominatim, GET /address adds the house number, and startup builds the indexes.
"""

import math
import random

import geopandas
import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon

from broombuster import analysis, gps, normalize, resolve, tiles

# ---------------------------------------------------------------------------
# Reference implementations (plain row-by-row scans)
# ---------------------------------------------------------------------------


_REF_TABLES: dict = {}


def _parts(g):
    """LineString parts (2+ coordinates) of a line geometry, else []."""
    if g is None or g.is_empty or g.geom_type not in ("LineString", "MultiLineString"):
        return []
    return [p for p in getattr(g, "geoms", [g]) if len(p.coords) >= 2]


def _ref_table(gdf):
    """(row bounds array, [(key, city, parts, even, odd)]) of every row, for the reference scan."""
    if id(gdf) not in _REF_TABLES:
        table = []
        for row in gdf.to_dict("records"):  # dicts: .get() like a row Series, but fast
            k = row.get("STREET_KEY")
            if not normalize.is_text(k):
                k = normalize.street_name(row.get("STREET_NAME"))
            table.append(
                (
                    k,
                    row.get("_city"),
                    _parts(row["geometry"]),
                    analysis.get_schedule(row, 0),
                    analysis.get_schedule(row, 1),
                )
            )
        bounds = np.array([g.bounds if g is not None else (np.nan,) * 4 for g in gdf.geometry])
        _REF_TABLES[id(gdf)] = (gdf, bounds, table)  # holds gdf so the id stays unique
    return _REF_TABLES[id(gdf)][1:]


def _ref_coincide(a, b, ka, kb, ca, cb) -> bool:
    """Two line parts (EPSG:3857) draw one street: Hausdorff within 3 m (10 m for two
    cities) and half the shorter part, or one street key with the same ends (~1 m)."""
    k = math.cosh(a.coords[0][1] / 6378137.0)
    tol = min((3.0 if ca == cb else 10.0) * k, min(a.length, b.length) / 2)
    if a.hausdorff_distance(b) <= tol:
        return True

    def ends(p):
        return {tuple(round(v) for v in p.coords[i][:2]) for i in (0, -1)}

    return ka == kb and ka != "" and ends(a) == ends(b)


def _ref_union(gdf, resolved):
    """schedules_for_all_matching_rows as a scan of every row."""
    seg = resolved.segment
    tparts = _parts(seg.geometry)
    if resolved.is_polygon or not tparts:
        return analysis.schedules_for_segment(seg)
    tkey = seg.get("STREET_KEY")
    if not normalize.is_text(tkey):
        tkey = normalize.street_name(seg.get("STREET_NAME"))
    bounds, table = _ref_table(gdf)
    x0, y0, x1, y1 = seg.geometry.bounds
    r = 11.0 * math.cosh(y1 / 6378137.0)  # past BORDER_M at any of the target's latitudes
    close = (
        (bounds[:, 0] <= x1 + r)
        & (bounds[:, 2] >= x0 - r)
        & (bounds[:, 1] <= y1 + r)
        & (bounds[:, 3] >= y0 - r)
    )
    se, so, eo, oo = set(), set(), [], []
    for i in np.flatnonzero(close):
        k, city, parts, e, o = table[i]
        if not any(
            _ref_coincide(a, b, tkey, k, seg.get("_city"), city) for a in tparts for b in parts
        ):
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


def _ref_resolve(gdf, lat, lon, max_distance_m=40.0):
    """(row position, is_polygon, distance) a per-row resolver loop picks."""
    x, y = resolve._TRANSFORMER_4326_TO_3857.transform(lon, lat)
    pt = Point(x, y)
    k = resolve.mercator_scale(lat)
    best, best_d, lines = None, float("inf"), []
    for i in gdf.sindex.query(pt.buffer(max(max_distance_m * 3.0, 100.0) * k)):
        row = gdf.iloc[i]
        g = row.geometry
        if g is None or g.is_empty:
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            if g.contains(pt):
                return int(i), True, 0.0
            continue
        d = pt.distance(g)
        lines.append((d, int(i)))
        if d < best_d:
            best, best_d = int(i), d
    if best is None or best_d > max_distance_m * k:
        return None
    if _unswept(gdf.iloc[best]):  # border street: the city that sweeps it, if in range
        b = gdf.iloc[best]
        for d, i in sorted(lines, key=lambda x: x[0]):
            r = gdf.iloc[i]
            if d > best_d + resolve.BORDER_EXTRA_M or d > max_distance_m * k:
                break
            alongside = 0 < r.geometry.project(pt) < r.geometry.length
            same = r.get("STREET_KEY") == b.get("STREET_KEY")
            if r.get("_city") != b.get("_city") and same and not _unswept(r) and alongside:
                best, best_d = i, d
                break
    return best, False, best_d / k


def _unswept(row):
    return all(
        str(row.get(f"DAY_{s}") or "").strip().upper() in analysis.NO_SWEEP_CODES
        for s in ("EVEN", "ODD")
    )


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
    pts += [
        (37.7749, -122.4194),
        (37.8044, -122.2712),
        (37.7652, -122.2416),
        (37.7599, -122.4148),
        (37.7858, -122.4064),
    ]
    return pts


def test_union_matches_full_scan_on_bay_area(bay_area_3857, sample_points):
    checked = 0
    for lat, lon in sample_points:
        r = resolve.nearest_segment(bay_area_3857, lat, lon)
        if r is None:
            continue
        assert analysis.schedules_for_all_matching_rows(bay_area_3857, r) == _ref_union(
            bay_area_3857, r
        ), (lat, lon, r.label)
        checked += 1
    assert checked >= 50, "too few resolved points to be meaningful"


@pytest.mark.parametrize(
    "lat,lon,even,odd",
    [
        # Oakland draws State Hwy 123 (swept) over San Pablo Ave (not).
        (37.814405, -122.274433, {"E"}, {"E"}),
        (37.801237, -122.262787, {"MWF"}, {"FE"}),  # Lakeside Dr under Oak St
        (37.795657, -122.278103, {"MWF"}, {"TTHS"}),  # Washington St under Jack London Sq
        # Border streets drawn by Oakland and Berkeley: both cities' days, either side.
        (37.850353, -122.283356, {"M24", "W2"}, {"T2", "T24"}),
        (37.850283, -122.283332, {"M24", "W2"}, {"T2", "T24"}),
        (37.85070, -122.27065, {"M24", "T1"}, {"M1", "T24"}),
    ],
)
def test_check_unions_rows_drawing_one_street(app_client, lat, lon, even, odd):
    body = {"lat": lat, "lon": lon, "region": "bay_area"}
    data = app_client.post("/api/check", json=body).json()
    assert {e[0] for e in data["schedule_even"]} == even
    assert {o[0] for o in data["schedule_odd"]} == odd


def test_tiles_color_streets_as_the_card(bay_area_gdf, bay_area_3857, sample_points):
    lines = [r for r in tiles.merge_segment_rows(bay_area_gdf) if r["render_type"] == "line"]
    tree = shapely.STRtree(
        geopandas.GeoSeries([r["geometry"] for r in lines], crs="EPSG:4326").to_crs(3857).values
    )
    checked = 0
    for lat, lon in sample_points:
        r = resolve.nearest_segment(bay_area_3857, lat, lon)
        if r is None:
            continue
        even, odd = analysis.schedules_for_all_matching_rows(bay_area_3857, r)
        tile = lines[int(tree.nearest(Point(r.projected_point)))]
        got = sorted((s["side"], s["code"], s["desc"], s["time"]) for s in tile["schedule"])
        assert got == sorted([("even", *e) for e in even] + [("odd", *o) for o in odd]), r.label
        checked += 1
    assert checked >= 50


def test_resolver_matches_row_scan_on_bay_area(bay_area_3857, sample_points):
    for lat, lon in sample_points:
        ref = _ref_resolve(bay_area_3857, lat, lon)
        try:
            got = resolve.resolve_car_segment(bay_area_3857, lat, lon)
        except resolve.NoSegmentNearby:
            assert ref is None, (lat, lon)
            continue
        assert ref is not None, (lat, lon)
        pos, is_poly, dist = ref
        assert _position(bay_area_3857, got.segment) == pos, (lat, lon)
        assert got.is_polygon == is_poly
        assert got.distance_m == pytest.approx(dist, abs=1e-9)


def test_resolver_matches_row_scan_on_chicago_zones(chicago_3857):
    gdf = chicago_3857
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
        {
            "STREET_KEY": "ELM",
            "STREET_NAME": "ELM ST",
            "DAY_EVEN": "ME",
            "DESC_EVEN": "Mon",
            "TIME_EVEN": "8AM-10AM",
            "DAY_ODD": None,
            "geometry": a,
        },
        {
            "STREET_KEY": "ELM",
            "STREET_NAME": "ELM ST",
            "DAY_EVEN": "WE",
            "DESC_EVEN": "Wed",
            "TIME_EVEN": "8AM-10AM",
            "DAY_ODD": "TE",
            "geometry": LineString([(100, 0), (0, 0)]),
        },
        # Whole street as one MultiLineString (Alameda shape) overlapping block a.
        {
            "STREET_KEY": "ELM",
            "STREET_NAME": "ELM ST",
            "DAY_EVEN": "F13",
            "DESC_EVEN": "Fri",
            "TIME_EVEN": "",
            "DAY_ODD": "F24",
            "geometry": MultiLineString([a.coords, b.coords]),
        },
        # Different street drawn on block a (a state highway over its street): merged.
        {"STREET_KEY": "OAK", "STREET_NAME": "OAK ST", "DAY_EVEN": "SE", "geometry": a},
        # NaN STREET_KEY falls back to the normalized STREET_NAME.
        {"STREET_KEY": np.nan, "STREET_NAME": "Elm Street", "DAY_EVEN": "THE", "geometry": a},
        # No-sweep code and an empty geometry contribute nothing.
        {"STREET_KEY": "ELM", "STREET_NAME": "ELM ST", "DAY_EVEN": "NS", "geometry": a},
        {"STREET_KEY": "ELM", "STREET_NAME": "ELM ST", "DAY_EVEN": "SUE", "geometry": LineString()},
        {
            "STREET_KEY": "ZONE",
            "STREET_NAME": "ZONE 1",
            "DAY_EVEN": "ME",
            "geometry": Polygon([(0, 10), (50, 10), (50, 60), (0, 60)]),
        },
        # Another street between block a's corners, curved: never merged.
        {
            "STREET_KEY": "PARK",
            "STREET_NAME": "PARK ST",
            "DAY_EVEN": "SUE",
            "geometry": LineString([(0, 0), (50, 40), (100, 0)]),
        },
        # The same city's other curb line, 5 m away: its own line.
        {
            "STREET_KEY": "ELM",
            "STREET_NAME": "ELM ST",
            "DAY_EVEN": "MWF",
            "geometry": LineString([(0, 5), (100, 5)]),
        },
        # The next city's drawing of block a, 6 m away: merged.
        {
            "STREET_KEY": "ELM",
            "STREET_NAME": "ELM ST",
            "DAY_EVEN": "W2",
            "_city": "next",
            "geometry": LineString([(1, -6), (99, -6)]),
        },
    ]
    rows = [{"_city": "here", **r} for r in rows]
    gdf = geopandas.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3857")
    gdf.index = pd.Index([10 * i + 5 for i in range(len(gdf))])
    return gdf


def _resolved(row, is_polygon=False):
    return resolve.ResolvedCar(
        segment=row,
        street_name=row["STREET_NAME"],
        street_display="",
        side=None,
        distance_m=0.0,
        projected_point=(0.0, 0.0),
        is_polygon=is_polygon,
    )


@pytest.mark.parametrize("pos", [0, 1, 2, 3, 4, 8, 9, 10])
def test_union_matches_full_scan_on_edge_cases(pos):
    gdf = _synthetic()
    r = _resolved(gdf.iloc[pos])
    assert analysis.schedules_for_all_matching_rows(gdf, r) == _ref_union(gdf, r)


def test_union_merges_sf_alameda_and_border_shapes():
    gdf = _synthetic()
    even, odd = analysis.schedules_for_all_matching_rows(gdf, _resolved(gdf.iloc[0]))
    assert [e[0] for e in even] == ["F13", "ME", "SE", "THE", "W2", "WE"]
    assert [o[0] for o in odd] == ["F24", "TE"]
    curb, _ = analysis.schedules_for_all_matching_rows(gdf, _resolved(gdf.iloc[9]))
    assert [e[0] for e in curb] == ["MWF"]


def test_tiles_merge_rows_as_the_card():
    gdf = _synthetic()
    recs = [r for r in tiles.merge_segment_rows(gdf) if r["render_type"] == "line"]
    # Block a (with its overlapping rows), block b, the curved street, the other curb line.
    assert len(recs) == 4
    block_a = next(r for r in recs if {"code": "ME"}.items() <= r["schedule"][0].items())
    even, odd = analysis.schedules_for_all_matching_rows(gdf, _resolved(gdf.iloc[0]))
    got = sorted((s["side"], s["code"]) for s in block_a["schedule"])
    assert got == sorted([("even", e[0]) for e in even] + [("odd", o[0]) for o in odd])


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


# ---------------------------------------------------------------------------
# Startup indexing
# ---------------------------------------------------------------------------


def test_startup_prebuilds_indexes(app_client):
    from broombuster.api import state

    gdf = state.region_gdf("chicago")
    assert gdf.has_sindex


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
        raise AssertionError("/api/check must not call Nominatim")

    monkeypatch.setattr(gps, "_nominatim", _boom)
    data = client.post("/api/check", json=_OAKLAND).json()
    assert data["address_pending"] is True
    assert data["address"] == "Chestnut St, Oakland"


def test_address_endpoint_adds_house_number_then_check_uses_cache(client, monkeypatch):
    monkeypatch.setattr(
        gps, "_nominatim", lambda lat, lon: {"road": "Chestnut Street", "house_number": "2931"}
    )
    street = "Chestnut St"
    client.post("/api/check", json=_OAKLAND)
    resp = client.get("/api/address", params=_OAKLAND)
    assert resp.status_code == 200
    assert resp.json()["address"].startswith(f"2931 {street}")
    # Now cached: /check answers with the number and nothing pending.
    data = client.post("/api/check", json=_OAKLAND).json()
    assert data["address"].startswith(f"2931 {street}")
    assert data["address_pending"] is False


def test_zone_address_is_never_pending(client):
    data = client.post(
        "/api/check", json={"lat": 41.8781, "lon": -87.6298, "region": "chicago"}
    ).json()
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
    resp = app_client.post("/api/check", json=body)
    assert resp.status_code == 200, resp.text
    assert time.perf_counter() - t0 < 1.0


def test_geocoder_backlog_is_skipped_not_queued(monkeypatch):
    gps._cache.clear()
    monkeypatch.setattr(gps, "_nominatim", lambda lat, lon: pytest.fail("queued"))
    held = [gps._slots.acquire() for _ in range(3)]  # all slots busy
    try:
        assert gps.maybe_house_number(37.5, -122.5, "Grand Ave") is None
    finally:
        for _ in held:
            gps._slots.release()
