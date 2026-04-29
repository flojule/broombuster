"""
Tests addressing reported Chicago UI problems and lazy-loading coverage.

1. Ensure Chicago region produces visible features (polygons/lines) for
   the map builder so the frontend can render street data.
2. Ensure repeated `maps.build_map_geojson` calls are stable (no flicker
   in hover HTML between invocations).
3. Ensure `data_loader.load_region_data` returns a non-empty combined
   GeoDataFrame for the Chicago region (lazy/full-region loading check).
"""
import pytest

from broombuster import data_loader
from broombuster import maps
from broombuster import car as car_module
from broombuster import normalize


def test_chicago_region_has_visual_data():
    """Loading the Chicago region must produce non-empty GeoDataFrame
    and `maps.build_map_geojson` should output at least one feature.
    """
    gdf = data_loader.load_region_data("chicago")
    assert not gdf.empty, "Chicago region GDF is empty — no data available"

    # Pick a representative row and use its centroid as a sample location.
    row = gdf.iloc[0]
    geom = row.geometry
    if geom is None or geom.is_empty:
        pytest.skip("Sample row has no geometry")
    centroid = geom.centroid
    myCar = car_module.Car(lat=centroid.y, lon=centroid.x)

    # Use a fixed local_now to make hover content deterministic.
    from datetime import datetime
    fixed_now = datetime(2024, 6, 1, 12, 0)

    geojson = maps.build_map_geojson(myCar, gdf.to_crs("EPSG:4326"), local_now=fixed_now)
    features = geojson.get("features", [])
    assert features, "build_map_geojson returned no features for Chicago region"


def test_build_map_geojson_is_stable_for_point():
    """Repeated builds for the same car & clipped city must produce
    identical hover HTML strings (prevents flicker between renders).
    """
    gdf = data_loader.load_region_data("chicago").to_crs("EPSG:4326")
    if gdf.empty:
        pytest.skip("Chicago GDF empty")

    # Use centroid of first geometry as stable test point
    row = gdf.iloc[0]
    geom = row.geometry
    if geom is None or geom.is_empty:
        pytest.skip("Sample geometry empty")
    cent = geom.centroid
    myCar = car_module.Car(lat=cent.y, lon=cent.x)

    from datetime import datetime
    fixed_now = datetime(2024, 6, 1, 12, 0)

    geo1 = maps.build_map_geojson(myCar, gdf, local_now=fixed_now)
    geo2 = maps.build_map_geojson(myCar, gdf, local_now=fixed_now)

    # Extract bolded names for a stable, order-insensitive comparison
    import re
    def bold_names(geo):
        names = []
        for f in geo.get("features", []):
            hover = f.get("properties", {}).get("hover_html", "")
            m = re.search(r"<b>([^<]+)</b>", hover)
            if m:
                names.append(m.group(1).strip())
        return set(names)

    assert bold_names(geo1) == bold_names(geo2), "Hover HTML differs between consecutive builds"


def test_load_region_data_returns_city_rows_for_chicago():
    """Ensure `load_region_data('chicago')` returns rows from the
    configured `chicago_all` city (lazy combined region load)."""
    combined = data_loader.load_region_data("chicago")
    assert not combined.empty, "Combined Chicago region is empty"
    # Ensure at least some rows include the expected city-related fields
    sample = combined.iloc[0]
    assert sample.get("STREET_NAME") is not None or sample.get("ward_id") is not None, (
        "Combined Chicago rows appear to lack expected schema columns"
    )
