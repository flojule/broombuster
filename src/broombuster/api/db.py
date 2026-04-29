"""
SQLite database layer — replaces Supabase for user accounts and prefs.

Schema
------
users        — bcrypt-hashed accounts
user_prefs   — per-user home location, preferred region, cars (JSON), notification prefs
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

_DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "data" / "app.sqlite")))


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id          TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    home_lat         REAL,
    home_lon         REAL,
    preferred_region TEXT NOT NULL DEFAULT 'bay_area',
    notify_email     INTEGER NOT NULL DEFAULT 0,
    cars             TEXT NOT NULL DEFAULT '[]',  -- JSON array
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
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_db() as conn:
        conn.executescript(_SCHEMA)
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

_PREFS_DEFAULT = {
    "home_lat": None,
    "home_lon": None,
    "preferred_region": "bay_area",
    "notify_email": False,
    "cars": [],
}


def get_prefs(user_id: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
    if row is None:
        return dict(_PREFS_DEFAULT)
    return {
        "home_lat":          row["home_lat"],
        "home_lon":          row["home_lon"],
        "preferred_region":  row["preferred_region"] or "bay_area",
        "notify_email":      bool(row["notify_email"]),
        "cars":              json.loads(row["cars"] or "[]"),
    }


def save_prefs(user_id: str, prefs: dict) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_prefs (user_id, home_lat, home_lon, preferred_region,
                                    notify_email, cars, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                home_lat         = excluded.home_lat,
                home_lon         = excluded.home_lon,
                preferred_region = excluded.preferred_region,
                notify_email     = excluded.notify_email,
                cars             = excluded.cars,
                updated_at       = excluded.updated_at
            """,
            (
                user_id,
                prefs.get("home_lat"),
                prefs.get("home_lon"),
                prefs.get("preferred_region", "bay_area"),
                int(bool(prefs.get("notify_email", False))),
                json.dumps(prefs.get("cars") or []),
            ),
        )
        conn.commit()
