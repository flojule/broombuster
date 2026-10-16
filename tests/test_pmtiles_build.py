"""Validate the PMTiles build inputs: merge_segment_rows and the ward dividers."""
from broombuster import data_loader, tiles


def test_merge_segment_rows_chicago_shape():
    gdf = data_loader.load_region_data("chicago")
    records = tiles.merge_segment_rows(gdf)
    assert records, "no merged records for chicago"
    for rec in records:
        assert set(rec) >= {"geometry", "render_type", "street", "city", "schedule"}
        assert rec["render_type"] in ("line", "polygon")
        assert isinstance(rec["schedule"], list)
        for entry in rec["schedule"]:
            assert set(entry) == {"code", "time", "desc", "side"}
    # Chicago is polygons, one record per zone.
    assert all(r["render_type"] == "polygon" for r in records)


def test_merge_keeps_chicago_polygons_one_to_one():
    """Chicago zones are polygons: one tile record per non-empty zone row."""
    gdf = data_loader.load_region_data("chicago")
    merged = tiles.merge_segment_rows(gdf)
    assert len(merged) == int((~gdf.geometry.is_empty & gdf.geometry.notna()).sum())


def test_ward_boundary_features_chicago():
    """Chicago yields one merged line feature of the ward-vs-ward dividers."""
    gdf = data_loader.load_region_data("chicago")
    records = tiles.merge_segment_rows(gdf)
    wards = tiles.ward_boundary_features(records)
    assert len(wards) == 1, "ward dividers must merge into a single feature"
    w = wards[0]
    assert w["render_type"] == "ward_boundary"
    assert w["geometry"].geom_type in ("LineString", "MultiLineString")
    assert not w["geometry"].is_empty
    # Line-only regions (no polygons) yield none.
    assert not tiles.ward_boundary_features(
        [r for r in records if r["render_type"] == "line"]
    )


def _rows(*rows):
    """A line GeoDataFrame: one row per dict (its "coords" become the geometry)."""
    import geopandas
    import shapely.geometry as sg
    blank = {"STREET_NAME": "MAIN ST", "_city": "x",
             "DESC_EVEN": "", "TIME_EVEN": "", "DESC_ODD": "", "TIME_ODD": ""}
    return geopandas.GeoDataFrame(
        [{**blank, **{k: v for k, v in r.items() if k != "coords"},
          "geometry": sg.LineString(r["coords"])} for r in rows], crs="EPSG:4326")


def test_even_left_follows_address_parity_and_direction():
    """A line whose sides differ records which side the even addresses are on, relative to
    the merged line's direction; a row drawn the other way round still agrees."""
    ab, ba = [(-122.0, 37.0), (-122.0, 37.001)], [(-122.0, 37.001), (-122.0, 37.0)]
    even_left = {"L_F_ADD": 100, "L_T_ADD": 198, "R_F_ADD": 101, "R_T_ADD": 199}
    even_right = {"L_F_ADD": 101, "L_T_ADD": 199, "R_F_ADD": 100, "R_T_ADD": 198}
    recs = tiles.merge_segment_rows(_rows(
        {"coords": ab, "DAY_EVEN": "ME", "DAY_ODD": None, **even_left},
        {"coords": ba, "DAY_EVEN": None, "DAY_ODD": "TE", **even_right}))
    assert [r["even_left"] for r in recs] == [True]


def test_even_left_unset_when_sides_match_or_unknown():
    ab = [(-122.0, 37.0), (-122.0, 37.001)]
    cd = [(-122.1, 37.0), (-122.1, 37.001)]
    recs = tiles.merge_segment_rows(_rows(
        {"coords": ab, "DAY_EVEN": "ME", "DAY_ODD": "ME", "L_F_ADD": 100, "L_T_ADD": 198},
        {"coords": cd, "DAY_EVEN": "ME", "DAY_ODD": "TE"}))
    assert [r["even_left"] for r in recs] == [None, None]
