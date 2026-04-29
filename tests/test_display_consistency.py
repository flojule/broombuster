"""
Tests to ensure the human-facing street display used in the map
features and the nearby-streets helper are consistent with the
persisted `STREET_DISPLAY` column on the GeoDataFrame rows.

These tests guard against regressions where car cards or reverse
geocoding produce a different visible street label than the map.
"""
import pyproj
import pytest
from shapely.geometry import Point

from broombuster import data_loader
from broombuster import gps
from broombuster import maps
from broombuster import normalize
from broombuster import analysis
from broombuster import car as car_module


_CRS = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@pytest.fixture(scope="module")
def bay_area_3857():
    # Load once per test module to avoid repeated disk reads and any
    # incidental network activity. Tests assume the repository includes
    # the prebuilt FGBs so this is fast and deterministic.
    return data_loader.load_region_data("bay_area").to_crs("EPSG:3857")


def _nearest_row_for_point(gdf_3857, lat, lon):
    x, y = _CRS.transform(lon, lat)
    pt = Point(x, y)
    best_row, best_d = None, float("inf")
    for i, row in gdf_3857.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        d = pt.distance(geom)
        if d < best_d:
            best_d = d
            best_row = row
    return best_row


# A few representative points used previously in nearest-segment tests.
CASES = [
    (37.821326, -122.280705),  # Chestnut St, Oakland
    (37.830060, -122.261070),  # Telegraph Ave, Oakland
    (37.759700, -122.421200),  # Guerrero St, SF
]


def _safe_display_from_row(row):
    disp = row.get("STREET_DISPLAY") or row.get("STREET_NAME")
    if isinstance(disp, str):
        return disp.strip()


@pytest.mark.parametrize("lat,lon", CASES)
def test_nearby_street_display_matches_segment(lat, lon, bay_area_3857):
    """
    `gps.get_nearby_streets_from_gdf` should return a human-facing display
    that corresponds to the nearest segment's `STREET_DISPLAY` (or a
    display derived from `STREET_NAME`). Compare via the canonical
    comparison key to avoid incidental casing/abbrev differences.
    """
    nearby = gps.get_nearby_streets_from_gdf(lat, lon, bay_area_3857)
    if not nearby:
        pytest.skip("No nearby streets found for this point")

    nearby_display = nearby[0][0]
    nearest = _nearest_row_for_point(bay_area_3857, lat, lon)
    if nearest is None:
        pytest.skip("No segment found near test point")

    row_display = _safe_display_from_row(nearest)
    assert row_display, "Nearest segment has no display/name"

    # Compare canonical keys — tests care about semantic equality, not
    # exact punctuation/casing emitted by different data sources.
    assert normalize.street_name(nearby_display) == normalize.street_name(row_display), (
        f"Nearby-streets display {nearby_display!r} does not match segment "
        f"display {row_display!r}"
    )


@pytest.mark.parametrize("lat,lon", CASES)
def test_map_hover_uses_street_display(lat, lon, bay_area_3857):
    """
    `maps.build_map_geojson` should include the persisted `STREET_DISPLAY`
    in the hover HTML for the nearest segment so the map label matches
    the street display shown elsewhere (e.g., car cards).
    """
    # Build a small clipped GDF around the point so the geojson contains
    # the nearest segment as a feature.
    from shapely.geometry import box

    pad_deg = 0.001
    clip = box(lon - pad_deg, lat - pad_deg, lon + pad_deg, lat + pad_deg)
    gdf_4326 = data_loader.load_region_data("bay_area").to_crs("EPSG:4326")
    clipped = gdf_4326[gdf_4326.geometry.intersects(clip)]
    if clipped.empty:
        pytest.skip("No segments in clipped area for this point")

    # Find nearest row using the 3857 dataset for accurate distance
    gdf_3857 = data_loader.load_region_data("bay_area").to_crs("EPSG:3857")
    nearest = _nearest_row_for_point(gdf_3857, lat, lon)
    if nearest is None:
        pytest.skip("No nearest segment found")

    row_display = _safe_display_from_row(nearest)
    if not row_display:
        pytest.skip("Nearest segment has no STREET_DISPLAY / STREET_NAME")

    # Construct a minimal Car object for map rendering. Pass a fixed
    # local_now so date-sensitive hover text is deterministic and stable
    # across test runs (prevents flicker due to 'today' vs 'tomorrow').
    from datetime import datetime
    myCar = car_module.Car(lat=lat, lon=lon)
    myCity = clipped
    fixed_now = datetime(2024, 6, 1, 12, 0)

    geojson = maps.build_map_geojson(myCar, myCity, local_now=fixed_now)
    features = geojson.get("features", [])
    assert features, "Map geojson has no features"

    # Extract the bolded name from hover html (<b>NAME</b>) and compare
    # canonical keys to be robust to punctuation/casing differences.
    import re
    found = False
    for f in features:
        props = f.get("properties", {})
        hover = props.get("hover_html") or ""
        m = re.search(r"<b>([^<]+)</b>", hover)
        if not m:
            continue
        hover_name = m.group(1).strip()
        if normalize.street_name(hover_name) == normalize.street_name(row_display):
            found = True
            break

    assert found, f"Hover HTML did not contain STREET_DISPLAY {row_display!r}"
