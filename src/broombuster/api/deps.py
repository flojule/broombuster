"""
FastAPI dependency — JWT verification.

Accepts locally-issued HS256 tokens from api/auth.py.
DEV_MODE=true skips verification and returns "dev-user".

Route decorators are unchanged:
    user_id: str = Depends(verify_jwt)
"""

import os

import jwt
from fastapi import Header, HTTPException

_DEV_MODE = os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes")


def verify_jwt(authorization: str = Header(default="")) -> str:
    """Verify a locally-issued HS256 JWT and return the user_id (sub claim)."""
    if _DEV_MODE:
        return "dev-user"

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1]

    # Import here to avoid a circular-import at module load time
    # (auth.py imports db.py which is imported before api.py finishes wiring).
    try:
        from auth import decode_access
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Auth module unavailable: {exc}")

    try:
        return decode_access(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
