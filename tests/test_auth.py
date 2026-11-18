"""Passkey sign-in, shared accounts, sessions and account prefs; WebAuthn's verdict is faked."""

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from broombuster.api import auth
from broombuster.api.app import app

BASE = "http://testserver"
PREFS = {
    "cars": [{"id": "c1", "name": "Car", "lat": 37.8, "lon": -122.2, "color": "#10b981"}],
    "homes": [{"id": "h1", "lat": 37.8, "lon": -122.2, "address": "1 Main St"}],
}


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

    assert client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 200
    assert client.get("/api/me").json()["prefs"] == PREFS

    assert client.post("/auth/logout", headers={"Origin": BASE}).status_code == 204
    assert client.get("/api/me").json()["user"] is None


def test_prefs_need_session_and_same_origin(env, client, monkeypatch):
    assert client.put("/api/me/prefs", json=PREFS, headers={"Origin": BASE}).status_code == 401
    _sign_in(client, monkeypatch)
    assert client.put("/api/me/prefs", json=PREFS).status_code == 403
    assert (
        client.put(
            "/api/me/prefs", json=PREFS, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.delete("/api/me").status_code == 403
    assert client.get("/api/me").json()["prefs"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"cars": [{"id": "c", "name": "n", "lat": 91, "lon": 0}], "homes": []},
        {"cars": [{"id": "c", "name": "n", "lat": 0, "lon": 0}] * 51, "homes": []},
        {"cars": []},
        {"cars": [{"id": "c\r\nATTACH:x", "name": "n", "lat": 0, "lon": 0}], "homes": []},
        {"cars": [], "homes": [{"id": "h\nx", "lat": 0, "lon": 0}]},
    ],
)
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


def test_session_in_use_is_renewed(env, client, monkeypatch):
    """/api/me pushes a session ending within a day of the full term back to the full term,
    and re-sets the cookie; a fresh session is left alone."""
    _sign_in(client, monkeypatch)
    with auth._connect() as db:
        full = db.execute("SELECT expires_at FROM sessions").fetchone()[0]
    r = client.get("/api/me")
    assert r.json()["user"] and "bb_session" not in r.headers.get("set-cookie", "")
    with auth._connect() as db:
        db.execute("UPDATE sessions SET expires_at = ?", (full - 2 * 86400,))
    r = client.get("/api/me")
    assert r.json()["user"] and "bb_session=" in r.headers["set-cookie"]
    with auth._connect() as db:
        assert db.execute("SELECT expires_at FROM sessions").fetchone()[0] >= full


# ── Phone position updates ────────────────────────────────────────────────────
OAKLAND = {"lat": 37.821326, "lon": -122.280705}  # Chestnut St: swept
ORIGIN = {"Origin": BASE}


def _signed_in_with_car(client, monkeypatch):
    _sign_in(client, monkeypatch)
    assert client.put("/api/me/prefs", json=PREFS, headers=ORIGIN).status_code == 200
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
    r = client.post(
        "/api/cars/location", json={"lat": "37.81", "lon": "-122.27"}, headers=_bearer(token)
    )
    assert r.status_code == 200
    assert client.get("/api/me").json()["prefs"]["cars"][0]["loc_rev"] == 2


def test_phone_token_rejections(env, client, monkeypatch, app_client):
    token = _signed_in_with_car(client, monkeypatch)
    no_cookie = TestClient(app)  # the phone sends no session cookie
    assert no_cookie.post("/api/cars/location", json=OAKLAND).status_code == 401
    assert (
        no_cookie.post("/api/cars/location", json=OAKLAND, headers=_bearer("bbc_nope")).status_code
        == 401
    )
    rough = no_cookie.post(
        "/api/cars/location", json={**OAKLAND, "accuracy": 500}, headers=_bearer(token)
    )
    assert rough.status_code == 422
    assert rough.json()["summary"].startswith("Position too rough (±500 m)")  # the phone shows it
    me = client.get("/api/me").json()
    assert me["prefs"]["cars"][0]["lat"] == PREFS["cars"][0]["lat"]
    assert me["phone_links"]["c1"]["error"].startswith("Position too rough")
    assert me["phone_links"]["c1"]["moved"] is None
    bad = no_cookie.post(
        "/api/cars/location", json={"lat": 37.8, "accuracy": "lots"}, headers=_bearer(token)
    )
    assert bad.status_code == 422
    assert bad.json()["summary"] == (
        "Could not read lon missing, accuracy ('lots'); the car was not moved."
    )
    not_json = no_cookie.post("/api/cars/location", content="lat=1", headers=_bearer(token))
    assert not_json.status_code == 422 and "JSON body" in not_json.json()["summary"]
    # An accepted report clears the refusal.
    assert (
        no_cookie.post("/api/cars/location", json=OAKLAND, headers=_bearer(token)).status_code
        == 200
    )
    link = client.get("/api/me").json()["phone_links"]["c1"]
    assert link["error"] is None and link["moved"]


