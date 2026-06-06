"""
Simulate the UI sequence triggered by the top-banner region dropdown:
1) client requests the full region (full_region=true) to preload data,
2) client immediately requests a small viewport bbox for rendering.

This test asserts that the set of features covering the viewport is the
same whether the server was previously asked for the full region or not.
If this fails in the browser it reproduces the flicker caused by differing
responses after region selection.
"""
import os

os.environ.setdefault("DEV_MODE", "1")

from fastapi.testclient import TestClient

from broombuster import data_loader
from broombuster.api import app as api_mod


def _extract_feature_keys(geo):
    keys = set()
    for f in geo.get("features", []):
        props = f.get("properties", {})
        hover = (props.get("hover_html") or "").strip()
        geom = f.get("geometry")
        keys.add((hover, str(geom)))
    return keys


def test_full_region_then_bbox_consistency():
    # Use a point in the bay area (Chestnut sample)
    lat, lon = 37.821326, -122.280705

    # Preload the city GDFs into the API module to avoid background-load races

    from broombuster.cities import REGIONS
    # Load each city synchronously and populate api module caches
    for ck in REGIONS["bay_area"]["cities"]:
        g4 = data_loader.load_city_data(ck).copy()
        api_mod._city_gdfs[ck] = g4.to_crs("EPSG:4326")
        api_mod._city_gdfs_3857[ck] = g4.to_crs("EPSG:3857")
        api_mod._city_events[ck] = type(
            "E", (), {"is_set": lambda self: True, "set": lambda self: None}
        )()
        api_mod._city_loaded_at[ck] = 0
    # Clear combined cache
    api_mod._region_combined.clear()

    with TestClient(api_mod.app) as client:
        # 1) preload full region
        resp_full = client.post(
            "/check", json={"lat": lat, "lon": lon, "region": "bay_area", "full_region": True}
        )
        assert resp_full.status_code == 200, resp_full.text
        geo_full = resp_full.json().get("geojson") or {"features": []}

        # 2) immediately request a small bbox
        pad = 0.002
        bbox = [lat - pad, lon - pad, lat + pad, lon + pad]
        resp_bbox = client.post(
            "/check", json={"lat": lat, "lon": lon, "region": "bay_area", "bbox": bbox}
        )
        assert resp_bbox.status_code == 200, resp_bbox.text
        geo_bbox = resp_bbox.json().get("geojson") or {"features": []}

    keys_full = _extract_feature_keys(geo_full)
    keys_bbox = _extract_feature_keys(geo_bbox)

    # The bbox response should be a subset of the full-region response's features
    missing = {k for k in keys_bbox if k not in keys_full}
    assert not missing, f"Features present in bbox but missing from full-region preload: {missing}"


def test_bbox_then_full_region_consistency():
    # Reverse order: request bbox first, then full_region — UI may do either.
    lat, lon = 37.821326, -122.280705

    # Preload cities to avoid background thread races
    from broombuster.cities import REGIONS
    for ck in REGIONS["bay_area"]["cities"]:
        g4 = data_loader.load_city_data(ck).copy()
        api_mod._city_gdfs[ck] = g4.to_crs("EPSG:4326")
        api_mod._city_gdfs_3857[ck] = g4.to_crs("EPSG:3857")
        api_mod._city_events[ck] = type(
            "E", (), {"is_set": lambda self: True, "set": lambda self: None}
        )()
        api_mod._city_loaded_at[ck] = 0
    api_mod._region_combined.clear()

    with TestClient(api_mod.app) as client:
        pad = 0.002
        bbox = [lat - pad, lon - pad, lat + pad, lon + pad]
        resp_bbox = client.post(
            "/check", json={"lat": lat, "lon": lon, "region": "bay_area", "bbox": bbox}
        )
        assert resp_bbox.status_code == 200, resp_bbox.text
        geo_bbox = resp_bbox.json().get("geojson") or {"features": []}

        resp_full = client.post(
            "/check", json={"lat": lat, "lon": lon, "region": "bay_area", "full_region": True}
        )
        assert resp_full.status_code == 200, resp_full.text
        geo_full = resp_full.json().get("geojson") or {"features": []}

    keys_full = _extract_feature_keys(geo_full)
    keys_bbox = _extract_feature_keys(geo_bbox)

    missing = {k for k in keys_bbox if k not in keys_full}
    assert not missing, f"Features present in bbox but missing after full-region load: {missing}"
