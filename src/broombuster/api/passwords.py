"""
Password hashing — bcrypt directly (passlib 1.7.x breaks on bcrypt >= 4.1).

Split out from auth.py so tools that only need to hash a password (e.g.
scripts/seed_account.py) can import it without triggering auth.py's import-time
JWT_SECRET guard.
"""

import secrets

import bcrypt

# bcrypt ignores bytes past position 72; truncate so long passwords hash
# instead of raising.
_BCRYPT_MAX_BYTES = 72


def _hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()).decode("ascii")


def _verify_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# Constant-time decoy for unknown-email logins (blocks user enumeration by
# timing). A real hash of a random secret, so _verify_pw never rejects it as
# malformed the way a hand-written placeholder string does.
_DUMMY_PW_HASH = _hash_pw(secrets.token_hex(16))