def test_phone_numbers_as_shortcuts_sends_them(env, client, monkeypatch, app_client):
    token = _signed_in_with_car(client, monkeypatch)
    for body in (
        {"lat": "37,804", "lon": "-122,271"},  # a decimal comma before three digits
        {"lat": "37,8044", "lon": "-122,2712", "accuracy": "35,0 m"},
        {"lat": "37.8044°", "lon": " -122.2712 ", "accuracy": "±40 ft"},
        {"lat": 37.8044, "lon": -122.2712, "accuracy": "12\u00a0m"},
    ):
        r = client.post("/api/cars/location", json=body, headers=_bearer(token))
        assert r.status_code == 200, (body, r.json())
    car = client.get("/api/me").json()["prefs"]["cars"][0]
    assert (car["lat"], car["lon"]) == (37.8044, -122.2712)
    # A thousands separator, too rough, an unknown unit.
    for accuracy in ("1,234 ft", "0.1 km", "65 parsecs"):
        r = client.post(
            "/api/cars/location", json={**OAKLAND, "accuracy": accuracy}, headers=_bearer(token)
        )
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
    assert client.put("/api/me/prefs", json=stale, headers=ORIGIN).status_code == 200
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
    assert made["url"].startswith(BASE + "/api/calendar/bbk_") and made["webcal"].startswith(
        "webcal://"
    )
    assert client.get("/api/me").json()["calendar"] is True
    path = made["url"][len(BASE) :]
    feed = client.get(path)
    assert feed.status_code == 200 and feed.headers["content-type"].startswith("text/calendar")
    assert "Move Car: street sweeping" in feed.text
    # A new link replaces the old one; turning it off revokes both.
    again = client.post("/api/me/calendar", headers={"Origin": BASE}).json()
    assert client.get(path).status_code == 404
    assert client.delete("/api/me/calendar", headers={"Origin": BASE}).status_code == 204
    assert client.get(again["url"][len(BASE) :]).status_code == 404
    assert client.get("/api/calendar/nope.ics").status_code == 404


def test_database_is_wal_and_a_polled_feed_marks_its_use_hourly(env, monkeypatch, app_client):
    client = TestClient(app, follow_redirects=False)
    _sign_in(client, monkeypatch)
    made = client.post("/api/me/calendar", headers={"Origin": BASE}).json()
    path = made["url"][len(BASE) :]

    def used(set_to=None):
        with auth._connect() as db:
            if set_to is not None:
                db.execute("UPDATE calendar_tokens SET last_used = ?", (set_to,))
            return db.execute("SELECT last_used FROM calendar_tokens").fetchone()[0]

    with auth._connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert used() is None and client.get(path).status_code == 200
    now = used()
    assert now > time.time() - 60
    used(now - 1800)
    client.get(path)
    assert used() == now - 1800  # used within the hour: not written again
    used(now - 3700)
    client.get(path)
    assert used() >= now


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
    assert client.put("/api/me/prefs", json=PREFS, headers=ORIGIN).status_code == 200
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


