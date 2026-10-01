"""Validate the PMTiles build inputs: merge_segment_rows and the ward dividers."""
from broombuster import data_loader, maps


def test_merge_segment_rows_chicago_shape():
    gdf = data_loader.load_region_data("chicago")
    records = maps.merge_segment_rows(gdf)
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
    merged = maps.merge_segment_rows(gdf)
    assert len(merged) == int((~gdf.geometry.is_empty & gdf.geometry.notna()).sum())


def test_ward_boundary_features_chicago():
    """Chicago yields one merged line feature of the ward-vs-ward dividers."""
    gdf = data_loader.load_region_data("chicago")
    records = maps.merge_segment_rows(gdf)
    wards = maps.ward_boundary_features(records)
    assert len(wards) == 1, "ward dividers must merge into a single feature"
    w = wards[0]
    assert w["render_type"] == "ward_boundary"
    assert w["geometry"].geom_type in ("LineString", "MultiLineString")
    assert not w["geometry"].is_empty
    # Line-only regions (no polygons) yield none.
    assert not maps.ward_boundary_features(
        [r for r in records if r["render_type"] == "line"]
    )
