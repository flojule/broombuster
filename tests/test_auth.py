"""Google sign-in, sessions and account prefs; Google's token endpoint is faked."""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from broombuster import config
from broombuster.api import auth
from broombuster.api.app import app

BASE = "http://testserver"
CLIENT_ID = "client-123"
PREFS = {"cars": [{"id": "c1", "name": "Car", "lat": 37.8, "lon": -122.2, "color": "#10b981"}],
         "homes": [{"id": "h1", "lat": 37.8, "lon": -122.2, "address": "1 Main St"}]}


def _id_token(**over):
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "sub-1",
              "email": "a@example.com", "email_verified": True,
              "exp": time.time() + 3600, **over}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class _Resp:
    def __init__(self, id_token):
        self._id_token = id_token

    def raise_for_status(self):
        pass

    def json(self):
        return {"id_token": self._id_token}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("BB_BASE_URL", BASE)
    monkeypatch.setenv("BB_DB", str(tmp_path / "users.sqlite"))
    monkeypatch.setattr(auth, "_callback_times", auth.deque())
    auth.init_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _sign_in(client, monkeypatch, **claim_over):
    """Run login + callback with a faked Google answer; returns the callback response."""
    r = client.get("/auth/login")
    assert r.status_code == 302
    query = parse_qs(urlparse(r.headers["location"]).query)
    nonce = client.cookies.get("bb_oauth").split(".")[1]
    monkeypatch.setattr(auth.requests, "post",
                        lambda *a, **k: _Resp(_id_token(**{"nonce": nonce, **claim_over})))
    return client.get("/auth/callback", params={"code": "c", "state": query["state"][0]})


def test_disabled_without_env(monkeypatch, client):
    for key in config.OAUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert client.get("/api/me").json() == {"enabled": False, "user": None, "prefs": None}
    assert client.get("/auth/login").status_code == 503
    assert auth.check_config() is False


def test_blank_template_is_guest_mode(monkeypatch):
    for key in config.OAUTH_ENV_KEYS:
        monkeypatch.setenv(key, "")
    assert auth.check_config() is False


