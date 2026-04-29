"""
Performance tests: data loading and tile-based API responses must stay
within acceptable time bounds.

These tests catch regressions where schema changes, new cities, or
normalizer complexity cause loading to become unacceptably slow.

Thresholds (generous to tolerate CI variability):
  - City FGB load (warm, from disk): < 10 s
  - Region FGB load (all cities combined): < 30 s
  - API /check tile request (data already in memory): < 3 s
"""
import os
import time

os.environ.setdefault("DEV_MODE", "1")

import pytest
from broombuster import data_loader
from broombuster.cities import CITIES, REGIONS


# ---------------------------------------------------------------------------
# FGB load timing — exercises the fast path (prebuilt FlatGeobuf on disk)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("city_key", list(CITIES.keys()))
def test_city_fgb_loads_within_10s(city_key):
    city = CITIES[city_key]
    fgb = city.get("fgb_path")
    if not fgb:
        pytest.skip(f"{city_key}: no fgb_path configured")
    root = os.path.dirname(os.path.dirname(__file__))
    if not os.path.exists(os.path.join(root, fgb)):
        pytest.skip(f"{city_key}: FGB not built yet")

    # Clear the in-process GDF cache so we always measure a real disk read.
    data_loader._GDF_CACHE.clear()

    t0 = time.perf_counter()
    gdf = data_loader.load_city_data(city_key)
    elapsed = time.perf_counter() - t0

    assert not gdf.empty, f"{city_key}: loaded GDF is empty"
    assert elapsed < 10.0, (
        f"{city_key}: FGB load took {elapsed:.2f}s — expected < 10s. "
        "Check for schema changes or normalizer regressions."
    )


@pytest.mark.parametrize("region_key", list(REGIONS.keys()))
def test_region_loads_within_30s(region_key):
    """Combined region load (all cities concatenated) must stay under 30 s."""
    region = REGIONS[region_key]
    # Skip if any city in the region has no FGB yet
    root = os.path.dirname(os.path.dirname(__file__))
    for ck in region["cities"]:
        fgb = CITIES[ck].get("fgb_path")
        if not fgb or not os.path.exists(os.path.join(root, fgb)):
            pytest.skip(f"{region_key}: FGB for {ck} not built yet")

    data_loader._GDF_CACHE.clear()

    t0 = time.perf_counter()
    gdf = data_loader.load_region_data(region_key)
    elapsed = time.perf_counter() - t0

    assert not gdf.empty, f"{region_key}: combined GDF is empty"
    assert elapsed < 30.0, (
        f"{region_key}: region load took {elapsed:.2f}s — expected < 30s."
    )


# ---------------------------------------------------------------------------
# Second load: cache hit must be near-instant (< 0.5 s)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("city_key", list(CITIES.keys()))
def test_city_second_load_uses_cache(city_key):
    city = CITIES[city_key]
    fgb = city.get("fgb_path")
    if not fgb:
        pytest.skip(f"{city_key}: no fgb_path configured")
    root = os.path.dirname(os.path.dirname(__file__))
    if not os.path.exists(os.path.join(root, fgb)):
        pytest.skip(f"{city_key}: FGB not built yet")

    # Prime the cache
    data_loader.load_city_data(city_key)

    t0 = time.perf_counter()
    gdf = data_loader.load_city_data(city_key)
    elapsed = time.perf_counter() - t0

    assert not gdf.empty
    assert elapsed < 0.5, (
        f"{city_key}: cached load took {elapsed:.3f}s — should be < 0.5s. "
        "The GDF cache may not be working."
    )


# ---------------------------------------------------------------------------
# API tile request timing — data already in memory
# ---------------------------------------------------------------------------

def test_api_tile_request_is_fast():
    """
    A tile-based /check request (no full_region, just tiles=[...]) must
    return in under 3 seconds when city data is already loaded.
    """
    from fastapi.testclient import TestClient
    from broombuster.api import app as api_mod

    lat, lon = 37.821326, -122.280705

    with TestClient(api_mod.app) as client:
        # Warm: ensure cities are loaded
        client.post("/check", json={"lat": lat, "lon": lon, "region": "bay_area"})

        # Tile request — should hit cached GDFs
        t0 = time.perf_counter()
        resp = client.post("/check", json={
            "lat": lat, "lon": lon,
            "region": "bay_area",
            "tiles": ["13/1309/3166"],
        })
        elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, resp.text
    assert elapsed < 3.0, (
        f"Tile request took {elapsed:.2f}s — expected < 3s after warm cache."
    )
