#!/usr/bin/env python3
"""
Seed (or reset) the shared BroomBuster account for a public deploy.

On a public deploy self-registration is disabled (ALLOW_REGISTRATION=false), so
the one shareable account is created here, server-side, instead of through the
sign-up form. Idempotent: re-running with an existing email resets its password.

The account's prefs (cars, homes) persist server-side in the same SQLite DB the
API uses, so everyone who signs into it shares one synced set — that is the
point of a "share if needed" login. Guest mode (no login) stays available and
keeps its data per-device in the browser.

Usage
-----
    # Explicit credentials
    SEED_EMAIL=share@broombuster SEED_PASSWORD='a-strong-passphrase' \
        python scripts/seed_account.py

    # Or pass on the command line
    python scripts/seed_account.py --email share@broombuster --password '...'

    # Omit the password to auto-generate a strong one (printed once)
    python scripts/seed_account.py --email share@broombuster

The DB location follows the same DB_PATH env var the API uses (default
<repo>/data/app.sqlite), so run this against the same DB the server reads.
"""

import argparse
import os
import secrets
import sys
import uuid

# broombuster.config loads .env on import, so DB_PATH matches the running app.
# The hasher comes from passwords.py, not auth.py: auth.py refuses to import in
# production unless JWT_SECRET is set, but seeding only needs to hash a password.
from broombuster.api import db
from broombuster.api.passwords import _hash_pw

_MIN_PW_LEN = 8


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed/reset the shared account.")
    parser.add_argument(
        "--email",
        default=os.environ.get("SEED_EMAIL", "share@broombuster"),
        help="Account email (default: SEED_EMAIL env or share@broombuster).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("SEED_PASSWORD"),
        help="Account password (default: SEED_PASSWORD env; auto-generated if unset).",
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    password = args.password
    generated = False
    if not password:
        # token_urlsafe(12) → ~16 chars, well over the 8-char minimum.
        password = secrets.token_urlsafe(12)
        generated = True
    if len(password) < _MIN_PW_LEN:
        print(f"Password must be at least {_MIN_PW_LEN} characters.", file=sys.stderr)
        return 2

    db.init_db()

    existing = db.get_user_by_email(email)
    if existing:
        # Reset the password in place; keep the user id so existing prefs stay
        # attached to the account.
        with db.get_db() as conn:
            conn.execute(
                "UPDATE users SET pw_hash = ? WHERE email = ?",
                (_hash_pw(password), email),
            )
            conn.commit()
        action = "updated (password reset)"
    else:
        db.create_user(str(uuid.uuid4()), email, _hash_pw(password))
        action = "created"

    print(f"Shared account {action}:")
    print(f"  email:    {email}")
    # Only echo the password when we generated it; otherwise the operator
    # already knows it, so we avoid printing secrets to logs unnecessarily.
    if generated:
        print(f"  password: {password}    <-- auto-generated, copy it now")
    else:
        print("  password: (the one you supplied)")
    print(f"  DB:       {db._DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
