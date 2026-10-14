"""Google sign-in, sessions and account prefs; Google's token endpoint is faked."""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from broombuster.api import auth
from broombuster.api.app import app

BASE = "http://testserver"
CLIENT_ID = "client-123"
PREFS = {"cars": [{"id": "c1", "name": "Car", "lat": 37.8, "lon": -122.2}],
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
    for key in auth._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert client.get("/me").json() == {"enabled": False, "user": None, "prefs": None}
    assert client.get("/auth/login").status_code == 503
    assert auth.check_config() is False


def test_blank_template_is_guest_mode(monkeypatch):
    for key in auth._ENV_KEYS:
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
    me = client.get("/me").json()
    assert me["user"] == {"email": "a@example.com"} and me["prefs"] is None

    assert client.put("/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 204
    assert client.get("/me").json()["prefs"] == PREFS

    assert client.post("/auth/logout", headers={"Origin": BASE}).status_code == 204
    assert client.get("/me").json()["user"] is None


def test_prefs_need_session_and_same_origin(env, client, monkeypatch):
    assert client.put("/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 401
    _sign_in(client, monkeypatch)
    assert client.put("/me/prefs", json=PREFS).status_code == 403
    assert client.put("/me/prefs", json=PREFS,
                      headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.delete("/me").status_code == 403
    assert client.get("/me").json()["prefs"] is None


@pytest.mark.parametrize("bad", [
    {"cars": [{"id": "c", "name": "n", "lat": 91, "lon": 0}], "homes": []},
    {"cars": [{"id": "c", "name": "n", "lat": 0, "lon": 0}] * 51, "homes": []},
    {"cars": []},
])
def test_prefs_validation(env, client, monkeypatch, bad):
    _sign_in(client, monkeypatch)
    assert client.put("/me/prefs", json=bad, headers={"Origin": BASE}).status_code == 422


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
    assert client.get("/me").json()["user"] is None


def test_callback_rejects_state_mismatch_and_missing_cookie(env, client):
    client.get("/auth/login")
    r = client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert r.headers["location"] == "/?auth_error=1"
    client.cookies.clear()
    r = client.get("/auth/callback", params={"code": "c", "state": ""})
    assert r.headers["location"] == "/?auth_error=1"


def test_delete_account_removes_user_and_sessions(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    client.put("/me/prefs", json=PREFS, headers={"Origin": BASE})
    token = client.cookies.get("bb_session")
    assert client.delete("/me", headers={"Origin": BASE}).status_code == 204
    client.cookies.set("bb_session", token)  # replay the old cookie
    assert client.get("/me").json()["user"] is None
    with auth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_expired_session_is_ignored(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    with auth._connect() as db:
        db.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/me").json()["user"] is None


def test_callback_rate_limit(env, client, monkeypatch):
    monkeypatch.setattr(auth, "_CALLBACK_LIMIT", 1)
    assert _sign_in(client, monkeypatch).headers["location"] == "/"
    client.cookies.clear()
    assert _sign_in(client, monkeypatch).headers["location"] == "/?auth_error=1"
    assert client.get("/me").json()["user"] is None