def test_partial_config_fails_fast(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("BB_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Set all of"):
        auth.check_config()


def test_login_redirect_targets_google(env, client):
    r = client.get("/auth/login")
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert q["client_id"] == [CLIENT_ID] and q["redirect_uri"] == [f"{BASE}/auth/callback"]
    assert q["scope"] == ["openid email"]
    assert "httponly" in r.headers["set-cookie"].lower()


def test_sign_in_save_prefs_sign_out(env, client, monkeypatch):
    r = _sign_in(client, monkeypatch)
    assert r.headers["location"] == "/"
    me = client.get("/api/me").json()
    assert me["user"] == {"email": "a@example.com"} and me["prefs"] is None

    assert client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 204
    assert client.get("/api/me").json()["prefs"] == PREFS

    assert client.post("/auth/logout", headers={"Origin": BASE}).status_code == 204
    assert client.get("/api/me").json()["user"] is None


def test_prefs_need_session_and_same_origin(env, client, monkeypatch):
    assert client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 401
    _sign_in(client, monkeypatch)
    assert client.put("/api/me/prefs", json=PREFS).status_code == 403
    assert client.put("/api/me/prefs", json=PREFS,
                      headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.delete("/api/me").status_code == 403
    assert client.get("/api/me").json()["prefs"] is None


@pytest.mark.parametrize("bad", [
    {"cars": [{"id": "c", "name": "n", "lat": 91, "lon": 0}], "homes": []},
    {"cars": [{"id": "c", "name": "n", "lat": 0, "lon": 0}] * 51, "homes": []},
    {"cars": []},
])
def test_prefs_validation(env, client, monkeypatch, bad):
    _sign_in(client, monkeypatch)
    assert client.put("/api/me/prefs", json=bad, headers={"Origin": BASE}).status_code == 422


@pytest.mark.parametrize("claims", [
    {"aud": "other-client"},
    {"iss": "https://evil.example"},
    {"email_verified": False},
    {"exp": time.time() - 1},
    {"nonce": "wrong"},
    {"exp": "soon"},
])
def test_callback_rejects_bad_id_token(env, client, monkeypatch, claims):
    r = _sign_in(client, monkeypatch, **claims)
    assert r.headers["location"] == "/?auth_error=1"
    assert client.get("/api/me").json()["user"] is None


def test_callback_rejects_state_mismatch_and_missing_cookie(env, client):
    client.get("/auth/login")
    r = client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert r.headers["location"] == "/?auth_error=1"
    client.cookies.clear()
    r = client.get("/auth/callback", params={"code": "c", "state": ""})
    assert r.headers["location"] == "/?auth_error=1"


def test_delete_account_removes_user_and_sessions(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE})
    token = client.cookies.get("bb_session")
    assert client.delete("/api/me", headers={"Origin": BASE}).status_code == 204
    client.cookies.set("bb_session", token)  # replay the old cookie
    assert client.get("/api/me").json()["user"] is None
    with auth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_expired_session_is_ignored(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    with auth._connect() as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/api/me").json()["user"] is None


def test_callback_rate_limit(env, client, monkeypatch):
    monkeypatch.setattr(auth, "_CALLBACK_LIMIT", 1)
    assert _sign_in(client, monkeypatch).headers["location"] == "/"
    client.cookies.clear()
    assert _sign_in(client, monkeypatch).headers["location"] == "/?auth_error=1"
    assert client.get("/api/me").json()["user"] is None


# ── Phone position updates ────────────────────────────────────────────────────
OAKLAND = {"lat": 37.8044, "lon": -122.2712}
ORIGIN = {"Origin": BASE}


def _signed_in_with_car(client, monkeypatch):
    _sign_in(client, monkeypatch)
    assert client.put("/api/me/prefs", json=PREFS, headers=ORIGIN).status_code == 204
    r = client.post("/api/cars/c1/token", headers=ORIGIN)
    assert r.status_code == 200
    return r.json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_phone_token_moves_car_and_bumps_version(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    r = client.post("/api/cars/location", json={**OAKLAND, "accuracy": 8}, headers=_bearer(token))
    assert r.status_code == 200 and r.json()["summary"].startswith("Parked at ")
    car = client.get("/api/me").json()["prefs"]["cars"][0]
    assert (car["lat"], car["lon"], car["loc_rev"]) == (OAKLAND["lat"], OAKLAND["lon"], 1)
    link = client.get("/api/me").json()["phone_links"]["c1"]
    assert link["used"] and link["moved"] and link["error"] is None
    # Shortcuts may send the coordinates as text.
    r = client.post("/api/cars/location", json={"lat": "37.81", "lon": "-122.27"},
                    headers=_bearer(token))
    assert r.status_code == 200
    assert client.get("/api/me").json()["prefs"]["cars"][0]["loc_rev"] == 2


def test_phone_token_rejections(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    no_cookie = TestClient(app)  # the phone sends no session cookie
    assert no_cookie.post("/api/cars/location", json=OAKLAND).status_code == 401
    assert no_cookie.post("/api/cars/location", json=OAKLAND,
                          headers=_bearer("bbc_nope")).status_code == 401
    rough = no_cookie.post("/api/cars/location", json={**OAKLAND, "accuracy": 500},
                           headers=_bearer(token))
    assert rough.status_code == 422
    assert rough.json()["summary"].startswith("Position too rough (±500 m)")  # the phone shows it
    me = client.get("/api/me").json()
    assert me["prefs"]["cars"][0]["lat"] == PREFS["cars"][0]["lat"]
    assert me["phone_links"]["c1"]["error"].startswith("Position too rough")
    assert me["phone_links"]["c1"]["moved"] is None
    bad = no_cookie.post("/api/cars/location", json={"lat": 37.8, "accuracy": "lots"},
                         headers=_bearer(token))
    assert bad.status_code == 422
    assert bad.json()["summary"] == ("Could not read lon missing, accuracy ('lots');"
                                     " the car was not moved.")
    not_json = no_cookie.post("/api/cars/location", content="lat=1", headers=_bearer(token))
    assert not_json.status_code == 422 and "JSON body" in not_json.json()["summary"]
    # An accepted report clears the refusal.
    assert no_cookie.post("/api/cars/location", json=OAKLAND,
                          headers=_bearer(token)).status_code == 200
    link = client.get("/api/me").json()["phone_links"]["c1"]
    assert link["error"] is None and link["moved"]


def test_phone_numbers_as_shortcuts_sends_them(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    for body in ({"lat": "37,8044", "lon": "-122,2712", "accuracy": "35,0 m"},
                 {"lat": "37.8044°", "lon": " -122.2712 ", "accuracy": "±40 ft"},
                 {"lat": 37.8044, "lon": -122.2712, "accuracy": "12\u00a0m"}):
        r = client.post("/api/cars/location", json=body, headers=_bearer(token))
        assert r.status_code == 200, (body, r.json())
    car = client.get("/api/me").json()["prefs"]["cars"][0]
    assert (car["lat"], car["lon"]) == (37.8044, -122.2712)
    # A thousands separator, too rough, an unknown unit.
    for accuracy in ("1,234 ft", "0.1 km", "65 parsecs"):
        r = client.post("/api/cars/location", json={**OAKLAND, "accuracy": accuracy},
                        headers=_bearer(token))
        assert r.status_code == 422, accuracy


def test_phone_token_revoked_and_replaced(env, client, monkeypatch):
    old = _signed_in_with_car(client, monkeypatch)
    new = client.post("/api/cars/c1/token", headers=ORIGIN).json()["token"]
    assert client.post("/api/cars/location", json=OAKLAND, headers=_bearer(old)).status_code == 401
    assert client.post("/api/cars/location", json=OAKLAND, headers=_bearer(new)).status_code == 200
    assert client.delete("/api/cars/c1/token", headers=ORIGIN).status_code == 204
    assert client.post("/api/cars/location", json=OAKLAND, headers=_bearer(new)).status_code == 401
    assert client.get("/api/me").json()["phone_links"] == {}


def test_token_needs_saved_car_session_and_origin(env, client, monkeypatch):
    assert client.post("/api/cars/c1/token", headers=ORIGIN).status_code == 401
    _sign_in(client, monkeypatch)
    assert client.post("/api/cars/c1/token", headers=ORIGIN).status_code == 404  # not saved yet
    client.put("/api/me/prefs", json=PREFS, headers=ORIGIN)
    assert client.post("/api/cars/c1/token").status_code == 403


def test_stale_device_cannot_overwrite_phone_position(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    client.post("/api/cars/location", json=OAKLAND, headers=_bearer(token))
    # A browser that never saw the phone update syncs its old copy, plus a rename.
    stale = {**PREFS, "cars": [{**PREFS["cars"][0], "name": "Renamed"}]}
    assert client.put("/api/me/prefs", json=stale, headers=ORIGIN).status_code == 204
    car = client.get("/api/me").json()["prefs"]["cars"][0]
    assert car["name"] == "Renamed" and car["lat"] == OAKLAND["lat"] and car["loc_rev"] == 1
    # A newer manual move on the browser does win.
    moved = {**PREFS, "cars": [{**PREFS["cars"][0], "lat": 37.7, "lon": -122.4, "loc_rev": 2}]}
    client.put("/api/me/prefs", json=moved, headers=ORIGIN)
    assert client.get("/api/me").json()["prefs"]["cars"][0]["lat"] == 37.7


def test_removing_a_car_drops_its_token(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    client.put("/api/me/prefs", json={"cars": [], "homes": []}, headers=ORIGIN)
    r = client.post("/api/cars/location", json=OAKLAND, headers=_bearer(token))
    assert r.status_code == 401


def test_delete_account_removes_tokens(env, client, monkeypatch):
    token = _signed_in_with_car(client, monkeypatch)
    assert client.delete("/api/me", headers=ORIGIN).status_code == 204
    r = client.post("/api/cars/location", json=OAKLAND, headers=_bearer(token))
    assert r.status_code == 401


def test_calendar_link_and_feed(env, monkeypatch, app_client):
    client = TestClient(app, follow_redirects=False)  # app_client loaded the city data
    _sign_in(client, monkeypatch)
    prefs = {"cars": [{**PREFS["cars"][0], **OAKLAND}], "homes": []}
    client.put("/api/me/prefs", json=prefs, headers={"Origin": BASE})
    assert client.post("/api/me/calendar").status_code == 403  # same-origin only
    made = client.post("/api/me/calendar", headers={"Origin": BASE}).json()
    assert made["url"].startswith(BASE + "/api/calendar/bbk_") and made["webcal"].startswith("webcal://")
    assert client.get("/api/me").json()["calendar"] is True
    path = made["url"][len(BASE):]
    feed = client.get(path)
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")
    assert "Move Car: street sweeping" in feed.text
    # A new link replaces the old one; turning it off revokes both.
    again = client.post("/api/me/calendar", headers={"Origin": BASE}).json()
    assert client.get(path).status_code == 404
    assert client.delete("/api/me/calendar", headers={"Origin": BASE}).status_code == 204
    assert client.get(again["url"][len(BASE):]).status_code == 404
    assert client.get("/api/calendar/nope.ics").status_code == 404
