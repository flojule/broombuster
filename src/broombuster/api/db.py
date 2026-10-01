"""
SQLite database layer — replaces Supabase for user accounts and prefs.

Schema
------
users        — bcrypt-hashed accounts
user_prefs   — per-user cars and homes (JSON arrays)
push_subs    — Web Push subscriptions (added in M4)

Usage
-----
    from db import get_db
    with get_db() as db:
        db.execute("SELECT ...", (...))
        db.commit()

`init_db()` is called once at startup (idempotent, safe to call on every boot).
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from broombuster.config import DEV_MODE, REPO_ROOT

_DB_PATH = Path(os.environ.get("DB_PATH", os.path.join(REPO_ROOT, "data", "app.sqlite")))


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,          -- UUID
    email       TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- home_lat/home_lon/home_address, preferred_region and notify_email are
-- unused legacy columns, kept so a rolled-back revision can still open the DB.
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    home_lat         REAL,
    home_lon         REAL,
    home_address     TEXT,
    preferred_region TEXT NOT NULL DEFAULT 'bay_area',
    notify_email     INTEGER NOT NULL DEFAULT 0,
    cars             TEXT NOT NULL DEFAULT '[]',  -- JSON array
    homes            TEXT NOT NULL DEFAULT '[]',  -- JSON array of {id,lat,lon,address}
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_subs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint    TEXT UNIQUE NOT NULL,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup.

    When `DEV_MODE` is set we also seed a synthetic `dev-user` row so that
    `save_prefs("dev-user", …)` doesn't trip the FK on user_prefs.user_id.
    deps.verify_jwt returns the literal string "dev-user" in DEV_MODE, so
    the seeded row matches whatever ID the API will pass through.
    """
    with get_db() as conn:
        # Owner-only: holds password hashes. New -wal/-shm files inherit the
        # DB's mode; fix any left over from before this was enforced.
        for f in (_DB_PATH, *(Path(f"{_DB_PATH}{s}") for s in ("-wal", "-shm"))):
            if f.exists():
                f.chmod(0o600)
        conn.execute("PRAGMA journal_mode=WAL")  # persistent per DB file
        conn.executescript(_SCHEMA)
        # Migration: add columns to pre-existing user_prefs tables.
        for ddl in (
            "ALTER TABLE user_prefs ADD COLUMN home_address TEXT",
            "ALTER TABLE user_prefs ADD COLUMN homes TEXT NOT NULL DEFAULT '[]'",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        # Fold a pre-multi-home single home into `homes`, once; clearing the old
        # columns keeps a home the user later deletes from coming back.
        conn.execute(
            """
            UPDATE user_prefs
            SET homes = json_array(json_object('id', 'legacy-home', 'lat', home_lat,
                                               'lon', home_lon, 'address', home_address)),
                home_lat = NULL, home_lon = NULL, home_address = NULL
            WHERE home_lat IS NOT NULL AND home_lon IS NOT NULL
              AND (homes IS NULL OR homes = '[]')
            """
        )
        if DEV_MODE:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
                ("dev-user", "dev@localhost", ""),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def create_user(user_id: str, email: str, pw_hash: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
            (user_id, email.lower().strip(), pw_hash),
        )
        conn.execute(
            "INSERT INTO user_prefs (user_id) VALUES (?)",
            (user_id,),
        )
        conn.commit()


def get_user_by_email(email: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()


def get_user_by_id(user_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Prefs helpers
# ---------------------------------------------------------------------------

def get_prefs(user_id: str) -> dict:
    """{"cars": [...], "homes": [...]} for a user (empty lists when unset)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT cars, homes FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return {"cars": [], "homes": []}
    return {"cars": json.loads(row["cars"] or "[]"), "homes": json.loads(row["homes"] or "[]")}


def save_prefs(user_id: str, prefs: dict) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_prefs (user_id, cars, homes, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                cars       = excluded.cars,
                homes      = excluded.homes,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(prefs.get("cars") or []), json.dumps(prefs.get("homes") or [])),
        )
        conn.commit()
