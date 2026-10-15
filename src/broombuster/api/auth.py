"""Google sign-in (OpenID Connect code flow), server-side sessions, per-user prefs.

Enabled when GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and BB_BASE_URL are all set
(BB_BASE_URL e.g. https://broombuster.com). Users, sessions and prefs live in the
SQLite file BB_DB (default data/users.sqlite).
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
import time
from collections import deque
from contextlib import closing
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from broombuster import config

log = logging.getLogger(__name__)
router = APIRouter()

_SESSION_COOKIE = "bb_session"
_OAUTH_COOKIE = "bb_oauth"
_SESSION_TTL_S = 30 * 86400
_OAUTH_TTL_S = 600
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_CALLBACK_LIMIT = 30  # token exchanges per minute, all clients together


# ── Config ────────────────────────────────────────────────────────────────────
def _config() -> tuple[str, str, str] | None:
    """(client_id, client_secret, base_url), or None when sign-in is off."""
    cid, secret, base = config.oauth_env()
    if not (cid and secret and base):
        return None
    return cid, secret, base.rstrip("/")


def check_config() -> bool:
    """True when sign-in is on; raises on a partial or malformed setup."""
    values = config.oauth_env()
    if not any(values):
        return False
    if not all(values):
        raise RuntimeError(f"Set all of {', '.join(config.OAUTH_ENV_KEYS)} or none.")
    if not values[2].startswith(("https://", "http://")):
        raise RuntimeError("BB_BASE_URL must start with https:// (or http:// for local dev).")
    return True


def _require_config() -> tuple[str, str, str]:
    cfg = _config()
    if cfg is None:
        raise HTTPException(503, "Sign-in is not configured.")
    return cfg


# ── Storage ───────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.db_path(), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db() -> None:
    with closing(_connect()) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                sub TEXT PRIMARY KEY, email TEXT NOT NULL,
                prefs TEXT, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL,
                sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS car_tokens (
                token_hash TEXT PRIMARY KEY, car_id TEXT NOT NULL,
                created_at INTEGER NOT NULL, last_used INTEGER,
                sub TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
                UNIQUE (sub, car_id));
        """)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── Sessions ──────────────────────────────────────────────────────────────────
def _secure(base: str) -> bool:
    return base.startswith("https://")


def _set_cookie(resp: Response, name: str, value: str, max_age: int, base: str, path: str) -> None:
    resp.set_cookie(name, value, max_age=max_age, path=path, httponly=True,
                    secure=_secure(base), samesite="lax")


def _session_user(request: Request) -> sqlite3.Row | None:
    token = request.cookies.get(_SESSION_COOKIE)
    if not token or _config() is None:
        return None
    with closing(_connect()) as db:
        return db.execute(
            "SELECT u.* FROM sessions s JOIN users u USING (sub)"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (_hash(token), int(time.time()))).fetchone()


def _same_origin(request: Request) -> None:
    """CSRF guard for state-changing calls: Origin must be this site."""
    if request.headers.get("origin") != _require_config()[2]:
        raise HTTPException(403, "Cross-origin request refused.")


def _signed_in_mutation(request: Request) -> sqlite3.Row:
    _same_origin(request)
    user = _session_user(request)
    if user is None:
        raise HTTPException(401, "Sign in required.")
    return user


# ── Google OpenID Connect ─────────────────────────────────────────────────────
_callback_times: deque[float] = deque()
_callback_lock = threading.Lock()


def _callback_allowed() -> bool:
    now = time.monotonic()
    with _callback_lock:
        while _callback_times and now - _callback_times[0] > 60:
            _callback_times.popleft()
        if len(_callback_times) >= _CALLBACK_LIMIT:
            return False
        _callback_times.append(now)
        return True


def _id_token_claims(id_token: str, client_id: str, nonce: str) -> dict:
    """Validated claims of an ID token received straight from Google's token endpoint.

    The token arrives over TLS from the issuer, so OIDC Core 3.1.3.7 allows
    skipping the signature check; issuer, audience, expiry and nonce are checked.
    """
    payload = id_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if (claims.get("iss") not in _GOOGLE_ISSUERS or claims.get("aud") != client_id
            or claims.get("exp", 0) <= time.time()
            or not hmac.compare_digest(str(claims.get("nonce", "")), nonce)
            or claims.get("email_verified") is not True
            or not claims.get("sub") or not claims.get("email")):
        raise ValueError("ID token claims rejected")
    return claims


