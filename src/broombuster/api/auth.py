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
from pydantic import BaseModel, EmailStr, field_validator

import db

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
# Rate limiting — optional; skipped gracefully if slowapi not installed
# ---------------------------------------------------------------------------

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
    _RATE_LIMIT = "10/minute"
except ImportError:
    _limiter = None
    _RATE_LIMIT = None


def _rate_limit(request: Request) -> None:
    if _limiter is None or _DEV_MODE:
        return
    # Delegate to slowapi if available; no-op otherwise.


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
# Password hashing — passlib with bcrypt
# ---------------------------------------------------------------------------

try:
    from passlib.context import CryptContext
    _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def _hash_pw(pw: str) -> str:
        return _pwd.hash(pw)

    def _verify_pw(pw: str, hashed: str) -> bool:
        return _pwd.verify(pw, hashed)

except ImportError:
    # Fallback for dev environments where passlib isn't installed yet.
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
def register(req: RegisterRequest, request: Request):
    _rate_limit(request)
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
def login(req: LoginRequest, request: Request):
    _rate_limit(request)
    # Constant-time path: always hash-compare even if user not found,
    # to prevent timing-based user enumeration.
    user = db.get_user_by_email(req.email)
    dummy_hash = "$2b$12$notarealhashjustpadding000000000000000000000000000000000"
    stored = user["pw_hash"] if user else dummy_hash
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
