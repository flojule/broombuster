"""
Tests for region-to-region transition correctness.

Past problem: switching from Bay Area to Chicago (or back) via the top
dropdown would sometimes show stale/wrong data because:
  1. _renderedRegion was not updated by setNearestRegion() in the frontend
     (now fixed: setNearestRegion() always syncs _renderedRegion).
  2. Tile requests sent the wrong region key after a switch.

These tests exercise the API side: tile/bbox requests with explicit
`region` fields must return features from the requested region only,
and repeating a request for the same region must return identical data.
"""
import os

os.environ.setdefault("DEV_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from broombuster import data_loader
from broombuster.api import app as api_mod
from broombuster.cities import REGIONS

# ---------------------------------------------------------------------------
# Fixture: preload both regions so tests don't measure load time
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client_with_data(tmp_path_factory):
    """TestClient with both bay_area and chicago pre-loaded."""
    # Preload caches to avoid background-thread races inside the test.
    for region_key in ("bay_area", "chicago"):
        for ck in REGIONS[region_key]["cities"]:
            try:
                g = data_loader.load_city_data(ck).copy()
                g["_city"] = ck
                api_mod._city_gdfs[ck]      = g.to_crs("EPSG:4326")
                api_mod._city_gdfs_3857[ck] = g.to_crs("EPSG:3857")
                import threading
                ev = threading.Event()
                ev.set()
                api_mod._city_events[ck]    = ev
                api_mod._city_loaded_at[ck] = 0
            except FileNotFoundError:
                pytest.skip(f"Data for {ck} not available; build the FGBs")
    api_mod._region_combined.clear()

    with TestClient(api_mod.app) as client:
        yield client


# ---------------------------------------------------------------------------
# Region isolation: tile request must return features from the right region
# ---------------------------------------------------------------------------

def test_bay_area_tiles_return_bay_area_features(client_with_data):
    """Tile request with region=bay_area must return line features (street segments)."""
    lat, lon = 37.821326, -122.280705  # Oakland, Chestnut St area
    resp = client_with_data.post("/check", json={
        "lat": lat, "lon": lon,
        "region": "bay_area",
        "tiles": ["13/1309/3166"],
    })
    assert resp.status_code == 200, resp.text
    geo = resp.json().get("geojson") or {}
    features = geo.get("features", [])
    assert features, "Bay Area tile request returned no features"
    render_types = {f["properties"].get("render_type") for f in features}
    assert "line" in render_types, (
        f"Expected street-line features in Bay Area tile, got: {render_types}"
    )


def test_chicago_tiles_return_polygon_features(client_with_data):
    """Tile request with region=chicago must return polygon features (ward zones)."""
    lat, lon = 41.8781, -87.6298  # Chicago city center
    resp = client_with_data.post("/check", json={
        "lat": lat, "lon": lon,
        "region": "chicago",
        "tiles": ["13/2097/3040"],
    })
    assert resp.status_code == 200, resp.text
    geo = resp.json().get("geojson") or {}
    features = geo.get("features", [])
    assert features, "Chicago tile request returned no features"
    render_types = {f["properties"].get("render_type") for f in features}
    assert "polygon" in render_types, (
        f"Expected polygon ward zones in Chicago tile, got: {render_types}"
    )


# ---------------------------------------------------------------------------
# Idempotency: same tile request for the same region returns identical data
# ---------------------------------------------------------------------------

def test_bay_area_repeated_request_is_idempotent(client_with_data):
    """Repeating the same bay_area tile request must return identical features."""
    lat, lon = 37.821326, -122.280705
    payload = {"lat": lat, "lon": lon, "region": "bay_area", "tiles": ["13/1309/3166"]}

    r1 = client_with_data.post("/check", json=payload)
    r2 = client_with_data.post("/check", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200

    f1 = {str(f["geometry"]) for f in (r1.json().get("geojson") or {}).get("features", [])}
    f2 = {str(f["geometry"]) for f in (r2.json().get("geojson") or {}).get("features", [])}
    assert f1 == f2, "Repeated tile request returned different feature sets"


def test_chicago_repeated_request_is_idempotent(client_with_data):
    """Repeating the same chicago tile request must return identical features."""
    lat, lon = 41.8781, -87.6298
    payload = {"lat": lat, "lon": lon, "region": "chicago", "tiles": ["13/2097/3040"]}

    r1 = client_with_data.post("/check", json=payload)
    r2 = client_with_data.post("/check", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200

    f1 = {str(f["geometry"]) for f in (r1.json().get("geojson") or {}).get("features", [])}
    f2 = {str(f["geometry"]) for f in (r2.json().get("geojson") or {}).get("features", [])}
    assert f1 == f2, "Repeated Chicago tile request returned different feature sets"


# ---------------------------------------------------------------------------
# Cross-region: switching back gives the original region's data
# ---------------------------------------------------------------------------

def test_bay_area_then_chicago_then_bay_area(client_with_data):
    """
    Sequential requests: bay_area → chicago → bay_area.
    The final bay_area response must match the first one (no cross-region bleed).
    """
    ba_payload = {"lat": 37.821326, "lon": -122.280705, "region": "bay_area",
                  "tiles": ["13/1309/3166"]}
    ch_payload = {"lat": 41.8781, "lon": -87.6298, "region": "chicago",
                  "tiles": ["13/2097/3040"]}

    r_ba1 = client_with_data.post("/check", json=ba_payload)
    _     = client_with_data.post("/check", json=ch_payload)
    r_ba2 = client_with_data.post("/check", json=ba_payload)

    assert r_ba1.status_code == 200 and r_ba2.status_code == 200

    geoms1 = {str(f["geometry"]) for f in (r_ba1.json().get("geojson") or {}).get("features", [])}
    geoms2 = {str(f["geometry"]) for f in (r_ba2.json().get("geojson") or {}).get("features", [])}

    assert geoms1, "First bay_area response has no features"
    assert geoms1 == geoms2, (
        "bay_area data changed after a chicago request — possible cross-region state bleed.\n"
        f"  Only in first:  {list(geoms1 - geoms2)[:3]}\n"
        f"  Only in second: {list(geoms2 - geoms1)[:3]}"
    )
