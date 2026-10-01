"""
Performance tests: data loading and /check responses must stay within
acceptable time bounds.

These tests catch regressions where schema changes, new cities, or
normalizer complexity cause loading to become unacceptably slow.

Thresholds (generous to tolerate CI variability):
  - City FGB load (warm, from disk): < 10 s
  - Region FGB load (all cities combined): < 30 s
  - Warm API /check: < 1 s
"""
import os
import time

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

    # Clear the in-process FGB cache so we always measure a real disk read.
    data_loader._read_fgb.cache_clear()

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

    data_loader._read_fgb.cache_clear()

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
# Warm /check latency — guards the indexed schedule union / vectorised resolver
# ---------------------------------------------------------------------------

def test_warm_check_is_fast(app_client):
    """A warm /check on a long multi-row street (Market St, SF) stays well
    under 1 s; it is ~5 ms on a Raspberry Pi 5 (was ~230 ms before indexing)."""
    body = {"lat": 37.7749, "lon": -122.4194, "region": "bay_area"}
    app_client.post("/check", json=body)  # warm: cities, sindex, segment index
    t0 = time.perf_counter()
    resp = app_client.post("/check", json=body)
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, resp.text
    assert elapsed < 1.0, f"warm /check took {elapsed:.2f}s — expected < 1s"
