"""Passkey sign-in, shared accounts, server-side sessions, per-account prefs.

Sign-in is a passkey (WebAuthn): no password, no email. It needs BB_BASE_URL (e.g.
https://broombuster.com); without it sign-in is off.

An account (its cars, homes, phone and calendar links) has one or more members, each
a person with their own passkeys. A person joins an account through a single-use
invite link. Accounts, members, sessions and prefs live in the SQLite file BB_DB
(default data/users.sqlite).
"""

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidJSONStructure,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from broombuster import config

log = logging.getLogger(__name__)
router = APIRouter()

_SESSION_COOKIE = "bb_session"
_PASSKEY_COOKIE = "bb_passkey"
_SESSION_TTL_S = 30 * 86400
_PASSKEY_TTL_S = 300
_INVITE_TTL_S = 7 * 86400
_MAX_MEMBERS = 10  # people per account
_MAX_PENDING_PASSKEYS = 1000  # sign-ins in progress, all clients together
_INVITE_PREFIX = "bbi_"
_TOKEN_PREFIX = "bbc_"  # phone: one car of one account
_CAL_PREFIX = "bbk_"  # calendar feed: one account


# ── Config ────────────────────────────────────────────────────────────────────
def _base() -> str | None:
    """The public site URL, or None when sign-in is off."""
    return config.base_url() or None


def _require_base() -> str:
    base = _base()
    if base is None:
        raise HTTPException(503, "Sign-in is not configured.")
    return base


def check_config() -> bool:
    """True when sign-in is on (BB_BASE_URL set); raises on a malformed URL."""
    base = config.base_url()
    if not base:
        return False
    if not base.startswith(("https://", "http://")):
        raise RuntimeError("BB_BASE_URL must start with https:// (or http:// for local dev).")
    return True