def test_merge_keeps_the_side_picked_for_the_stored_position():
    """A side picked here (no position change) is saved; a side picked for another
    position never lands on the stored one."""
    stored = [{"id": "c1", "lat": 1.0, "lon": 2.0, "loc_rev": 5, "side": "even"}]
    pick = [{"id": "c1", "lat": 1.0, "lon": 2.0, "loc_rev": 5, "side": "odd"}]
    assert auth._merge_cars(pick, stored)[0]["side"] == "odd"
    old_client = [{"id": "c1", "lat": 1.0, "lon": 2.0, "loc_rev": 5}]
    assert auth._merge_cars(old_client, stored)[0]["side"] == "even"
    stale = [{"id": "c1", "lat": 9.0, "lon": 9.0, "loc_rev": 4, "side": "odd"}]
    got = auth._merge_cars(stale, stored)[0]
    assert (got["lat"], got["side"]) == (1.0, "even")


# ── Two devices, one account ──────────────────────────────────────────────────
CAR2 = {"id": "c2", "name": "Van", "lat": 37.81, "lon": -122.25, "color": "#3b82f6"}


def test_stale_save_is_refused_and_the_other_devices_car_stays(env, client, monkeypatch):
    """Phone B adds a car and links a phone; phone A, still on the old lists, renames a
    car: its save is refused (409) and the new car and its link stay."""
    _sign_in(client, monkeypatch)
    r = client.put("/api/me/prefs", json=PREFS, headers=ORIGIN)
    assert r.json() == {"rev": 1} and client.get("/api/me").json()["prefs_rev"] == 1
    both = {**PREFS, "cars": [*PREFS["cars"], CAR2], "rev": 1}
    assert client.put("/api/me/prefs", json=both, headers=ORIGIN).json() == {"rev": 2}
    assert client.post("/api/cars/c2/token", headers=ORIGIN).status_code == 200

    renamed = {**PREFS, "cars": [{**PREFS["cars"][0], "name": "Renamed"}], "rev": 1}
    assert client.put("/api/me/prefs", json=renamed, headers=ORIGIN).status_code == 409
    me = client.get("/api/me").json()
    assert [c["id"] for c in me["prefs"]["cars"]] == ["c1", "c2"] and "c2" in me["phone_links"]
    assert me["prefs_rev"] == 2
    # Rebased on rev 2, the rename goes through.
    rebased = {**both, "cars": [{**PREFS["cars"][0], "name": "Renamed"}, CAR2], "rev": 2}
    assert client.put("/api/me/prefs", json=rebased, headers=ORIGIN).json() == {"rev": 3}
    # A client from before versions sends none: saved as before.
    assert client.put("/api/me/prefs", json=PREFS, headers=ORIGIN).json() == {"rev": 4}


def test_prefs_version_is_added_to_an_existing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("BB_DB", str(tmp_path / "users.sqlite"))
    with sqlite3.connect(tmp_path / "users.sqlite") as old:
        old.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, prefs TEXT, created_at INTEGER)")
        old.execute("""INSERT INTO accounts VALUES ('a', '{"cars":[],"homes":[]}', 1)""")
        old.execute(
            "CREATE TABLE members (id TEXT PRIMARY KEY, account_id TEXT, name TEXT,"
            " created_at INTEGER)"
        )
        old.execute("INSERT INTO members VALUES ('m', 'a', 'Anna', 1)")
    auth.init_db()
    with auth._connect() as db:
        seen = db.execute("SELECT last_seen FROM members").fetchone()[0]
        db.execute("UPDATE members SET last_seen = 5")
    assert seen > time.time() - 60  # existing members count as seen now: none is pruned
    auth.init_db()  # idempotent
    with auth._connect() as db:
        row = db.execute("SELECT prefs, prefs_rev FROM accounts").fetchone()
        assert db.execute("SELECT last_seen FROM members").fetchone()[0] == 5
    assert tuple(row) == ('{"cars":[],"homes":[]}', 0)


