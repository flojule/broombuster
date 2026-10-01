"""ReCollect trash pickups (no network: requests are monkeypatched)."""

import datetime

import requests

from broombuster import trash

_TODAY = datetime.date(2026, 9, 30)
_LATER = _TODAY + datetime.timedelta(days=3)


def _event(day, *subjects, event_type="pickup"):
    return {"day": day.isoformat(),
            "flags": [{"subject": s, "event_type": event_type} for s in subjects]}


def test_parse_pickups_groups_sorts_and_filters():
    events = [_event(_LATER, "Trash"), _event(_TODAY, "Trash", "Compost"),
              _event(_TODAY, "Trash"), _event(_TODAY - datetime.timedelta(days=1), "Trash"),
              _event(_TODAY, "Reminder", event_type="reminder"), {"day": "bad", "flags": []}]
    assert trash.parse_pickups(events, _TODAY) == {
        "Compost": [_TODAY.isoformat()], "Trash": [_TODAY.isoformat(), _LATER.isoformat()]}


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_pickups_for_oakland(monkeypatch):
    trash._cache.clear()

    def _get(url, params, timeout):
        if url.endswith("address-suggest"):
            return _Resp([{"place_id": "PID"}])
        assert "/places/PID/" in url
        return _Resp({"events": [_event(_TODAY, "Trash")]})
    monkeypatch.setattr(trash.requests, "get", _get)
    assert trash.pickups("oakland", "1 Main St", _TODAY) == {"Trash": [_TODAY.isoformat()]}
    assert trash.pickups("san_francisco", "1 Main St", _TODAY) == {}  # no trash: block


def test_failure_is_cached_briefly(monkeypatch):
    trash._cache.clear()

    def _down(*a, **k):
        raise requests.ConnectionError
    monkeypatch.setattr(trash.requests, "get", _down)
    monkeypatch.setattr(trash.time, "time", lambda: 1000.0)
    assert trash.pickups("oakland", "1 Main St", _TODAY) == {}
    (expires, value), = trash._cache.values()
    assert value is None and expires == 1000.0 + trash._FAILURE_TTL_S
    trash._cache.clear()


def test_check_home_endpoint(monkeypatch, app_client):
    monkeypatch.setattr(trash, "pickups", lambda city, address, today:
                        {"Trash": ["2026-10-01"]} if city == "oakland" else {})
    data = app_client.post("/check-home", json={
        "lat": 37.8113, "lon": -122.2580, "address": "1200 Lakeshore Ave, Oakland"}).json()
    assert data == {"region": "bay_area", "address": "1200 Lakeshore Ave, Oakland",
                    "pickups": {"Trash": ["2026-10-01"]}}
