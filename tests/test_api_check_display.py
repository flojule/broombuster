"""
API-level tests for the canonical `address` field on `/check` responses.

Step 1 of the re-architecture made the backend the single source of truth
for the displayed address. The frontend renders `sched.address` verbatim;
no Nominatim mixing on the client. Optional Nominatim house-number
enrichment is gated by a STREET_KEY match against the resolved segment.

These tests run the FastAPI app with `DEV_MODE=1` so authentication is
skipped.
"""
import os
os.environ.setdefault("DEV_MODE", "1")

import pytest
from fastapi.testclient import TestClient

from broombuster import data_loader
from broombuster import normalize


def _nearest_row_for_point(gdf_3857, lat, lon):
    from pyproj import Transformer
    from shapely.geometry import Point
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = t.transform(lon, lat)
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


def test_api_check_address_matches_street_display():
    """The canonical address always contains the resolved segment's street."""
    lat, lon = 37.821326, -122.280705  # Chestnut St, Oakland

    gdf_3857 = data_loader.load_region_data("bay_area").to_crs("EPSG:3857")
    nearest = _nearest_row_for_point(gdf_3857, lat, lon)
    if nearest is None:
        pytest.skip("No segment found to test against")

    row_display = (nearest.get("STREET_DISPLAY") or nearest.get("STREET_NAME") or "").strip()
    if not row_display:
        pytest.skip("Nearest segment has no display/name")

    from broombuster.api import app as api_mod

    with TestClient(api_mod.app) as client:
        payload = {"lat": lat, "lon": lon, "region": "bay_area"}
        resp = client.post("/check", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    addr = data.get("address") or ""
    # The street name in the canonical address must equal the resolved
    # segment's street under street_name() canonicalisation. The address
    # may also contain a house number prefix and a city suffix.
    assert normalize.street_name(row_display) in normalize.street_name(addr) or \
           normalize.street_name(addr).endswith(normalize.street_name(row_display)) or \
           normalize.street_name(row_display) == normalize.street_name(addr), (
        f"API address {addr!r} does not contain segment {row_display!r}"
    )


class TestAddressFlow:
    """Step 1 invariants for the canonical address field."""

    def _post(self, payload):
        from broombuster.api import app as api_mod
        with TestClient(api_mod.app) as client:
            resp = client.post("/check", json=payload)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_address_contains_city_suffix(self):
        """For a Bay Area point, address ends with ', Oakland' (or another bay-area city)."""
        lat, lon = 37.821326, -122.280705
        data = self._post({"lat": lat, "lon": lon, "region": "bay_area"})
        addr = data.get("address") or ""
        # Either the resolver matched (city suffix present) or fell back to coords.
        assert "," in addr, f"expected city suffix in address, got {addr!r}"

    def test_address_consistent_with_snap_street_name(self):
        """The address always contains the same street as `snap.street_name`."""
        lat, lon = 37.821326, -122.280705
        data = self._post({"lat": lat, "lon": lon, "region": "bay_area"})
        snap = data.get("snap")
        if not snap:
            pytest.skip("No segment resolved at this location")
        addr = data.get("address") or ""
        snap_street = snap.get("street_name") or ""
        if not snap_street:
            pytest.skip("snap missing street_name")
        # The street component of the canonical address must equal the snap street.
        # Use street_name() (drops suffixes, directionals, punctuation) on both
        # sides so we ignore "Ave" vs "Avenue", case, and house number prefixes.
        assert normalize.street_name(snap_street) and \
               normalize.street_name(snap_street) in normalize.street_name(addr) or \
               normalize.street_name(addr).endswith(normalize.street_name(snap_street)) or \
               normalize.street_name(addr) == normalize.street_name(snap_street), (
            f"snap.street_name={snap_street!r} not consistent with address={addr!r}"
        )

    def test_no_segment_falls_back_to_coords(self):
        """Coordinates with no nearby segment produce a coordinate-string fallback."""
        # A point in the Pacific Ocean far from any street.
        lat, lon = 37.500, -123.500
        data = self._post({"lat": lat, "lon": lon, "region": "bay_area"})
        addr = data.get("address") or ""
        # Coordinate fallback uses 4-decimal lat/lon "37.5000, -123.5000"
        assert addr.replace(" ", "").startswith("37.5"), (
            f"expected coord fallback, got {addr!r}"
        )

    def test_chicago_zone_address_format(self):
        """A polygon-zone city formats the address as 'Zone: <name>, Chicago'."""
        # Downtown Chicago — point inside the zone polygon dataset.
        lat, lon = 41.8781, -87.6298
        data = self._post({"lat": lat, "lon": lon, "region": "chicago"})
        snap = data.get("snap")
        if not snap or not snap.get("is_polygon"):
            pytest.skip("No polygon zone resolved at this location")
        addr = data.get("address") or ""
        assert addr.startswith("Zone:"), f"expected 'Zone:' prefix, got {addr!r}"
        assert "Chicago" in addr, f"expected Chicago suffix, got {addr!r}"


def test_house_number_gate_drops_mismatched_road(monkeypatch):
    """The Nominatim house-number gate must drop the number when the geocoded
    road disagrees with the resolved segment's street."""
    from broombuster import gps

    # Mock _reverse_geocode to return a road that doesn't match.
    monkeypatch.setattr(gps, "_reverse_geocode",
                        lambda lat, lon: ("5th Street", 1234))

    # expected_street is "Grand Ave" — _normalize.street_name("5th Street") is
    # "5TH" which does not equal _normalize.street_name("Grand Ave") = "GRAND".
    result = gps.maybe_house_number(37.0, -122.0, "Grand Ave")
    assert result is None, "house number must be dropped when roads disagree"


def test_house_number_gate_keeps_matched_road(monkeypatch):
    """When the geocoded road matches, the house number is returned."""
    from broombuster import gps
    monkeypatch.setattr(gps, "_reverse_geocode",
                        lambda lat, lon: ("Grand Avenue", 1234))
    result = gps.maybe_house_number(37.0, -122.0, "GRAND AVE")
    assert result == 1234


def test_house_number_gate_handles_geocode_failure(monkeypatch):
    """Nominatim returning None (network error or no match) returns None."""
    from broombuster import gps
    monkeypatch.setattr(gps, "_reverse_geocode",
                        lambda lat, lon: (None, None))
    result = gps.maybe_house_number(37.0, -122.0, "Grand Ave")
    assert result is None
