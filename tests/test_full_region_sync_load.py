"""
Test that `POST /check` with `full_region=true` returns a populated
geojson even when the server's in-memory per-city caches are empty
(cold-start / first-request scenario).

Note: the frontend no longer sends `full_region=true` — it uses the
tile-based incremental fetch path instead. This test exercises the API
path as a server-side contract: a `full_region` request must still
work and return complete data, which is useful for debugging and for
any direct API consumers.
"""
import os
os.environ.setdefault("DEV_MODE", "1")

from fastapi.testclient import TestClient
from broombuster.api import app as api_mod
from broombuster import data_loader
import pytest


def test_full_region_triggers_sync_load():
    # Choose bay_area center point used elsewhere
    lat, lon = 37.821326, -122.280705

    # Clear any in-memory caches to simulate a cold server
    api_mod._city_gdfs.clear()
    api_mod._city_gdfs_3857.clear()
    api_mod._city_events.clear()
    api_mod._region_combined.clear()

    with TestClient(api_mod.app) as client:
        resp = client.post("/check", json={"lat": lat, "lon": lon, "region": "bay_area", "full_region": True})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        geo = data.get("geojson") or {}
        features = geo.get("features", [])
        assert features, "full_region request returned no features after sync load"
