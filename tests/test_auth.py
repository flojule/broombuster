"""Passkey sign-in, shared accounts, sessions and account prefs; WebAuthn's verdict is faked."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from broombuster.api import auth
from broombuster.api.app import app

BASE = "http://testserver"
PREFS = {"cars": [{"id": "c1", "name": "Car", "lat": 37.8, "lon": -122.2, "color": "#10b981"}],
         "homes": [{"id": "h1", "lat": 37.8, "lon": -122.2, "address": "1 Main St"}]}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("BB_BASE_URL", BASE)
    monkeypatch.setenv("BB_DB", str(tmp_path / "users.sqlite"))
    auth.init_db()


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _sign_in(client, monkeypatch, name="Anna", cred="cred-a"):
    """Create an account (and so a session) with a passkey."""
    assert _register(client, monkeypatch, name, _cid(cred)).status_code == 204


def test_disabled_without_env(monkeypatch, client):
    monkeypatch.delenv("BB_BASE_URL", raising=False)
    assert client.get("/api/me").json() == {"enabled": False, "user": None, "prefs": None}
    assert client.post("/auth/passkey/login/options").status_code == 503
    assert auth.check_config() is False


def test_blank_template_is_guest_mode(monkeypatch):
    monkeypatch.setenv("BB_BASE_URL", "")
    assert auth.check_config() is False


def test_base_url_needs_a_scheme(monkeypatch):
    monkeypatch.setenv("BB_BASE_URL", "broombuster.com")
    with pytest.raises(RuntimeError, match="BB_BASE_URL"):
        auth.check_config()


def test_sign_in_save_prefs_sign_out(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    me = client.get("/api/me").json()
    assert me["user"] == {"name": "Anna"}
    assert me["family"] == ["Anna"] and me["prefs"] is None

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



def test_delete_account_removes_user_and_sessions(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE})
    token = client.cookies.get("bb_session")
    assert client.delete("/api/me", headers={"Origin": BASE}).status_code == 204
    client.cookies.set("bb_session", token)  # replay the old cookie
    assert client.get("/api/me").json()["user"] is None
    with auth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_expired_session_is_ignored(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    with auth._connect() as db:
        db.execute("UPDATE sessions SET expires_at = 0")
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


def test_phone_token_moves_car_and_bumps_version(env, client, monkeypatch, app_client):
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


def test_phone_token_rejections(env, client, monkeypatch, app_client):
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


def test_phone_numbers_as_shortcuts_sends_them(env, client, monkeypatch, app_client):
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


def test_phone_token_revoked_and_replaced(env, client, monkeypatch, app_client):
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


# ── Passkeys, invites, shared accounts ────────────────────────────────────────
# The WebAuthn library's checks are its own; these tests fake its verdict and test
# the glue: challenges used once, the right account, the right session.
def _cid(name):
    """A canonical base64url credential id, as a browser sends it."""
    return bytes_to_base64url(name.encode())


def _fake_registration(**kw):
    cred = kw["credential"]
    assert base64url_to_bytes(cred["response"]["challengeCheck"]) == kw["expected_challenge"]
    return SimpleNamespace(
        credential_id=base64url_to_bytes(cred["id"]),
        credential_public_key=b"pk-" + cred["id"].encode(),
        sign_count=0,
    )


def _fake_authentication(**kw):
    assert (
        base64url_to_bytes(kw["credential"]["response"]["challengeCheck"])
        == kw["expected_challenge"]
    )
    return SimpleNamespace(new_sign_count=kw["credential_current_sign_count"] + 1)


def _register(client, monkeypatch, name, cred_id, invite=None):
    """Passkey sign-up through the real endpoints; the WebAuthn answer is faked."""
    monkeypatch.setattr(auth, "verify_registration_response", _fake_registration)
    body = {"name": name, **({"invite": invite} if invite else {})}
    opts = client.post("/auth/passkey/register/options", json=body, headers=ORIGIN)
    assert opts.status_code == 200
    challenge = opts.json()["challenge"]
    cred = {"id": cred_id, "response": {"challengeCheck": challenge}}
    return client.post("/auth/passkey/register/verify", json=cred, headers=ORIGIN)


def _login(client, monkeypatch, cred_id):
    monkeypatch.setattr(auth, "verify_authentication_response", _fake_authentication)
    opts = client.post("/auth/passkey/login/options", headers=ORIGIN)
    assert opts.status_code == 200
    cred = {"id": cred_id, "response": {"challengeCheck": opts.json()["challenge"]}}
    return client.post("/auth/passkey/login/verify", json=cred, headers=ORIGIN)


def _invite_token(client):
    url = client.post("/api/me/invite", headers=ORIGIN).json()["url"]
    return url.split("?join=", 1)[1]



def test_passkey_sign_up_and_sign_in(env, client, monkeypatch):
    assert _register(client, monkeypatch, "Anna", _cid("cred-a")).status_code == 204
    me = client.get("/api/me").json()
    assert me["user"] == {"name": "Anna"} and me["family"] == ["Anna"]

    client.post("/auth/logout", headers=ORIGIN)
    assert client.get("/api/me").json()["user"] is None
    assert _login(client, monkeypatch, _cid("cred-a")).status_code == 204
    assert client.get("/api/me").json()["user"]["name"] == "Anna"
    with auth._connect() as db:  # the signature counter moves on each sign-in
        assert db.execute("SELECT sign_count FROM passkeys").fetchone()[0] == 1


def test_passkey_challenge_answers_once(env, client, monkeypatch):
    monkeypatch.setattr(auth, "verify_registration_response", _fake_registration)
    opts = client.post("/auth/passkey/register/options", json={"name": "Anna"}, headers=ORIGIN)
    cred = {"id": _cid("cred-a"), "response": {"challengeCheck": opts.json()["challenge"]}}
    verify = "/auth/passkey/register/verify"
    assert client.post(verify, json=cred, headers=ORIGIN).status_code == 204
    client.cookies.clear()
    assert client.post(verify, json=cred, headers=ORIGIN).status_code == 400


def test_passkey_needs_same_origin_and_a_name(env, client, monkeypatch):
    assert client.post("/auth/passkey/register/options", json={"name": "A"}).status_code == 403
    assert client.post("/auth/passkey/login/options").status_code == 403
    assert (
        client.post("/auth/passkey/register/options", json={"name": ""}, headers=ORIGIN).status_code
        == 422
    )


def test_unknown_passkey_is_refused(env, client, monkeypatch):
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    client.cookies.clear()
    assert _login(client, monkeypatch, _cid("cred-x")).status_code == 400
    assert client.get("/api/me").json()["user"] is None


def test_invite_adds_a_member_to_the_same_account(env, client, monkeypatch):
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    assert client.put("/api/me/prefs", json=PREFS, headers=ORIGIN).status_code == 204
    token = _invite_token(client)
    client.cookies.clear()

    assert _register(client, monkeypatch, "Ben", _cid("cred-b"), invite=token).status_code == 204
    me = client.get("/api/me").json()
    assert me["user"]["name"] == "Ben" and me["family"] == ["Anna", "Ben"]
    assert me["prefs"] == PREFS  # Ben sees the shared cars and homes
    with auth._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1

    client.cookies.clear()  # the link is single-use
    assert _register(client, monkeypatch, "Cy", _cid("cred-c"), invite=token).status_code == 400


def test_expired_invite_is_refused(env, client, monkeypatch):
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    token = _invite_token(client)
    with auth._connect() as db:
        db.execute("UPDATE invites SET expires_at = 0")
    client.cookies.clear()
    assert _register(client, monkeypatch, "Ben", _cid("cred-b"), invite=token).status_code == 400



def test_invites_are_capped_per_account(env, client, monkeypatch):
    monkeypatch.setattr(auth, "_MAX_MEMBERS", 1)
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    assert client.post("/api/me/invite", headers=ORIGIN).status_code == 409
    assert client.post("/api/me/invite").status_code == 403


def test_leaving_keeps_the_account_for_others(env, client, monkeypatch):
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    client.put("/api/me/prefs", json=PREFS, headers=ORIGIN)
    token = _invite_token(client)
    client.cookies.clear()
    _register(client, monkeypatch, "Ben", _cid("cred-b"), invite=token)

    assert client.delete("/api/me", headers=ORIGIN).status_code == 204  # Ben leaves
    client.cookies.clear()
    _login(client, monkeypatch, _cid("cred-a"))
    me = client.get("/api/me").json()
    assert me["family"] == ["Anna"] and me["prefs"] == PREFS
    client.cookies.clear()
    assert _login(client, monkeypatch, _cid("cred-b")).status_code == 400  # Ben's passkey is gone


def test_last_member_leaving_deletes_the_account(env, client, monkeypatch):
    _register(client, monkeypatch, "Anna", _cid("cred-a"))
    client.put("/api/me/prefs", json=PREFS, headers=ORIGIN)
    client.post("/api/cars/c1/token", headers=ORIGIN)
    assert client.delete("/api/me", headers=ORIGIN).status_code == 204
    with auth._connect() as db:
        for table in ("accounts", "members", "car_tokens", "sessions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0




def test_drops_the_google_era_schema(tmp_path, monkeypatch):
    """Rows keyed to a Google account can no longer be reached; they are cleared."""
    db_file = tmp_path / "old.sqlite"
    monkeypatch.setenv("BB_DB", str(db_file))
    with sqlite3.connect(db_file) as old:
        old.executescript("""
            CREATE TABLE users (sub TEXT PRIMARY KEY, email TEXT NOT NULL, prefs TEXT,
                                created_at INTEGER NOT NULL);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL,
                sub TEXT NOT NULL);
            CREATE TABLE car_tokens (token_hash TEXT PRIMARY KEY, car_id TEXT NOT NULL,
                created_at INTEGER NOT NULL, last_used INTEGER, sub TEXT NOT NULL,
                last_moved INTEGER, last_error TEXT);
            CREATE TABLE calendar_tokens (token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                last_used INTEGER, sub TEXT NOT NULL UNIQUE);
            INSERT INTO users VALUES ('sub-1', 'a@example.com', NULL, 1);
        """)
    auth.init_db()
    auth.init_db()  # idempotent
    with auth._connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone()
        assert db.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0
        assert [r[1] for r in db.execute("PRAGMA table_info(car_tokens)")].count("account_id") == 1
