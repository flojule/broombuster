"""ReCollect adapter + ReCollectTrashPlugin.

Offline: parser + plugin.format with a monkeypatched adapter (no network).
A network-guarded live test confirms Oakland's real endpoints still answer;
it is skipped when RECOLLECT_LIVE is unset.
"""

import datetime
import os
from zoneinfo import ZoneInfo

os.environ.setdefault("DEV_MODE", "1")

import pytest

from broombuster import recollect
from broombuster.domains import DomainPlugin
from broombuster.domains.trash import ReCollectTrashPlugin

_TZ = ZoneInfo("America/Los_Angeles")
_TODAY = datetime.date.today()
_TOMORROW = _TODAY + datetime.timedelta(days=1)
_AFTER = _TODAY + datetime.timedelta(days=3)
_NOW = datetime.datetime.combine(_TODAY, datetime.time(7, 0), tzinfo=_TZ)


def _event(day, *flag_names, event_type="pickup"):
    return {
        "day": day.isoformat(),
        "flags": [{"name": n.lower(), "subject": n, "event_type": event_type}
                  for n in flag_names],
    }


class TestParsePickups:
    def test_groups_by_stream_and_sorts(self):
        events = [
            _event(_AFTER, "Trash"),
            _event(_TODAY, "Trash"),
            _event(_TOMORROW, "Compost"),
        ]
        out = recollect.parse_pickups(events, _TODAY)
        assert out["Trash"] == [_TODAY, _AFTER]
        assert out["Compost"] == [_TOMORROW]

    def test_drops_past_dates(self):
        out = recollect.parse_pickups(
            [_event(_TODAY - datetime.timedelta(days=2), "Trash")], _TODAY)
        assert out == {}

    def test_ignores_non_pickup_flags(self):
        out = recollect.parse_pickups(
            [_event(_TODAY, "Reminder", event_type="reminder")], _TODAY)
        assert out == {}

    def test_dedupes_dates(self):
        out = recollect.parse_pickups([_event(_TODAY, "Trash"),
                                       _event(_TODAY, "Trash")], _TODAY)
        assert out["Trash"] == [_TODAY]

    def test_malformed_day_skipped(self):
        out = recollect.parse_pickups([{"day": "not-a-date",
                                        "flags": [{"name": "trash",
                                                   "event_type": "pickup"}]}], _TODAY)
        assert out == {}


class TestPlugin:
    def setup_method(self):
        self.cities = {"oakland": {"trash": {"kind": "recollect",
                                             "area": "OaklandCA", "service_id": 608}}}

    def test_protocol_and_subject(self):
        p = ReCollectTrashPlugin()
        assert isinstance(p, DomainPlugin)
        assert p.subject == "home"

    def test_supports_only_recollect_kind(self):
        assert ReCollectTrashPlugin(cities=self.cities).supports_city("oakland")
        assert not ReCollectTrashPlugin(
            cities={"x": {"trash": {"kind": "zone"}}}).supports_city("x")

    def test_resolve_requires_address(self, monkeypatch):
        monkeypatch.setattr(recollect, "suggest_place", lambda *a, **k: "PID")
        p = ReCollectTrashPlugin(cities=self.cities)
        assert p.resolve_for(None, 37.8, -122.2, "oakland", address=None) is None
        r = p.resolve_for(None, 37.8, -122.2, "oakland", address="1 Main St")
        assert r and r["place_id"] == "PID"

    def test_resolve_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(recollect, "enabled", lambda: False)
        p = ReCollectTrashPlugin(cities=self.cities)
        assert p.resolve_for(None, 37.8, -122.2, "oakland", address="1 Main St") is None

    def _format(self, monkeypatch, pickups):
        monkeypatch.setattr(recollect, "fetch_pickups", lambda *a, **k: pickups)
        p = ReCollectTrashPlugin(cities=self.cities)
        resolved = {"place_id": "PID", "service_id": 608, "address": "1 Main St"}
        return p.format(resolved, None, _NOW)

    def test_format_today(self, monkeypatch):
        res = self._format(monkeypatch, {"Trash": [_TODAY], "Compost": [_AFTER]})
        assert res.urgency == "today"
        assert any(line.startswith("Trash:") for line in res.schedule_lines)
        assert res.extras["streams"]["Trash"] == [_TODAY.isoformat()]

    def test_format_tomorrow(self, monkeypatch):
        res = self._format(monkeypatch, {"Trash": [_TOMORROW]})
        assert res.urgency == "tomorrow"

    def test_format_safe_when_future(self, monkeypatch):
        res = self._format(monkeypatch, {"Trash": [_AFTER]})
        assert res.urgency == "safe"

    def test_format_no_pickups_is_safe(self, monkeypatch):
        res = self._format(monkeypatch, {})
        assert res.urgency == "safe"
        assert res.schedule_lines

    def test_format_unresolved_is_safe(self):
        res = ReCollectTrashPlugin().format(None, None, _NOW)
        assert res.urgency == "safe"
        assert res.schedule_lines == ["Trash schedule unavailable"]


class TestCheckHomeEndpoint:
    def test_home_endpoint_returns_trash_domain(self, monkeypatch):
        from fastapi.testclient import TestClient

        from broombuster.api import app as app_module

        monkeypatch.setattr(recollect, "suggest_place", lambda *a, **k: "PID")
        monkeypatch.setattr(recollect, "fetch_pickups",
                            lambda *a, **k: {"Trash": [_TODAY]})
        with TestClient(app_module.app) as client:
            resp = client.post("/check-home", json={
                "lat": 37.8113, "lon": -122.2580, "region": "bay_area",
                "address": "1200 Lakeshore Ave, Oakland",
            })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        ids = [d["id"] for d in data["domains"]]
        assert "trash" in ids
        trash = next(d for d in data["domains"] if d["id"] == "trash")
        assert trash["urgency"] == "today"

    def test_car_check_excludes_home_domains(self):
        from fastapi.testclient import TestClient

        from broombuster.api import app as app_module

        with TestClient(app_module.app) as client:
            resp = client.post("/check", json={
                "lat": 37.821326, "lon": -122.280705, "region": "bay_area",
            })
        ids = [d["id"] for d in resp.json()["domains"]]
        assert "sweeping" in ids
        assert "trash" not in ids, "car /check must not run home-subject trash"


@pytest.mark.skipif(not os.environ.get("RECOLLECT_LIVE"),
                    reason="set RECOLLECT_LIVE=1 to hit the live ReCollect API")
class TestLiveOakland:
    def test_suggest_and_fetch(self):
        recollect.clear_caches()
        pid = recollect.suggest_place("OaklandCA", 608, "1200 Lakeshore Ave, Oakland")
        assert pid, "expected a place_id for a known Oakland address"
        pickups = recollect.fetch_pickups(pid, 608, today=datetime.date.today())
        assert pickups, "expected upcoming pickups"
        assert any("rash" in k or "ompost" in k for k in pickups)
