"""Validate the PMTiles build inputs: merge_segment_rows and the manifest."""
import json
from pathlib import Path

from broombuster import data_loader, maps

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = _ROOT / "frontend" / "tiles" / "manifest.json"


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


def test_merge_matches_build_map_geojson_count_chicago():
    """Chicago polygons are 1:1, so merge count == build_map_geojson feature count."""
    import types

    gdf = data_loader.load_region_data("chicago")
    merged = maps.merge_segment_rows(gdf)
    car = types.SimpleNamespace(lat=41.9, lon=-87.66)
    geojson = maps.build_map_geojson(car, gdf)
    assert len(merged) == len(geojson["features"])


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


def test_manifest_features_match_merge_count_if_built():
    if not _MANIFEST.exists():
        return  # archives not built in this environment
    manifest = json.loads(_MANIFEST.read_text())
    if "chicago" not in manifest:
        return
    gdf = data_loader.load_region_data("chicago")
    records = maps.merge_segment_rows(gdf)
    # The archive carries the merged segments plus the dissolved ward outlines.
    expected = len(records) + len(maps.ward_boundary_features(records))
    assert manifest["chicago"]["features"] == expected
