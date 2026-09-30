"""
Local authentication — replaces Supabase Auth.

Routes
------
POST /auth/register  — create account, return tokens
POST /auth/login     — verify password, return tokens
POST /auth/refresh   — exchange refresh token for new access token

Tokens
------
Access token:  HS256 JWT, 15-minute TTL, audience="broombuster"
Refresh token: HS256 JWT, 30-day TTL, audience="broombuster-refresh"

Both contain {"sub": user_id, "aud": ..., "exp": ..., "iat": ...}.

Environment variables
---------------------
JWT_SECRET        — required in production; auto-generated in DEV_MODE
REFRESH_SECRET    — optional; falls back to JWT_SECRET + "-refresh"
DEV_MODE          — if "true", suppresses slowapi rate limiting

Rate limiting
-------------
Failed logins are rate-limited via slowapi (10 per minute per IP).
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from broombuster.config import ALLOW_REGISTRATION, DEV_MODE

# Password hashing lives in passwords.py so seed_account.py can import the
# hasher without tripping the JWT_SECRET guard below. _DUMMY_PW_HASH is the
# constant-time decoy the login path uses to block user enumeration.
from . import db
from .passwords import _DUMMY_PW_HASH, _hash_pw, _verify_pw

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_JWT_SECRET     = os.environ.get("JWT_SECRET") or (
    "dev-only-secret-not-for-production" if DEV_MODE else None
)
_REFRESH_SECRET = os.environ.get("REFRESH_SECRET") or (
    (_JWT_SECRET + "-refresh") if _JWT_SECRET else None
)

_MIN_SECRET_LEN = 32  # RFC 7518 s3.2: HS256 key >= 32 bytes

if not DEV_MODE and not (_JWT_SECRET and len(_JWT_SECRET) >= _MIN_SECRET_LEN
                         and len(_REFRESH_SECRET) >= _MIN_SECRET_LEN):
    raise RuntimeError(
        f"JWT_SECRET (and REFRESH_SECRET if set) must be >= {_MIN_SECRET_LEN} chars in "
        "production. Generate one with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_ACCESS_TTL          = timedelta(minutes=15)
_REFRESH_TTL         = timedelta(days=30)
_AUD_ACCESS          = "broombuster"
_AUD_REFRESH         = "broombuster-refresh"

# ---------------------------------------------------------------------------
# Rate limiting — slowapi; disabled in DEV_MODE
# ---------------------------------------------------------------------------

_RATE_LIMIT = "10/minute"

_limiter = None if DEV_MODE else Limiter(key_func=get_remote_address)


def rate_limit(rate: str | None):
    """Per-IP slowapi limit decorator when a limiter is active; else no-op.

    The decorated route must take a `request: Request` parameter.
    """
    def deco(func):
        if _limiter is not None and rate:
            return _limiter.limit(rate)(func)
        return func
    return deco


_maybe_limit = rate_limit(_RATE_LIMIT)


def init_rate_limiting(app) -> None:
    """Attach the limiter and 429 handler to the FastAPI app (no-op if disabled)."""
    if _limiter is None:
        return
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _issue(user_id: str, secret: str, aud: str, ttl: timedelta) -> str:
    now = datetime.now(tz=UTC)
    payload = {"sub": user_id, "aud": aud, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access(token: str) -> str:
    """Verify an access token and return user_id (sub). Raises jwt exceptions on failure."""
    return jwt.decode(token, _JWT_SECRET, algorithms=["HS256"], audience=_AUD_ACCESS)["sub"]


def decode_refresh(token: str) -> str:
    """Verify a refresh token and return user_id. Raises jwt exceptions on failure."""
    return jwt.decode(token, _REFRESH_SECRET, algorithms=["HS256"], audience=_AUD_REFRESH)["sub"]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str

    @field_validator("password")
    @classmethod
    def pw_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


def _token_response(user_id: str) -> dict:
    return {
        "access_token":  _issue(user_id, _JWT_SECRET, _AUD_ACCESS, _ACCESS_TTL),
        "refresh_token": _issue(user_id, _REFRESH_SECRET, _AUD_REFRESH, _REFRESH_TTL),
        "token_type":    "bearer",
        "user_id":       user_id,
    }


@router.post("/register")
@_maybe_limit
def register(req: RegisterRequest, request: Request):
    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    existing = db.get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    user_id = str(uuid.uuid4())
    try:
        db.create_user(user_id, req.email, _hash_pw(req.password))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Registration failed: {exc}")
    return _token_response(user_id)


@router.post("/login")
@_maybe_limit
def login(req: LoginRequest, request: Request):
    # Constant-time path: always hash-compare even if user not found,
    # to prevent timing-based user enumeration.
    user = db.get_user_by_email(req.email)
    stored = user["pw_hash"] if user else _DUMMY_PW_HASH
    ok = _verify_pw(req.password, stored)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _token_response(user["id"])


@router.post("/refresh")
def refresh(req: RefreshRequest):
    try:
        user_id = decode_refresh(req.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {exc}")
    if db.get_user_by_id(user_id) is None:
        raise HTTPException(status_code=401, detail="User not found")
    return _token_response(user_id)
