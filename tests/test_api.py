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
    resp = client.post("/api/check", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health_and_cities(client):
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["version"]
    regions = client.get("/api/cities").json()
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
    assert client.post("/api/check", json={"lat": 91, "lon": 0}).status_code == 422


@pytest.mark.parametrize(
    "road,number,expected",
    [
        ("Grand Avenue", "1234", 1234),  # same street: kept
        ("5th Street", "1234", None),  # corner: Nominatim's road differs, dropped
        (None, None, None),
    ],
)
def test_house_number_gate(monkeypatch, road, number, expected):
    gps._cache.clear()
    monkeypatch.setattr(gps, "_nominatim", lambda lat, lon: {"road": road, "house_number": number})
    assert gps.maybe_house_number(37.0, -122.0, "GRAND AVE") == expected
    gps._cache.clear()


# Schedule extraction from a GDF row ------------------------------------------


def _row(**kw):
    return pd.Series({"DAY_EVEN": None, "DAY_ODD": None, **kw})


@pytest.mark.parametrize(
    "row,expected",
    [
        (
            _row(DAY_EVEN="ME", DESC_EVEN="Every Mon", TIME_EVEN="8AM-10AM"),
            ("ME", "Every Mon", "8AM-10AM"),
        ),
        (_row(DAY_EVEN="ME"), ("ME", "", "")),  # missing desc/time columns
        (_row(DAY_EVEN="ME", DESC_EVEN="N/A", TIME_EVEN=float("nan")), ("ME", "", "")),
        (_row(DAY_EVEN="NS"), None),  # explicit no-sweep
        (_row(DAY_EVEN=""), None),
        (_row(DAY_EVEN="   "), None),
        (_row(DAY_EVEN=float("nan")), None),
        (_row(DAY_EVEN=0), None),
    ],
)
def test_get_schedule(row, expected):
    assert analysis.get_schedule(row, 0) == expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ((100, 200), "even"),
        ((101, 201), "odd"),
        ((100, 201), None),
        ((0, 100), "even"),
        ((None, 200), None),
        (("100.0", "200.0"), "even"),
        (("nan", "200"), None),
    ],
)
def test_address_range_parity(args, expected):
    assert resolve._parity(*args) == expected


def test_locate_uses_cloudflare_headers(client):
    r = client.get("/api/locate", headers={"cf-iplatitude": "41.9", "cf-iplongitude": "-87.7"})
    assert r.json()["region"] == "chicago"


def test_shell_revalidates_and_api_is_not_cached(client):
    assert client.get("/styles.css").headers["cache-control"] == "no-cache"
    assert client.get("/api/cities").headers["cache-control"] == "no-store"


def test_index_assets_are_versioned_and_cacheable(client):
    r = client.get("/")
    assert r.headers["cache-control"] == "no-cache"
    assert 'src="/js/app.js?v=' in r.text and 'href="/styles.css?v=' in r.text
    assert "/vendor/pmtiles.js?v=" in r.text and "/js/maplibre-global.js?v=" in r.text
    v = r.text.split("/js/app.js?v=")[1].split('"')[0]
    js = client.get(f"/js/app.js?v={v}")
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_rate_limit_blocks_bursts(client, monkeypatch):
    from broombuster.api import web

    monkeypatch.setattr(web, "RATE_LIMIT", True)
    monkeypatch.setattr(web, "_limiter", web.RateLimiter())
    hdr = {"cf-connecting-ip": "9.9.9.9", "cf-iplatitude": "37.8", "cf-iplongitude": "-122.3"}
    tunnel = type(client)(client.app, client=("127.0.0.1", 50000))  # the tunnel's loopback peer
    codes = [tunnel.get("/api/locate", headers=hdr).status_code for _ in range(22)]
    assert codes[-1] == 429 and 429 not in codes[:20]
    assert client.get("/health", headers={"cf-connecting-ip": "9.9.9.9"}).status_code == 200


def test_client_ip_ignores_garbage_header():
    from types import SimpleNamespace

    from broombuster.api import web

    req = lambda h: SimpleNamespace(headers=h, client=SimpleNamespace(host="127.0.0.1"))  # noqa: E731
    assert web.client_ip(req({"cf-connecting-ip": "9.9.9.9"})) == "9.9.9.9"
    assert web.client_ip(req({"cf-connecting-ip": "evil/../x"})) == "127.0.0.1"


def test_client_ip_trusts_header_only_from_loopback():
    from types import SimpleNamespace

    from broombuster.api import web

    req = SimpleNamespace(
        headers={"cf-connecting-ip": "9.9.9.9"}, client=SimpleNamespace(host="8.8.8.8")
    )
    assert web.client_ip(req) == "8.8.8.8"


def test_body_limit(client):
    big = {"cars": [], "homes": [], "pad": "x" * 70_000}
    assert client.put("/api/me/prefs", json=big).status_code == 413
    chunked = client.post("/api/check", content=iter([b"{}"]))
    assert chunked.status_code == 411


