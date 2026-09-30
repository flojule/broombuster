"""Deployment hardening: security headers, prefs bounds, DB perms, data age."""

import os
import stat
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from broombuster.api import app as app_module
from broombuster.api import db, state
from broombuster.cities import CITIES


def test_security_headers_and_no_cors():
    # No `with`: skip the lifespan (city loading); /cities needs no data.
    r = TestClient(app_module.app).get("/cities", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "access-control-allow-origin" not in r.headers


def test_prefs_list_bounds():
    with pytest.raises(ValidationError):
        app_module.PrefsRequest(cars=[{}] * 51)
    with pytest.raises(ValidationError):
        app_module.PrefsRequest(homes=[{}] * 21)
    with pytest.raises(ValidationError):
        app_module.PrefsRequest(cars=["not-an-object"])


def test_prefs_size_limit(monkeypatch):
    saved = []
    monkeypatch.setattr(db, "save_prefs", lambda uid, prefs: saved.append(prefs))
    big = app_module.PrefsRequest(cars=[{"name": "x" * 70_000}])
    with pytest.raises(HTTPException) as exc:
        app_module.save_prefs(big, None, "user")
    assert exc.value.status_code == 413
    assert not saved
    app_module.save_prefs(app_module.PrefsRequest(cars=[{"name": "ok"}]), None, "user")
    assert saved


def test_db_is_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "perm.sqlite"
    path.touch(mode=0o644)
    monkeypatch.setattr(db, "_DB_PATH", path)
    db.init_db()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_data_age_uses_commit_time_not_mtime(monkeypatch):
    city = CITIES["san_francisco"]
    ten_days_ago = time.time() - 10 * 86400
    monkeypatch.setattr(state, "_git_commit_time", lambda path: ten_days_ago)
    monkeypatch.setattr(state, "DATA_AUTO_REFRESH", False)
    assert state.data_age_days(city) == pytest.approx(10, abs=0.01)
    # With runtime refresh on, the file's mtime is the truth.
    monkeypatch.setattr(state, "DATA_AUTO_REFRESH", True)
    assert state.data_age_days(city) != pytest.approx(10, abs=0.01)


def test_git_commit_time_for_tracked_file():
    path = os.path.join(state.REPO_ROOT, CITIES["san_francisco"]["fgb_path"])
    t = state._git_commit_time(path)
    assert t is not None and t <= time.time()
