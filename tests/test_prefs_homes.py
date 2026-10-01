"""Prefs persistence for cars/homes + the one-time legacy single-home migration."""

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


def _set_legacy_home(db_mod, homes="'[]'"):
    with db_mod.get_db() as conn:
        conn.execute(
            f"UPDATE user_prefs SET home_lat=?, home_lon=?, home_address=?, homes={homes} "
            "WHERE user_id='dev-user'",
            (37.8044, -122.2712, "150 Frank Ogawa Plaza"),
        )
        conn.commit()


def test_legacy_single_home_migrates_into_array(fresh_db):
    fresh_db.save_prefs("dev-user", {"cars": []})  # create the prefs row
    _set_legacy_home(fresh_db)
    fresh_db.init_db()  # next boot migrates
    (h,) = fresh_db.get_prefs("dev-user")["homes"]
    assert (h["lat"], h["lon"], h["address"]) == (37.8044, -122.2712, "150 Frank Ogawa Plaza")


def test_deleted_legacy_home_stays_deleted(fresh_db):
    fresh_db.save_prefs("dev-user", {"cars": []})
    _set_legacy_home(fresh_db)
    fresh_db.init_db()
    fresh_db.save_prefs("dev-user", {"cars": [], "homes": []})  # user removes it
    fresh_db.init_db()
    assert fresh_db.get_prefs("dev-user")["homes"] == []


def test_homes_array_is_not_overwritten_by_legacy_columns(fresh_db):
    homes = [{"id": "h1", "lat": 37.8, "lon": -122.2, "address": "A"}]
    fresh_db.save_prefs("dev-user", {"homes": homes})
    _set_legacy_home(fresh_db, homes="homes")
    fresh_db.init_db()
    assert fresh_db.get_prefs("dev-user")["homes"] == homes


def test_empty_prefs_when_nothing_saved(fresh_db):
    assert fresh_db.get_prefs("dev-user") == {"cars": [], "homes": []}