@router.get("/auth/login")
def login():
    client_id, _, base = _require_config()
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    url = _GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": f"{base}/auth/callback",
        "response_type": "code", "scope": "openid email",
        "state": state, "nonce": nonce, "prompt": "select_account"})
    resp = RedirectResponse(url, status_code=302)
    _set_cookie(resp, _OAUTH_COOKIE, f"{state}.{nonce}", _OAUTH_TTL_S, base, "/auth")
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None,
             error: str | None = None):
    client_id, secret, base = _require_config()
    failed = RedirectResponse("/?auth_error=1", status_code=302)
    failed.delete_cookie(_OAUTH_COOKIE, path="/auth")
    saved_state, _, nonce = request.cookies.get(_OAUTH_COOKIE, "").partition(".")
    if error or not code or not state or not saved_state \
            or not hmac.compare_digest(saved_state, state):
        log.warning("sign-in refused: error=%s state_ok=%s", error, bool(saved_state))
        return failed
    if not _callback_allowed():
        log.warning("sign-in refused: rate limit")
        return failed
    try:
        r = requests.post(_GOOGLE_TOKEN_URL, timeout=10, data={
            "code": code, "client_id": client_id, "client_secret": secret,
            "redirect_uri": f"{base}/auth/callback", "grant_type": "authorization_code"})
        r.raise_for_status()
        claims = _id_token_claims(r.json()["id_token"], client_id, nonce)
    except (requests.RequestException, KeyError, IndexError, ValueError,
            TypeError, AttributeError) as e:
        log.warning("sign-in failed: %s", e)
        return failed

    token, now = secrets.token_urlsafe(32), int(time.time())
    with closing(_connect()) as db, db:
        db.execute("INSERT INTO users (sub, email, created_at) VALUES (?, ?, ?)"
                   " ON CONFLICT(sub) DO UPDATE SET email = excluded.email",
                   (claims["sub"], claims["email"], now))
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        db.execute("INSERT INTO sessions (token_hash, expires_at, sub) VALUES (?, ?, ?)",
                   (_hash(token), now + _SESSION_TTL_S, claims["sub"]))
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(_OAUTH_COOKIE, path="/auth")
    _set_cookie(resp, _SESSION_COOKIE, token, _SESSION_TTL_S, base, "/")
    return resp