# ── Storage ───────────────────────────────────────────────────────────────────
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY, prefs TEXT, created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS members (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        name TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS passkeys (
        credential_id TEXT PRIMARY KEY,
        member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
        public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL,
        member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS invites (
        token_hash TEXT PRIMARY KEY, expires_at INTEGER NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS car_tokens (
        token_hash TEXT PRIMARY KEY, car_id TEXT NOT NULL,
        created_at INTEGER NOT NULL, last_used INTEGER,
        account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        last_moved INTEGER, last_error TEXT,
        UNIQUE (account_id, car_id))""",
    """CREATE TABLE IF NOT EXISTS calendar_tokens (
        token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL, last_used INTEGER,
        account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE)""",
)


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.db_path(), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _drop_google_schema(db: sqlite3.Connection) -> None:
    """The first schema keyed everything to a Google account, which can no longer sign in."""
    for table in ("sessions", "car_tokens", "calendar_tokens", "users"):
        db.execute(f"DROP TABLE {table}")
    log.info("dropped the Google-era users, sessions and links")


def init_db() -> None:
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if (db.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone()
                and not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'accounts'").fetchone()):
            _drop_google_schema(db)
        for stmt in _SCHEMA:
            db.execute(stmt)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _stored_prefs(db: sqlite3.Connection, account_id: str) -> dict:
    row = db.execute("SELECT prefs FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return json.loads(row["prefs"]) if row and row["prefs"] else {"cars": [], "homes": []}


# ── Sessions ──────────────────────────────────────────────────────────────────
def _secure(base: str) -> bool:
    return base.startswith("https://")


def _set_cookie(resp: Response, name: str, value: str, max_age: int, base: str, path: str) -> None:
    resp.set_cookie(name, value, max_age=max_age, path=path, httponly=True,
                    secure=_secure(base), samesite="lax")


def _start_session(resp: Response, member_id: str, base: str) -> None:
    token, now = secrets.token_urlsafe(32), int(time.time())
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        db.execute("INSERT INTO sessions (token_hash, expires_at, member_id) VALUES (?, ?, ?)",
                   (_hash(token), now + _SESSION_TTL_S, member_id))
    _set_cookie(resp, _SESSION_COOKIE, token, _SESSION_TTL_S, base, "/")


def _session_user(request: Request) -> sqlite3.Row | None:
    """The signed-in member (its account_id included), or None."""
    token = request.cookies.get(_SESSION_COOKIE)
    if not token or _base() is None:
        return None
    with closing(_connect()) as db:
        return db.execute(
            "SELECT m.* FROM sessions s JOIN members m ON m.id = s.member_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (_hash(token), int(time.time()))).fetchone()


def _same_origin(request: Request) -> None:
    """CSRF guard for state-changing calls: Origin must be this site."""
    if request.headers.get("origin") != _require_base():
        raise HTTPException(403, "Cross-origin request refused.")


def _signed_in_mutation(request: Request) -> sqlite3.Row:
    _same_origin(request)
    user = _session_user(request)
    if user is None:
        raise HTTPException(401, "Sign in required.")
    return user


# ── Accounts and invites ──────────────────────────────────────────────────────
class _BadInvite(Exception):
    pass


def _member_count(db: sqlite3.Connection, account_id: str) -> int:
    return db.execute("SELECT COUNT(*) FROM members WHERE account_id = ?",
                      (account_id,)).fetchone()[0]


def _add_member(db: sqlite3.Connection, member_id: str, account_id: str | None,
                name: str) -> None:
    """A new member, in `account_id` if given, else in a new account."""
    now = int(time.time())
    if account_id is None:
        account_id = secrets.token_urlsafe(16)
        db.execute("INSERT INTO accounts (id, prefs, created_at) VALUES (?, NULL, ?)",
                   (account_id, now))
    db.execute("INSERT INTO members (id, account_id, name, created_at) VALUES (?, ?, ?, ?)",
               (member_id, account_id, name, now))


def _account_for_invite(db: sqlite3.Connection, token: str) -> str:
    """The account a valid, unused invite opens; the invite is used up here."""
    row = db.execute("SELECT account_id FROM invites WHERE token_hash = ? AND expires_at > ?",
                     (_hash(token), int(time.time()))).fetchone()
    if row is None or _member_count(db, row["account_id"]) >= _MAX_MEMBERS:
        raise _BadInvite()
    db.execute("DELETE FROM invites WHERE token_hash = ?", (_hash(token),))
    return row["account_id"]


def _valid_invite(token: str | None) -> str | None:
    """The invite token if it has the right shape, else None (a stale cookie is ignored)."""
    return token if token and token.startswith(_INVITE_PREFIX) and len(token) < 100 else None


# ── Sign-out ──────────────────────────────────────────────────────────────────
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


# ── Passkeys (WebAuthn) ───────────────────────────────────────────────────────
# Two ceremonies: create an account (or join one with an invite), and sign in. Each
# options call parks its challenge here under a random key sent in a cookie; the verify
# call takes it out, so a challenge answers once and only within _PASSKEY_TTL_S.
_challenges: dict[str, dict] = {}
_challenges_lock = threading.Lock()


def _options_response(options, base: str, state: dict) -> Response:
    key, now = secrets.token_urlsafe(16), time.time()
    with _challenges_lock:
        for k in [k for k, v in _challenges.items() if v["expires"] < now]:
            del _challenges[k]
        if len(_challenges) >= _MAX_PENDING_PASSKEYS:
            raise HTTPException(429, "Too many sign-ins in progress. Try again in a minute.")
        _challenges[key] = {**state, "expires": now + _PASSKEY_TTL_S}
    resp = Response(options_to_json(options), media_type="application/json")
    resp.headers["Cache-Control"] = "no-store"
    _set_cookie(resp, _PASSKEY_COOKIE, key, _PASSKEY_TTL_S, base, "/auth/passkey")
    return resp


def _take_challenge(request: Request) -> dict:
    key = request.cookies.get(_PASSKEY_COOKIE, "")
    with _challenges_lock:
        state = _challenges.pop(key, None)
    if state is None or state["expires"] < time.time():
        raise HTTPException(400, "That sign-in took too long. Please try again.")
    return state


def _rp_id(base: str) -> str:
    return urlparse(base).hostname or ""


_RESIDENT_KEY = AuthenticatorSelectionCriteria(
    resident_key=ResidentKeyRequirement.REQUIRED, require_resident_key=True,
    user_verification=UserVerificationRequirement.REQUIRED)


class NewPasskey(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    invite: str | None = Field(None, max_length=100)


@router.post("/auth/passkey/register/options")
def passkey_register_options(request: Request, body: NewPasskey):
    """Options for a new account, or with an invite a new member of a shared one."""
    _same_origin(request)
    base = _require_base()
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Give your name.")
    member_id = secrets.token_urlsafe(16)
    options = generate_registration_options(
        rp_id=_rp_id(base), rp_name="BroomBuster", user_name=name,
        user_id=member_id.encode(), user_display_name=name,
        authenticator_selection=_RESIDENT_KEY)
    return _options_response(options, base, {
        "purpose": "new", "challenge": options.challenge, "member_id": member_id,
        "name": name, "invite": _valid_invite(body.invite)})


@router.post("/auth/passkey/register/verify", status_code=204)
def passkey_register_verify(request: Request, credential: dict = Body(...)):
    _same_origin(request)
    base = _require_base()
    state = _take_challenge(request)
    if state["purpose"] != "new":
        raise HTTPException(400, "That sign-in took too long. Please try again.")
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=state["challenge"],
            expected_rp_id=_rp_id(base), expected_origin=base, require_user_verification=True)
    except (InvalidRegistrationResponse, InvalidJSONStructure, KeyError, TypeError, ValueError):
        raise HTTPException(400, "The passkey could not be verified.") from None

    now = int(time.time())
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        try:
            account_id = _account_for_invite(db, state["invite"]) if state["invite"] else None
        except _BadInvite:
            raise HTTPException(400, "This invite link has expired or is used up.") from None
        _add_member(db, state["member_id"], account_id, state["name"])
        try:
            db.execute("INSERT INTO passkeys (credential_id, member_id, public_key, sign_count,"
                       " created_at) VALUES (?, ?, ?, ?, ?)",
                       (bytes_to_base64url(verified.credential_id), state["member_id"],
                        verified.credential_public_key, verified.sign_count, now))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "This passkey is already saved.") from None

    resp = Response(status_code=204)
    resp.headers["Cache-Control"] = "no-store"
    _start_session(resp, state["member_id"], base)
    resp.delete_cookie(_PASSKEY_COOKIE, path="/auth/passkey")
    return resp


@router.post("/auth/passkey/login/options")
def passkey_login_options(request: Request):
    _same_origin(request)
    base = _require_base()
    # No allow-list: the passkey itself names the account it belongs to.
    options = generate_authentication_options(
        rp_id=_rp_id(base), user_verification=UserVerificationRequirement.REQUIRED)
    return _options_response(options, base, {"purpose": "login", "challenge": options.challenge})


@router.post("/auth/passkey/login/verify", status_code=204)
def passkey_login_verify(request: Request, credential: dict = Body(...)):
    _same_origin(request)
    base = _require_base()
    state = _take_challenge(request)
    if state["purpose"] != "login":
        raise HTTPException(400, "That sign-in took too long. Please try again.")
    cred_id = credential.get("id")
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM passkeys WHERE credential_id = ?",
                         (cred_id if isinstance(cred_id, str) else "",)).fetchone()
        if row is None:
            raise HTTPException(400, "That passkey is not saved here.")
        try:
            verified = verify_authentication_response(
                credential=credential, expected_challenge=state["challenge"],
                expected_rp_id=_rp_id(base), expected_origin=base,
                credential_public_key=row["public_key"],
                credential_current_sign_count=row["sign_count"], require_user_verification=True)
        except (InvalidAuthenticationResponse, InvalidJSONStructure, KeyError, TypeError,
                ValueError):
            raise HTTPException(400, "The passkey could not be verified.") from None
        db.execute("UPDATE passkeys SET sign_count = ? WHERE credential_id = ?",
                   (verified.new_sign_count, row["credential_id"]))

    resp = Response(status_code=204)
    resp.headers["Cache-Control"] = "no-store"
    _start_session(resp, row["member_id"], base)
    resp.delete_cookie(_PASSKEY_COOKIE, path="/auth/passkey")
    return resp


# ── Invites ───────────────────────────────────────────────────────────────────
@router.post("/api/me/invite")
def create_invite(user: sqlite3.Row = Depends(_signed_in_mutation)):
    """A single-use link that adds a new person to this account (shown once)."""
    base = _require_base()
    now = int(time.time())
    token = _INVITE_PREFIX + secrets.token_urlsafe(32)
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if _member_count(db, user["account_id"]) >= _MAX_MEMBERS:
            raise HTTPException(409, f"An account has at most {_MAX_MEMBERS} people.")
        db.execute("DELETE FROM invites WHERE expires_at < ?", (now,))
        db.execute("INSERT INTO invites (token_hash, expires_at, account_id) VALUES (?, ?, ?)",
                   (_hash(token), now + _INVITE_TTL_S, user["account_id"]))
    return {"url": f"{base}/?join={token}", "days": _INVITE_TTL_S // 86400}


# ── Account data ──────────────────────────────────────────────────────────────
class Car(BaseModel):
    id: str = Field(max_length=64)
    name: str = Field(max_length=200)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    # Position version: bumped on every move, from the browser or a phone, so a
    # stale device can't overwrite a newer position (see _merge_cars).
    loc_rev: int = Field(0, ge=0, le=2**31)
    # Chosen when the car is added, so removing another car never repaints it.
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    # Licence plate, for Chicago's towed / relocated vehicle lookups (optional).
    plate: str | None = Field(None, max_length=10, pattern=r"^[A-Za-z0-9 -]*$")
    plate_state: str | None = Field(None, pattern=r"^[A-Z]{2}$")


class Home(BaseModel):
    id: str = Field(max_length=64)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    address: str = Field("", max_length=500)
    # Weekly collection day (Mon=0) where the city has no trash lookup.
    collection_day: int | None = Field(None, ge=0, le=6)


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


@router.get("/api/me")
def me(request: Request, response: Response):
    """Sign-in state and the account's saved prefs; `enabled` is false when sign-in is off.

    `user` is the signed-in person and `family` the names of everyone in their account.
    `phone_links` maps each car that has a phone token to its last report: `used`
    (any report), `moved` (last accepted one), `error` (why the last one was refused;
    null when it was accepted), times in epoch seconds.
    """
    response.headers["Cache-Control"] = "no-store"
    if _base() is None:
        return {"enabled": False, "user": None, "prefs": None}
    user = _session_user(request)
    if user is None:
        return {"enabled": True, "user": None, "prefs": None}
    with closing(_connect()) as db:
        account_id = user["account_id"]
        account = db.execute("SELECT prefs FROM accounts WHERE id = ?", (account_id,)).fetchone()
        links = {r["car_id"]: {"used": r["last_used"], "moved": r["last_moved"],
                               "error": r["last_error"]} for r in db.execute(
            "SELECT car_id, last_used, last_moved, last_error FROM car_tokens"
            " WHERE account_id = ?", (account_id,))}
        cal = db.execute("SELECT created_at FROM calendar_tokens WHERE account_id = ?",
                         (account_id,)).fetchone()
        family = [r["name"] for r in db.execute(
            "SELECT name FROM members WHERE account_id = ? ORDER BY created_at", (account_id,))]
    return {"enabled": True, "user": {"name": user["name"]},
            "family": family,
            "prefs": json.loads(account["prefs"]) if account and account["prefs"] else None,
            "phone_links": links, "calendar": cal is not None}


@router.put("/api/me/prefs", status_code=204)
def put_prefs(prefs: Prefs, user: sqlite3.Row = Depends(_signed_in_mutation)):
    new = prefs.model_dump(mode="json", exclude_unset=True)
    account_id = user["account_id"]
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")  # a phone update can't slip in between read and write
        new["cars"] = _merge_cars(new["cars"], _stored_prefs(db, account_id)["cars"])
        db.execute("UPDATE accounts SET prefs = ? WHERE id = ?",
                   (json.dumps(new, separators=(",", ":")), account_id))
        keep = [c["id"] for c in new["cars"]]
        db.execute(f"DELETE FROM car_tokens WHERE account_id = ? AND car_id NOT IN"
                   f" ({','.join('?' * len(keep))})", (account_id, *keep))


@router.delete("/api/me")
def delete_me(user: sqlite3.Row = Depends(_signed_in_mutation)):
    """Leave the account: this member, their sessions and passkeys go. When nobody else is
    left in it, the account goes too (cars, homes, phone and calendar links)."""
    account_id = user["account_id"]
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM members WHERE id = ?", (user["id"],))
        if _member_count(db, account_id) == 0:
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


# ── Phone position updates ────────────────────────────────────────────────────
# A phone (iOS Shortcut, Tasker) reports where the car was parked with a bearer
# token that is tied to one car of one account and can do nothing else.
@router.post("/api/cars/{car_id}/token")
def create_car_token(car_id: str, user: sqlite3.Row = Depends(_signed_in_mutation)):
    """New phone token for a saved car (shown once); replaces any earlier one."""
    account_id = user["account_id"]
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not any(c["id"] == car_id for c in _stored_prefs(db, account_id)["cars"]):
            raise HTTPException(404, "Car not saved to your account yet.")
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        db.execute("DELETE FROM car_tokens WHERE account_id = ? AND car_id = ?",
                   (account_id, car_id))
        db.execute("INSERT INTO car_tokens (token_hash, car_id, created_at, account_id)"
                   " VALUES (?, ?, ?, ?)", (_hash(token), car_id, int(time.time()), account_id))
    return {"token": token}


@router.delete("/api/cars/{car_id}/token", status_code=204)
def revoke_car_token(car_id: str, user: sqlite3.Row = Depends(_signed_in_mutation)):
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM car_tokens WHERE account_id = ? AND car_id = ?",
                   (user["account_id"], car_id))


def car_for_token(request: Request) -> sqlite3.Row:
    """The (account_id, car_id) a bearer token belongs to; marks the token used.

    The token's hash goes on request.state.car_token, for record_refusal().
    """
    _require_base()
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing bearer token.")
    token_hash = _hash(token.strip())
    with closing(_connect()) as db, db:
        db.execute("UPDATE car_tokens SET last_used = ? WHERE token_hash = ?",
                   (int(time.time()), token_hash))
        row = db.execute("SELECT account_id, car_id FROM car_tokens WHERE token_hash = ?",
                         (token_hash,)).fetchone()
    if row is None:
        raise HTTPException(401, "Unknown or revoked token.")
    request.state.car_token = token_hash
    return row


def record_refusal(token_hash: str, reason: str) -> None:
    """Note why a phone report was refused, for the car's page."""
    with closing(_connect()) as db, db:
        db.execute("UPDATE car_tokens SET last_error = ? WHERE token_hash = ?",
                   (reason[:300], token_hash))


# ── Calendar subscription ─────────────────────────────────────────────────────
# A secret URL a calendar app polls (/api/calendar/<token>.ics): the account's cars'
# sweeping and homes' pickups. The token can only read that feed; one per account.
@router.post("/api/me/calendar")
def create_calendar_token(request: Request, user: sqlite3.Row = Depends(_signed_in_mutation)):
    """A new calendar feed URL (shown once); replaces any earlier one."""
    token = _CAL_PREFIX + secrets.token_urlsafe(32)
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM calendar_tokens WHERE account_id = ?", (user["account_id"],))
        db.execute("INSERT INTO calendar_tokens (token_hash, created_at, account_id)"
                   " VALUES (?, ?, ?)", (_hash(token), int(time.time()), user["account_id"]))
    base = _require_base()
    https = f"{base}/api/calendar/{token}.ics"
    return {"url": https, "webcal": "webcal://" + https.split("://", 1)[1]}


@router.delete("/api/me/calendar", status_code=204)
def revoke_calendar_token(user: sqlite3.Row = Depends(_signed_in_mutation)):
    with closing(_connect()) as db, db:
        db.execute("DELETE FROM calendar_tokens WHERE account_id = ?", (user["account_id"],))


def prefs_for_calendar_token(token: str) -> dict | None:
    """The saved prefs of the account a calendar token belongs to (marks it used)."""
    if _base() is None or not token.startswith(_CAL_PREFIX):
        return None
    token_hash = _hash(token)
    with closing(_connect()) as db, db:
        row = db.execute("SELECT account_id FROM calendar_tokens WHERE token_hash = ?",
                         (token_hash,)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE calendar_tokens SET last_used = ? WHERE token_hash = ?",
                   (int(time.time()), token_hash))
        return _stored_prefs(db, row["account_id"])


def set_car_location(account_id: str, car_id: str, lat: float, lon: float) -> bool:
    """Move a saved car, bump its position version and mark its phone token's report
    accepted; False if the car is gone."""
    with closing(_connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        prefs = _stored_prefs(db, account_id)
        car = next((c for c in prefs["cars"] if c["id"] == car_id), None)
        if car is None:
            return False
        car.update(lat=lat, lon=lon, loc_rev=car.get("loc_rev", 0) + 1)
        db.execute("UPDATE accounts SET prefs = ? WHERE id = ?",
                   (json.dumps(prefs, separators=(",", ":")), account_id))
        db.execute("UPDATE car_tokens SET last_moved = ?, last_error = NULL"
                   " WHERE account_id = ? AND car_id = ?", (int(time.time()), account_id, car_id))
    return True
