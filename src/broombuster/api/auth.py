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
Install: pip install slowapi
"""

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from . import db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")

_JWT_SECRET     = os.environ.get("JWT_SECRET") or (
    "dev-secret-change-me" if _DEV_MODE else None
)
_REFRESH_SECRET = os.environ.get("REFRESH_SECRET") or (
    (_JWT_SECRET + "-refresh") if _JWT_SECRET else None
)

if not _DEV_MODE and not _JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET env var must be set in production. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_ACCESS_TTL_MINUTES  = 15
_REFRESH_TTL_DAYS    = 30
_AUD_ACCESS          = "broombuster"
_AUD_REFRESH         = "broombuster-refresh"

# ---------------------------------------------------------------------------
# Rate limiting — slowapi; disabled when slowapi is absent or in DEV_MODE
# ---------------------------------------------------------------------------

_RATE_LIMIT = "10/minute"

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = None if _DEV_MODE else Limiter(key_func=get_remote_address)
except ImportError:
    _limiter = None
    _RATE_LIMIT = None


def _maybe_limit(func):
    """Apply the slowapi limit decorator when a limiter is active; else no-op."""
    if _limiter is not None and _RATE_LIMIT:
        return _limiter.limit(_RATE_LIMIT)(func)
    return func


def init_rate_limiting(app) -> None:
    """Attach the limiter and 429 handler to the FastAPI app (no-op if disabled)."""
    if _limiter is None:
        return
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _issue_access(user_id: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "aud": _AUD_ACCESS,
        "iat": now,
        "exp": now + timedelta(minutes=_ACCESS_TTL_MINUTES),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


def _issue_refresh(user_id: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "aud": _AUD_REFRESH,
        "iat": now,
        "exp": now + timedelta(days=_REFRESH_TTL_DAYS),
    }
    return jwt.encode(payload, _REFRESH_SECRET, algorithm="HS256")


def decode_access(token: str) -> str:
    """Verify an access token and return user_id (sub). Raises jwt exceptions on failure."""
    payload = jwt.decode(
        token, _JWT_SECRET, algorithms=["HS256"], audience=_AUD_ACCESS
    )
    return payload["sub"]


def decode_refresh(token: str) -> str:
    """Verify a refresh token and return user_id. Raises jwt exceptions on failure."""
    payload = jwt.decode(
        token, _REFRESH_SECRET, algorithms=["HS256"], audience=_AUD_REFRESH
    )
    return payload["sub"]


# ---------------------------------------------------------------------------
# Password hashing — bcrypt directly (passlib 1.7.x breaks on bcrypt >= 4.1)
# ---------------------------------------------------------------------------

# bcrypt ignores bytes past position 72; truncate so long passwords hash
# instead of raising.
_BCRYPT_MAX_BYTES = 72

try:
    import bcrypt

    def _hash_pw(pw: str) -> str:
        return bcrypt.hashpw(
            pw.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()
        ).decode("ascii")

    def _verify_pw(pw: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(
                pw.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("ascii")
            )
        except (ValueError, TypeError):
            return False

except ImportError:
    # Dev-only fallback when bcrypt isn't installed.
    import hashlib

    def _hash_pw(pw: str) -> str:
        salt = secrets.token_hex(16)
        h = hashlib.sha256((salt + pw).encode()).hexdigest()
        return f"sha256:{salt}:{h}"

    def _verify_pw(pw: str, hashed: str) -> bool:
        if hashed.startswith("sha256:"):
            _, salt, h = hashed.split(":", 2)
            return hashlib.sha256((salt + pw).encode()).hexdigest() == h
        return False


# Constant-time decoy for unknown-email logins (blocks user enumeration by
# timing). A real hash of a random secret, so _verify_pw never rejects it as
# malformed the way a hand-written placeholder string does.
_DUMMY_PW_HASH = _hash_pw(secrets.token_hex(16))


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
        "access_token":  _issue_access(user_id),
        "refresh_token": _issue_refresh(user_id),
        "token_type":    "bearer",
        "user_id":       user_id,
    }


@router.post("/register")
@_maybe_limit
def register(req: RegisterRequest, request: Request):
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
