"""
FastAPI dependency — JWT verification.

Accepts locally-issued HS256 tokens from api/auth.py.
DEV_MODE=true skips verification and returns "dev-user".

    user_id: str = Depends(verify_jwt)
"""

import jwt
from fastapi import Header, HTTPException

from broombuster.config import DEV_MODE

from .auth import decode_access


def verify_jwt(authorization: str = Header(default="")) -> str:
    """Verify a locally-issued HS256 JWT and return the user_id (sub claim)."""
    if DEV_MODE:
        return "dev-user"
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    try:
        return decode_access(authorization.split(" ", 1)[1])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
