"""HTTP API end to end on the real data (no network: Nominatim is never hit)."""

import pandas as pd
import pytest

from broombuster import analysis, gps, resolve

_OAKLAND = {"lat": 37.821326, "lon": -122.280705, "region": "bay_area"}  # Chestnut St
_CHICAGO = {"lat": 41.8781, "lon": -87.6298, "region": "chicago"}


@pytest.fixture
def client(app_client):
    gps._cache.clear()
    yield app_client
    gps._cache.clear()


def _check(client, payload):
    resp = client.post("/check", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_and_cities(client):
    assert client.get("/health").json() == {"status": "ok"}
    regions = client.get("/cities").json()
    assert set(regions) == {"bay_area", "chicago"}
    assert regions["chicago"]["tz"] == "America/Chicago"
    assert {"name", "center", "zoom", "tz"} <= set(regions["bay_area"])


def test_check_line_street(client, bay_area_3857):
    data = _check(client, _OAKLAND)
    assert data["region"] == "bay_area"
    assert data["address"] == "Chestnut St, Oakland"
    assert data["car_side"] in ("even", "odd", None)
    assert len(data["side_labels"]) == 2
    entries = data["schedule_even"] + data["schedule_odd"]
    assert entries and all(len(e) == 3 for e in entries)
    # The resolved segment's own schedule is part of the union.
    seg = resolve.nearest_segment(bay_area_3857, _OAKLAND["lat"], _OAKLAND["lon"]).segment
    own = [e for e in (analysis.get_schedule(seg, 0), analysis.get_schedule(seg, 1)) if e]
    assert all(list(e) in entries for e in own)


def test_check_chicago_zone(client):
    data = _check(client, _CHICAGO)
    assert data["address"].startswith("Zone: Ward ") and data["address"].endswith(", Chicago")
    assert data["address_pending"] is False
    assert data["schedule_even"] == data["schedule_odd"]
    assert all(e[0].startswith("DATES:") for e in data["schedule_even"])


def test_check_region_defaults_to_nearest(client):
    assert _check(client, {"lat": _CHICAGO["lat"], "lon": _CHICAGO["lon"]})["region"] == "chicago"


def test_check_without_street_falls_back_to_coords(client):
    data = _check(client, {"lat": 37.5, "lon": -123.5, "region": "bay_area"})
    assert data["address"] == "37.5000, -123.5000"
    assert data["schedule_even"] == data["schedule_odd"] == []


def test_check_rejects_bad_coordinates(client):
    assert client.post("/check", json={"lat": 91, "lon": 0}).status_code == 422


@pytest.mark.parametrize("road,number,expected", [
    ("Grand Avenue", "1234", 1234),   # same street: kept
    ("5th Street", "1234", None),     # corner: Nominatim's road differs, dropped
    (None, None, None),
])
def test_house_number_gate(monkeypatch, road, number, expected):
    gps._cache.clear()
    monkeypatch.setattr(gps, "_nominatim",
                        lambda lat, lon: {"road": road, "house_number": number})
    assert gps.maybe_house_number(37.0, -122.0, "GRAND AVE") == expected
    gps._cache.clear()


# Schedule extraction from a GDF row ------------------------------------------

def _row(**kw):
    return pd.Series({"DAY_EVEN": None, "DAY_ODD": None, **kw})


@pytest.mark.parametrize("row,expected", [
    (_row(DAY_EVEN="ME", DESC_EVEN="Every Mon", TIME_EVEN="8AM-10AM"),
     ("ME", "Every Mon", "8AM-10AM")),
    (_row(DAY_EVEN="ME"), ("ME", "", "")),                     # missing desc/time columns
    (_row(DAY_EVEN="ME", DESC_EVEN="N/A", TIME_EVEN=float("nan")), ("ME", "", "")),
    (_row(DAY_EVEN="NS"), None),                                # explicit no-sweep
    (_row(DAY_EVEN=""), None),
    (_row(DAY_EVEN="   "), None),
    (_row(DAY_EVEN=float("nan")), None),
    (_row(DAY_EVEN=0), None),
])
def test_get_schedule(row, expected):
    assert analysis.get_schedule(row, 0) == expected


@pytest.mark.parametrize("args,expected", [
    ((100, 200), "even"), ((101, 201), "odd"), ((100, 201), None), ((0, 100), "even"),
    ((None, 200), None), (("100.0", "200.0"), "even"), (("nan", "200"), None),
])
def test_address_range_parity(args, expected):
    assert resolve._parity(*args) == expected
