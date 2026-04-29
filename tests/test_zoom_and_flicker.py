"""
Tests to reproduce and guard against UI flicker and missing-zone issues
when clients request data at different zoom / bbox sizes.

These tests assert stability of `maps.build_map_geojson` across repeated
builds and across bbox sizes (simulating zoom in / out), and ensure that
Chicago polygon zones are present both at small and large clip boxes.
"""
import pytest
from shapely.geometry import box
from broombuster import data_loader
from broombuster import maps
from broombuster import car as car_module


def _fixed_now():
    from datetime import datetime
    return datetime(2024, 6, 1, 12, 0)


def test_build_geojson_stable_across_bbox_sizes():
    """A segment visible in a small bbox should still be present when the
    bbox is expanded (no flicker due to different clipping logic).
    """
    lat, lon = 37.821326, -122.280705
    # Small bbox ~200m, large bbox ~2km
    small = box(lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001)
    large = box(lon - 0.02, lat - 0.02, lon + 0.02, lat + 0.02)

    gdf = data_loader.load_region_data("bay_area").to_crs("EPSG:4326")
    small_gdf = gdf[gdf.geometry.intersects(small)]
    large_gdf = gdf[gdf.geometry.intersects(large)]

    if small_gdf.empty:
        pytest.skip("No segments in small bbox to validate")

    myCar = car_module.Car(lat=lat, lon=lon)
    geo_small = maps.build_map_geojson(myCar, small_gdf, local_now=_fixed_now())
    geo_large = maps.build_map_geojson(myCar, large_gdf, local_now=_fixed_now())

    names_small = set()
    for f in geo_small.get("features", []):
        hover = f.get("properties", {}).get("hover_html", "")
        if "<b>" in hover:
            start = hover.find("<b>") + 3
            end = hover.find("</b>", start)
            names_small.add(hover[start:end].strip())

    names_large = set()
    for f in geo_large.get("features", []):
        hover = f.get("properties", {}).get("hover_html", "")
        if "<b>" in hover:
            start = hover.find("<b>") + 3
            end = hover.find("</b>", start)
            names_large.add(hover[start:end].strip())

    # Every small-bbox name should be present in the large-bbox result.
    assert names_small, "No labeled features in small bbox"
    missing = names_small - names_large
    assert not missing, f"Features missing when expanding bbox: {missing!r}"


def test_chicago_zone_present_at_multiple_scales():
    """Chicago uses polygon zones; they must be present regardless of
    requested bbox size (zoom in or out).
    """
    gdf = data_loader.load_region_data("chicago").to_crs("EPSG:4326")
    if gdf.empty:
        pytest.skip("Chicago GDF empty")

    row = gdf.iloc[0]
    geom = row.geometry
    if geom is None or geom.is_empty:
        pytest.skip("Sample geometry empty")

    cent = geom.centroid
    lat, lon = cent.y, cent.x

    # Very small and very large bboxes around centroid
    small = box(lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001)
    large = box(lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)

    small_gdf = gdf[gdf.geometry.intersects(small)]
    large_gdf = gdf[gdf.geometry.intersects(large)]

    myCar = car_module.Car(lat=lat, lon=lon)
    geo_small = maps.build_map_geojson(myCar, small_gdf, local_now=_fixed_now())
    geo_large = maps.build_map_geojson(myCar, large_gdf, local_now=_fixed_now())

    # Count polygon features in both builds
    def poly_count(geo):
        return sum(1 for f in geo.get("features", []) if f.get("properties", {}).get("render_type") == "polygon")

    ps = poly_count(geo_small)
    pl = poly_count(geo_large)

    assert ps > 0 or pl > 0, "No polygon zones present at any scale for Chicago"
    # If polygons exist in the small view, they should also be visible in the large view
    assert ps == 0 or pl >= ps, "Polygon zones disappeared when zooming out"