_REBASE_JS = r"""
const vm = require('vm'), fs = require('fs');
const ctx = { window: {}, navigator: {}, document: {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + ';globalThis._rebase = _rebase;', ctx);
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
// Each result, and for each item the index of the local object it is (-1: a new one).
process.stdout.write(JSON.stringify(cases.map(([local, base, server]) => {
  const out = ctx._rebase(local, base, server);
  return [out, out.map(x => local.indexOf(x))];
})));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_client_reapplies_its_edits_on_the_account_lists():
    """account.js _rebase(local, base, server): a three-way merge per item and field.
    Fields this device changed since `base` keep its value, the rest take the server's;
    an item removed on either side stays removed; local items keep their identity."""
    c1 = {"id": "c1", "name": "Car", "lat": 1.0}
    renamed, theirs = {**c1, "name": "Ours"}, {**c1, "name": "Theirs"}
    c2, c3 = {"id": "c2", "name": "Van"}, {"id": "c3", "name": "Bike"}
    plated = {**c1, "plate": "ABC"}
    spot = {**c1, "loc_rev": 4, "side": "even"}
    cases = [
        ([renamed], [c1], [c1, c2]),  # renamed here, a car added elsewhere
        ([], [c1], [theirs, c2]),  # removed here
        ([c1], [c1], []),  # removed elsewhere
        ([renamed], [c1], []),  # removed elsewhere, edited here: stays removed
        ([c1, c3], [c1], [theirs]),  # added here, renamed elsewhere: same object
        ([{"lat": 1.0, "name": "Car", "id": "c1"}], [c1], [theirs]),  # key order is no edit
        # Plate changed elsewhere, name here: both kept.
        ([{**plated, "name": "Ours"}], [plated], [{**plated, "plate": "XYZ"}]),
        # Moved here (newer), renamed and moved by a phone elsewhere: our spot, their name.
        (
            [{"id": "c1", "name": "Car", "lat": 2.0, "loc_rev": 10}],
            [spot],
            [{**spot, "name": "Theirs", "lat": 3.0, "loc_rev": 5}],
        ),
        # Side picked here, then a phone moved the car: the newer spot, no side.
        ([{**spot, "side": "odd"}], [spot], [{**c1, "lat": 3.0, "loc_rev": 5}]),
    ]
    js = Path(__file__).resolve().parent.parent / "frontend" / "js" / "account.js"
    out = subprocess.run(
        ["node", "-e", _REBASE_JS, str(js)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == [
        [[renamed, c2], [0, -1]],
        [[c2], [-1]],
        [[], []],
        [[], []],
        [[theirs, c3], [0, 1]],
        [[theirs], [0]],
        [[{**plated, "name": "Ours", "plate": "XYZ"}], [0]],
        [[{"id": "c1", "name": "Theirs", "lat": 2.0, "loc_rev": 10}], [0]],
        [[{"id": "c1", "name": "Car", "lat": 3.0, "loc_rev": 5}], [0]],
    ]


# ── Abuse limits ──────────────────────────────────────────────────────────────
def _from(ip):
    """A client behind the tunnel with this visitor IP."""
    tunnel = TestClient(app, client=("127.0.0.1", 50000), follow_redirects=False)
    tunnel.headers.update({"cf-connecting-ip": ip, **ORIGIN})
    return tunnel


def test_pending_sign_ins_are_capped_per_client(env, monkeypatch):
    """One client's (an IPv6 /64's) 7 sign-ins in progress keep only its newest 5; other
    clients still get in, up to the global cap."""
    monkeypatch.setattr(auth, "_challenges", {})
    monkeypatch.setattr(auth, "_MAX_PENDING_PASSKEYS", 7)
    opts = "/auth/passkey/login/options"
    a = _from("2001:db8::1")
    keys = []
    for _ in range(7):
        assert a.post(opts).status_code == 200
        keys.append(a.cookies.get("bb_passkey"))
    assert list(auth._challenges) == keys[2:]
    assert _from("2001:db8:0:1::1").post(opts).status_code == 200  # another /64
    assert _from("2001:db8::ffff:2").post(opts).status_code == 200  # a's /64: a's oldest goes
    assert len(auth._challenges) == 6 and keys[2] not in auth._challenges
    assert _from("198.51.100.7").post(opts).status_code == 200
    assert _from("198.51.100.8").post(opts).status_code == 429  # the global cap


def test_sign_ups_are_capped_per_client_per_day(env, monkeypatch):
    from broombuster.api import web

    monkeypatch.setattr(web, "RATE_LIMIT", True)
    monkeypatch.setattr(web, "_limiter", web.RateLimiter())
    monkeypatch.setattr(auth, "_signups", web.RateLimiter(86400))
    monkeypatch.setattr(auth, "_SIGNUPS_PER_DAY", 2)
    a = _from("2001:db8::1")
    opts = "/auth/passkey/register/options"
    assert [a.post(opts, json={"name": "A"}).status_code for _ in range(3)] == [200, 200, 429]
    assert a.post(opts, json={"name": "A", "invite": "bbi_x"}).status_code == 200  # joins
    assert _from("2001:db8:0:1::1").post(opts, json={"name": "A"}).status_code == 200


def test_idle_empty_accounts_are_deleted_at_sign_up(env, monkeypatch):
    """At a sign-up, an account with no cars, no homes, one member and no member seen for
    30 days goes. Signing out is no idleness; a car, or prefs that are not JSON, keep it."""
    for name in ("Anna", "Bob", "Ben", "Eve"):
        c = TestClient(app, follow_redirects=False)
        _sign_in(c, monkeypatch, name, f"cred-{name}")
        if name == "Ben":
            assert c.put("/api/me/prefs", json=PREFS, headers=ORIGIN).status_code == 200
        assert c.post("/auth/logout", headers=ORIGIN).status_code == 204
    with auth._connect() as db:
        db.execute("UPDATE accounts SET created_at = 0")
        db.execute("UPDATE members SET last_seen = 0 WHERE name != 'Anna'")  # Anna: just now
        db.execute(
            "UPDATE accounts SET prefs = 'not json'"
            " WHERE id = (SELECT account_id FROM members WHERE name = 'Eve')"
        )
    _sign_in(TestClient(app, follow_redirects=False), monkeypatch, "Dan", "cred-d")
    with auth._connect() as db:
        names = [r[0] for r in db.execute("SELECT name FROM members ORDER BY name")]
        assert names == ["Anna", "Ben", "Dan", "Eve"]
        assert db.execute("SELECT COUNT(*) FROM passkeys").fetchone()[0] == 4


def test_last_seen_moves_on_sign_in_and_daily_session_use(env, client, monkeypatch):
    _sign_in(client, monkeypatch)
    with auth._connect() as db:
        assert db.execute("SELECT last_seen FROM members").fetchone()[0] > time.time() - 60
        db.execute("UPDATE members SET last_seen = 5")
    client.get("/api/me")  # a fresh session: not renewed, nor seen again (daily at most)
    with auth._connect() as db:
        assert db.execute("SELECT last_seen FROM members").fetchone()[0] == 5
        db.execute("UPDATE sessions SET expires_at = expires_at - 2 * 86400")
    client.get("/api/me")  # a day later: renewed, and seen
    with auth._connect() as db:
        assert db.execute("SELECT last_seen FROM members").fetchone()[0] > time.time() - 60


def test_sessions_are_capped_and_sign_out_everywhere(env, client, monkeypatch):
    monkeypatch.setattr(auth, "_MAX_SESSIONS", 2)
    _sign_in(client, monkeypatch)
    devices = []
    for _ in range(2):
        with auth._connect() as db:  # the sessions so far were last used 10 s earlier
            db.execute("UPDATE sessions SET expires_at = expires_at - 10")
        d = TestClient(app, follow_redirects=False)
        assert _login(d, monkeypatch, _cid("cred-a")).status_code == 204
        devices.append(d)
    assert client.get("/api/me").json()["user"] is None  # the least recently used went
    assert all(d.get("/api/me").json()["user"] for d in devices)

    other = TestClient(app, follow_redirects=False)
    _sign_in(other, monkeypatch, "Ben", "cred-b")
    assert devices[0].post("/auth/logout-everywhere").status_code == 403  # same-origin only
    assert devices[0].post("/auth/logout-everywhere", headers=ORIGIN).status_code == 204
    assert not any(d.get("/api/me").json()["user"] for d in devices)
    assert other.get("/api/me").json()["user"]["name"] == "Ben"
    assert _login(client, monkeypatch, _cid("cred-a")).status_code == 204  # the passkey stays
