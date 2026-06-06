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
