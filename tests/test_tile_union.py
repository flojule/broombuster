"""
Test that assembling per-tile GeoJSONs yields the same result as building
one GeoJSON for the combined bbox. This reproduces client-side flicker
when tiles and merged bbox responses differ.
"""
from shapely.geometry import box, shape
import pytest
from broombuster import data_loader
from broombuster import maps
from broombuster import car as car_module


def _fixed_now():
    from datetime import datetime
    return datetime(2024, 6, 1, 12, 0)


def _feature_id(f):
    # Use geometry WKT + hover_html as a stable identifier for comparison
    geom = f.get("geometry")
    props = f.get("properties", {})
    hover = props.get("hover_html") or ""
    try:
        g = shape(geom)
        wkt = g.wkt
    except Exception:
        wkt = str(geom)
    return (wkt, hover.strip())


def test_tile_union_equals_bbox():
    # Representative center in Bay Area (Chestnut area used earlier)
    lat, lon = 37.821326, -122.280705

    # Build a combined bbox (approx 4 km across)
    min_lat, min_lon = lat - 0.02, lon - 0.02
    max_lat, max_lon = lat + 0.02, lon + 0.02
    combined_box = box(min_lon, min_lat, max_lon, max_lat)

    region_gdf = data_loader.load_region_data("bay_area").to_crs("EPSG:4326")
    combined_gdf = region_gdf[region_gdf.geometry.intersects(combined_box)]
    if combined_gdf.empty:
        pytest.skip("No data in combined bbox")

    myCar = car_module.Car(lat=lat, lon=lon)
    full_geo = maps.build_map_geojson(myCar, combined_gdf, local_now=_fixed_now())
    full_ids = set(_feature_id(f) for f in full_geo.get("features", []))

    # Split bbox into 2x2 tiles
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2
    tiles = [
        box(min_lon, min_lat, mid_lon, mid_lat),
        box(mid_lon, min_lat, max_lon, mid_lat),
        box(min_lon, mid_lat, mid_lon, max_lat),
        box(mid_lon, mid_lat, max_lon, max_lat),
    ]

    tile_ids = set()
    for t in tiles:
        tgdf = region_gdf[region_gdf.geometry.intersects(t)]
        if tgdf.empty:
            continue
        geo = maps.build_map_geojson(myCar, tgdf, local_now=_fixed_now())
        for f in geo.get("features", []):
            tile_ids.add(_feature_id(f))

    # The union of tile features should equal the full bbox features
    assert full_ids == tile_ids, (
        "Mismatch between full-bbox and per-tile feature sets",
        {
            "only_in_full": list(full_ids - tile_ids)[:5],
            "only_in_tiles": list(tile_ids - full_ids)[:5],
        },
    )
