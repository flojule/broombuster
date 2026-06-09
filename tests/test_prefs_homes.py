"""Prefs persistence for multiple homes + legacy single-home backfill."""

import os

os.environ.setdefault("DEV_MODE", "1")

import pytest

from broombuster.api import db


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "prefs_test.sqlite")
    db.init_db()  # DEV_MODE seeds the 'dev-user' row (FK target)
    return db


def test_homes_array_roundtrip(fresh_db):
    homes = [
        {"id": "h1", "lat": 37.80, "lon": -122.27, "address": "1 Foo St"},
        {"id": "h2", "lat": 37.81, "lon": -122.28, "address": "2 Bar Ave"},
    ]
    fresh_db.save_prefs("dev-user", {"homes": homes, "cars": []})
    got = fresh_db.get_prefs("dev-user")
    assert got["homes"] == homes


def test_legacy_single_home_backfills_into_array(fresh_db):
    # Simulate a pre-multi-home row: only the singular columns are populated.
    fresh_db.save_prefs("dev-user", {"cars": []})  # create the prefs row
    with fresh_db.get_db() as conn:
        conn.execute(
            "UPDATE user_prefs SET home_lat=?, home_lon=?, home_address=?, homes='[]' "
            "WHERE user_id='dev-user'",
            (37.8044, -122.2712, "150 Frank Ogawa Plaza"),
        )
        conn.commit()
    got = fresh_db.get_prefs("dev-user")
    assert len(got["homes"]) == 1
    h = got["homes"][0]
    assert (h["lat"], h["lon"], h["address"]) == (37.8044, -122.2712, "150 Frank Ogawa Plaza")


def test_homes_array_takes_precedence_over_legacy_columns(fresh_db):
    with fresh_db.get_db() as conn:
        conn.execute(
            "UPDATE user_prefs SET home_lat=?, home_lon=? WHERE user_id='dev-user'",
            (1.0, 2.0),
        )
        conn.commit()
    homes = [{"id": "h1", "lat": 37.8, "lon": -122.2, "address": "A"}]
    fresh_db.save_prefs("dev-user", {"homes": homes})
    got = fresh_db.get_prefs("dev-user")
    assert got["homes"] == homes  # array wins; no legacy synthesis


def test_empty_homes_when_nothing_saved(fresh_db):
    assert fresh_db.get_prefs("dev-user")["homes"] == []
