"""Auth roundtrip and unknown-user handling.

Regression coverage for two bugs fixed when rate limiting was wired up:
  - the constant-time login decoy used a malformed bcrypt string, so an
    unknown email raised inside passlib and returned 500 instead of 401;
  - passlib 1.7.x is incompatible with bcrypt >= 4.1, so register/login
    raised at runtime. Auth now uses the bcrypt library directly.
"""

import os

os.environ.setdefault("DEV_MODE", "1")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from broombuster.api import auth, db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "auth_test.sqlite")
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", True)
    db.init_db()
    app = FastAPI()
    app.include_router(auth.router)
    auth.init_rate_limiting(app)
    return TestClient(app)


def test_register_then_login_roundtrip(client):
    r = client.post("/auth/register", json={"email": "a@b.co", "password": "supersecret"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    r2 = client.post("/auth/login", json={"email": "a@b.co", "password": "supersecret"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["user_id"] == r.json()["user_id"]


def test_login_unknown_email_returns_401(client):
    r = client.post("/auth/login", json={"email": "nobody@nowhere.co", "password": "whatever12"})
    assert r.status_code == 401


def test_login_wrong_password_returns_401(client):
    client.post("/auth/register", json={"email": "c@d.co", "password": "rightpass1"})
    r = client.post("/auth/login", json={"email": "c@d.co", "password": "wrongpass1"})
    assert r.status_code == 401


def test_password_hash_verify_roundtrip():
    h = auth._hash_pw("correct horse battery staple")
    assert auth._verify_pw("correct horse battery staple", h)
    assert not auth._verify_pw("wrong password", h)


def test_register_disabled_returns_403(client, monkeypatch):
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)
    r = client.post("/auth/register", json={"email": "e@f.co", "password": "supersecret"})
    assert r.status_code == 403


def test_password_change_ends_refresh_sessions(client):
    r = client.post("/auth/register", json={"email": "g@h.co", "password": "oldpass123"})
    rt = r.json()["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": rt}).status_code == 200

    with db.get_db() as conn:
        conn.execute("UPDATE users SET pw_hash = ? WHERE email = ?",
                     (auth._hash_pw("newpass123"), "g@h.co"))
        conn.commit()
    r2 = client.post("/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 401
    assert "Session ended" in r2.json()["detail"]


def test_invalid_refresh_token_does_not_leak_details(client):
    r = client.post("/auth/refresh", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid refresh token"