@router.post("/auth/logout")
def logout(request: Request):
    _same_origin(request)
    token = request.cookies.get(_SESSION_COOKIE)
    if token:
        with closing(_connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


# ── Account data ──────────────────────────────────────────────────────────────
class Car(BaseModel):
    id: str = Field(max_length=64)
    name: str = Field(max_length=200)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    # Position version: bumped on every move, from the browser or a phone, so a
    # stale device can't overwrite a newer position (see _merge_cars).
    loc_rev: int = Field(0, ge=0, le=2**31)


class Home(BaseModel):
    id: str = Field(max_length=64)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    address: str = Field("", max_length=500)


class Prefs(BaseModel):
    cars: list[Car] = Field(max_length=50)
    homes: list[Home] = Field(max_length=50)


def _merge_cars(incoming: list[dict], stored: list[dict]) -> list[dict]:
    """Incoming cars, each keeping the stored position unless the incoming one is newer."""
    have = {c["id"]: c for c in stored}
    out = []
    for car in incoming:
        old = have.get(car["id"], {})
        if old.get("loc_rev") and old["loc_rev"] >= car.get("loc_rev", 0):
            car = {**car, "lat": old["lat"], "lon": old["lon"], "loc_rev": old["loc_rev"]}
        out.append(car)
    return out


def _stored_prefs(db: sqlite3.Connection, sub: str) -> dict:
    row = db.execute("SELECT prefs FROM users WHERE sub = ?", (sub,)).fetchone()
    return json.loads(row["prefs"]) if row and row["prefs"] else {"cars": [], "homes": []}


@router.get("/api/me")
def me(request: Request, response: Response):
    """Sign-in state and saved prefs; `enabled` is false when sign-in is off.

    `phone_links` maps each car that has a phone token to when it was last used.
    """
    response.headers["Cache-Control"] = "no-store"
    if _config() is None:
        return {"enabled": False, "user": None, "prefs": None}
    user = _session_user(request)
    if user is None:
        return {"enabled": True, "user": None, "prefs": None}
    with closing(_connect()) as db:
        links = {r["car_id"]: r["last_used"] for r in db.execute(
            "SELECT car_id, last_used FROM car_tokens WHERE sub = ?", (user["sub"],))}
    return {"enabled": True, "user": {"email": user["email"]},
            "prefs": json.loads(user["prefs"]) if user["prefs"] else None,
            "phone_links": links}


@router.put("/api/me/prefs", status_code=204)
def put_prefs(prefs: Prefs, user: sqlite3.Row = Depends(_signed_in_mutation)):
    new = prefs.model_dump(mode="json", exclude_unset=True)
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")  # a phone update can't slip in between read and write
        new["cars"] = _merge_cars(new["cars"], _stored_prefs(db, user["sub"])["cars"])
        db.execute("UPDATE users SET prefs = ? WHERE sub = ?",
                   (json.dumps(new, separators=(",", ":")), user["sub"]))
        keep = [c["id"] for c in new["cars"]]
        db.execute(f"DELETE FROM car_tokens WHERE sub = ? AND car_id NOT IN"
                   f" ({','.join('?' * len(keep))})", (user["sub"], *keep))


@router.delete("/api/me")
def delete_me(user: sqlite3.Row = Depends(_signed_in_mutation)):
    """Delete the account: user row, prefs, phone tokens and every session."""
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM users WHERE sub = ?", (user["sub"],))
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


# ── Phone position updates ────────────────────────────────────────────────────
# A phone (iOS Shortcut, Tasker) reports where the car was parked with a bearer
# token that is tied to one car of one account and can do nothing else.
_TOKEN_PREFIX = "bbc_"


@router.post("/api/cars/{car_id}/token")
def create_car_token(car_id: str, user: sqlite3.Row = Depends(_signed_in_mutation)):
    """New phone token for a saved car (shown once); replaces any earlier one."""
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not any(c["id"] == car_id for c in _stored_prefs(db, user["sub"])["cars"]):
            raise HTTPException(404, "Car not saved to your account yet.")
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        db.execute("DELETE FROM car_tokens WHERE sub = ? AND car_id = ?", (user["sub"], car_id))
        db.execute("INSERT INTO car_tokens (token_hash, car_id, created_at, sub)"
                   " VALUES (?, ?, ?, ?)", (_hash(token), car_id, int(time.time()), user["sub"]))
    return {"token": token}


@router.delete("/api/cars/{car_id}/token", status_code=204)
def revoke_car_token(car_id: str, user: sqlite3.Row = Depends(_signed_in_mutation)):
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM car_tokens WHERE sub = ? AND car_id = ?", (user["sub"], car_id))


def car_for_token(request: Request) -> sqlite3.Row:
    """The (sub, car_id) a bearer token belongs to; marks the token used."""
    _require_config()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing bearer token.")
    token_hash = _hash(token.strip())
    with closing(_connect()) as db, db:
        db.execute("UPDATE car_tokens SET last_used = ? WHERE token_hash = ?",
                   (int(time.time()), token_hash))
        row = db.execute("SELECT sub, car_id FROM car_tokens WHERE token_hash = ?",
                         (token_hash,)).fetchone()
    if row is None:
        raise HTTPException(401, "Unknown or revoked token.")
    return row


def set_car_location(sub: str, car_id: str, lat: float, lon: float) -> bool:
    """Move a saved car and bump its position version; False if the car is gone."""
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prefs = _stored_prefs(db, sub)
        car = next((c for c in prefs["cars"] if c["id"] == car_id), None)
        if car is None:
            return False
        car.update(lat=lat, lon=lon, loc_rev=car.get("loc_rev", 0) + 1)
        db.execute("UPDATE users SET prefs = ? WHERE sub = ?",
                   (json.dumps(prefs, separators=(",", ":")), sub))
    return True
