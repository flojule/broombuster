"""HTTP plumbing (api/web.py): rate-limit keys and pruning, refusals before the app,
HSTS, the service worker's cache name, precompressed static files and versioned tiles."""

import gzip

import pytest
from fastapi.testclient import TestClient

from broombuster.api import web
from broombuster.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def _from(ip):
    """A client behind the tunnel with this visitor IP."""
    tunnel = TestClient(app, client=("127.0.0.1", 50000))
    tunnel.headers["cf-connecting-ip"] = ip
    return tunnel


def test_client_key_groups_an_ipv6_64():
    assert web.client_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert web.client_key("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert web.client_key("203.0.113.9") == "203.0.113.9"
    assert web.client_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert web.client_key("testclient") == "testclient"


def test_rate_limit_counts_a_whole_ipv6_64(monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", True)
    monkeypatch.setattr(web, "_limiter", web.RateLimiter())
    monkeypatch.setattr(web, "LIMITS", (("/api/me", 2),))
    codes = [_from(f"2001:db8::{i}").get("/api/me").status_code for i in range(1, 4)]
    assert codes == [200, 200, 429]
    assert _from("2001:db8:0:1::1").get("/api/me").status_code == 200  # another /64


def test_next_sweep_file_is_rate_limited():
    assert web._limit_for("/api/next-sweep.ics") == ("/api/next-sweep", 30)


def test_idle_clients_are_pruned_at_most_once_per_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(web.time, "monotonic", lambda: now[0])
    lim = web.RateLimiter(60)
    for i in range(10_001):
        lim.allow(f"c{i}", "b", 5)
    now[0] += 61
    lim.allow("x", "b", 5)  # prunes the idle ones
    assert len(lim._hits) == 1
    for i in range(10_001):
        lim.allow(f"d{i}", "b", 5)
    table = lim._hits
    lim.allow("y", "b", 5)  # within the window: no rebuild
    assert lim._hits is table and len(table) == 10_003


def test_refusals_before_the_app_read_as_fastapis(monkeypatch):
    """Size and rate refusals, made before the app runs, carry `detail` as its own do."""
    path, c = "/api/check", TestClient(app)
    big = c.post(path, content=b"x" * (web.MAX_BODY_BYTES + 1))
    assert big.status_code == 413 and big.json() == {"detail": "Request too large."}
    chunked = c.post(path, content=iter([b"{}"]))
    assert chunked.status_code == 411 and chunked.json()["detail"]
    monkeypatch.setattr(web, "RATE_LIMIT", True)
    monkeypatch.setattr(web, "_limiter", web.RateLimiter())
    monkeypatch.setattr(web, "LIMITS", ((path, 1),))
    c.post(path, json={})
    limited = c.post(path, json={})
    assert limited.status_code == 429 and limited.json() == {"detail": "Too many requests."}


def test_hsts_only_over_https(client, monkeypatch):
    monkeypatch.setenv("BB_BASE_URL", "https://broombuster.com")
    assert client.get("/api/me").headers["strict-transport-security"] == "max-age=31536000"
    monkeypatch.setenv("BB_BASE_URL", "http://localhost:8000")
    assert "strict-transport-security" not in client.get("/api/me").headers


def test_offline_cache_name_follows_the_page_and_manifest(tmp_path, monkeypatch):
    """An HTML-only or manifest-only deploy installs a new service worker; asset URLs stay."""
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("app")
    for f in ("styles.css", "index.html", *web._SHELL_FILES):
        (tmp_path / f).write_text(f)
    monkeypatch.setattr(web, "_FRONTEND", tmp_path)
    assets, shell = web._asset_version(), web._shell_version()
    for f in ("index.html", "manifest.json"):
        (tmp_path / f).write_text("changed " + f)
        assert web._asset_version() == assets and web._shell_version() != shell
        shell = web._shell_version()


def test_static_text_is_served_precompressed(client):
    path = "/layers/bay_area/parking.geojson"
    raw = (web._FRONTEND / path.lstrip("/")).read_bytes()
    r = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert r.content == raw and int(r.headers["content-length"]) < len(raw) / 3
    assert r.headers["etag"].startswith('W/"') and r.headers["vary"] == "Accept-Encoding"
    again = client.get(path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    plain = client.get(path, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.content == raw
    assert plain.headers["etag"] == r.headers["etag"][2:]
    assert plain.headers["content-type"] == r.headers["content-type"]
    part = client.get(path, headers={"Range": "bytes=0-99", "Accept-Encoding": "gzip"})
    assert part.status_code == 206 and part.content == raw[:100]
    assert "content-encoding" not in part.headers
    stored = web._static_encoded[str(web._FRONTEND / path.lstrip("/"))][1]
    assert gzip.decompress(stored["gzip"]) == raw
    web._compress_file(web._FRONTEND / path.lstrip("/"))  # as the startup thread does
    br = client.get(path, headers={"Accept-Encoding": "gzip, br"})
    assert br.headers["content-encoding"] == "br" and br.content == raw
    assert int(br.headers["content-length"]) < int(r.headers["content-length"])
    assert br.headers["etag"] == r.headers["etag"]


def test_binary_static_files_are_not_recompressed(client):
    r = client.get("/icon-192.png", headers={"Accept-Encoding": "gzip, br"})
    assert r.status_code == 200 and "content-encoding" not in r.headers


def test_dynamic_json_is_still_compressed(app_client):
    body = {"lat": 37.7605, "lon": -122.4212}
    r = app_client.post("/api/check", json=body, headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-store"
    r = app_client.get("/api/cities", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.headers["cache-control"] == "no-cache"


def test_tiles_are_immutable_only_under_their_version(app_client):
    regions = app_client.get("/api/cities").json()
    v = regions["bay_area"]["tiles_version"]
    assert v and v == web.tiles_version("bay_area") != regions["chicago"]["tiles_version"]
    rng = {"Range": "bytes=0-15"}
    hit = app_client.get(f"/tiles/bay_area.pmtiles?v={v}", headers=rng)
    assert hit.status_code == 206 and len(hit.content) == 16
    assert hit.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "content-encoding" not in hit.headers and hit.headers["etag"]
    for url in ("/tiles/bay_area.pmtiles", "/tiles/bay_area.pmtiles?v=stale"):
        r = app_client.get(url, headers=rng)
        assert r.status_code == 206 and r.headers["cache-control"] == (
            "public, max-age=3600, must-revalidate"
        )
    again = app_client.get(
        "/tiles/bay_area.pmtiles", headers={"If-None-Match": hit.headers["etag"]}
    )
    assert again.status_code == 304


def test_layer_archives_are_immutable_only_under_the_hash_in_their_name():
    path = "/tiles/bay_area/parking.64c9ddd6c396.pmtiles"
    assert web.cache_control(path, "64c9ddd6c396") == "public, max-age=31536000, immutable"
    for v in (None, "000000000000"):
        assert web.cache_control(path, v) == "public, max-age=3600, must-revalidate"


def test_map_layers_are_immutable_only_under_their_own_hash():
    path = "/layers/bay_area/parking.geojson"
    v = web._layer_version(path)
    assert v and web.cache_control(path, v) == "public, max-age=31536000, immutable"
    assert web.cache_control(path, web.asset_version()) == "no-cache"


def test_closures_are_cached_a_minute_and_revalidated(app_client):
    r = app_client.get("/api/closures", params={"region": "bay_area"})
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=60"
    etag = {"If-None-Match": r.headers["etag"]}
    again = app_client.get("/api/closures", params={"region": "bay_area"}, headers=etag)
    assert again.status_code == 304


def test_unhandled_error_is_a_logged_500_with_headers(client, monkeypatch):
    from broombuster.api import app as app_module

    def boom(lat, lon):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "region_for_point", boom)
    r = client.get("/api/locate", headers={"cf-iplatitude": "41.9", "cf-iplongitude": "-87.7"})
    assert r.status_code == 500 and r.json() == {"detail": "Internal error."}
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
