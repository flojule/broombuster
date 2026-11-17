"""HTTP plumbing (api/web.py): rate-limit keys and pruning, refusals on the phone path,
HSTS, and the service worker's cache name."""

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


def test_phone_refusals_carry_a_summary(monkeypatch):
    """The phone shows `summary`: refusals before the app (size, rate) carry it too."""
    path, auth = web.PHONE_PATH, {"Authorization": "Bearer bbc_x"}
    c = TestClient(app)
    big = c.post(path, content=b"x" * (web.MAX_BODY_BYTES + 1), headers=auth)
    assert big.status_code == 413 and big.json()["summary"] == "Request too large."
    chunked = c.post(path, content=iter([b"{}"]), headers=auth)
    assert chunked.status_code == 411 and chunked.json()["summary"]
    monkeypatch.setattr(web, "RATE_LIMIT", True)
    monkeypatch.setattr(web, "_limiter", web.RateLimiter())
    monkeypatch.setattr(web, "LIMITS", (("/api/cars", 1),))
    c.post(path, json={}, headers=auth)
    limited = c.post(path, json={}, headers=auth)
    assert limited.status_code == 429 and limited.json()["summary"] == "Too many requests."
    assert "summary" not in c.post("/api/cars/c1/token").json()  # not the phone path


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