def test_service_worker_is_templated(client):
    r = client.get("/sw.js")
    assert "__VERSION__" not in r.text and "__API_PATHS__" not in r.text
    assert '["/api", "/auth", "/health"]' in r.text and r.headers["cache-control"] == "no-cache"


def test_csp_forbids_inline_and_remote_scripts(client):
    h = client.get("/").headers
    assert "content-security-policy-report-only" not in h  # enforced by default
    csp = h["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    html = client.get("/").text
    assert "<script>" not in html and "onclick=" not in html
    assert 'type="module">' not in html  # no inline module script


def test_service_worker_precaches_every_script(client):
    sw = client.get("/sw.js").text
    for f in ("/js/app.js", "/js/maplibre-global.js", "/vendor/maplibre-gl.mjs"):
        assert f'"{f}"' in sw


def test_geocode_searches_inside_the_region(client, monkeypatch):
    seen = []

    def _search(q, bbox):
        seen.append((q, bbox))
        return {"lat": 37.8, "lon": -122.27} if "market" in q.lower() else {}

    monkeypatch.setattr(gps, "_nominatim_search", _search)
    r = client.get("/api/geocode", params={"q": "  1 Market   St ", "region": "bay_area"})
    assert r.json() == {"lat": 37.8, "lon": -122.27}
    assert seen[0][0] == "1 Market St" and seen[0][1] is not None  # bounded to the region's cities
    assert client.get("/api/geocode", params={"q": "nowhere"}).json() == {"lat": None, "lon": None}
    assert seen[-1][1] is None  # no region: unbounded
    client.get("/api/geocode", params={"q": "1 market st", "region": "bay_area"})
    assert len(seen) == 2  # cached


def test_geocode_outage_is_503(client, monkeypatch):
    def _down(q, bbox):
        raise OSError("down")

    monkeypatch.setattr(gps, "_nominatim_search", _down)
    assert client.get("/api/geocode", params={"q": "1 Main St"}).status_code == 503


# Street-sweeping signs read on Google Street View (imagery date): the car parked under
# the sign must get the sign's day and hours. Re-read a sign before changing a row.
_SIGNS = [
    # Ford St, Oakland (2025-05): "9:00 AM to 12:00 noon, 2nd and 4th Mondays"
    (37.77242, -122.23263, "M24", "9AM–12PM", None),
    # Taft Ave, Oakland (2022-05): "12:30 PM to 3:30 PM, 2nd Mondays"
    (37.84270, -122.24902, "M2", "12:30PM–3:30PM", None),
    # West St, Berkeley, east side (2022-08): "9 AM to 12 noon, 2nd Monday each month"
    (37.86440, -122.28508, "M2", "9AM–12PM", "odd"),
    # Berkeley Way, south side (2024-07): "12:30 PM to 3:30 PM, 2nd Tuesday"
    (37.87215, -122.27504, "T2", "12:30PM–3:30PM", "even"),
]


@pytest.mark.parametrize(("lat", "lon", "code", "time", "side"), _SIGNS)
def test_schedule_matches_signs_on_the_street(client, lat, lon, code, time, side):
    data = _check(client, {"lat": lat, "lon": lon, "region": "bay_area"})
    sides = [side] if side else ["even", "odd"]
    if side:
        assert data["car_side"] == side
    found = [(e[0], e[2]) for s in sides for e in data[f"schedule_{s}"]]
    assert (code, time) in found


def test_undated_oakland_codes_send_drivers_to_the_sign(client):
    """36th Ave 800 block, Oakland: coded S / SU ("Sat" / "Sun"); signs (Street View 2025)
    read 2nd & 4th Mon and 2nd & 4th Tue, 9 AM-12 noon. No day is claimed, hours are."""
    data = _check(client, {"lat": 37.7724, "lon": -122.2243, "region": "bay_area"})
    for side in ("even", "odd"):
        assert [(e[0], e[2]) for e in data[f"schedule_{side}"]] == [("POSTED", "9AM–12PM")]


@pytest.mark.parametrize(
    ("lat", "lon", "side", "code"),
    [(37.807788, -122.281132, "odd", "TTHS"), (37.807737, -122.281018, "even", "MWF")],
)
def test_two_line_street_side_follows_its_kerb_line(client, lat, lon, side, code):
    """Market St 1300 block, Oakland: one line per kerb, the other side coded MS."""
    data = _check(client, {"lat": lat, "lon": lon, "region": "bay_area"})
    assert data["car_side"] == side
    assert [e[0] for e in data[f"schedule_{side}"]] == [code]


def test_no_sweeping_where_nobody_can_park(client):
    """Broadway at 14th, Oakland: bus lanes and red curb (Street View 2025-02)."""
    data = _check(client, {"lat": 37.8048, "lon": -122.2709, "region": "bay_area"})
    assert data["address"] == "Broadway, Oakland"
    assert not data["schedule_even"] and not data["schedule_odd"]
