"""ReCollect trash pickups (no network: requests are monkeypatched)."""

import datetime
import threading

import pytest
import requests

from broombuster import calfeed, feeds, net, trash

_TODAY = datetime.date(2026, 9, 30)
_LATER = _TODAY + datetime.timedelta(days=3)


def _event(day, *subjects, event_type="pickup"):
    return {
        "day": day.isoformat(),
        "flags": [{"subject": s, "event_type": event_type} for s in subjects],
    }


def test_parse_pickups_groups_sorts_and_filters():
    events = [
        _event(_LATER, "Trash"),
        _event(_TODAY, "Trash", "Compost"),
        _event(_TODAY, "Trash"),
        _event(_TODAY - datetime.timedelta(days=1), "Trash"),
        _event(_TODAY, "Reminder", event_type="reminder"),
        {"day": "bad", "flags": []},
    ]
    assert trash.parse_pickups(events, _TODAY) == {
        "Compost": [_TODAY.isoformat()],
        "Trash": [_TODAY.isoformat(), _LATER.isoformat()],
    }


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_pickups_for_oakland(monkeypatch):
    trash.cache.clear()

    def _get(url, params, timeout):
        if url.endswith("address-suggest"):
            return _Resp([{"place_id": "PID"}])
        assert "/places/PID/" in url
        return _Resp({"events": [_event(_TODAY, "Trash")]})

    monkeypatch.setattr(net.session, "get", _get)
    assert trash.pickups("oakland", "1 Main St", _TODAY) == {"Trash": [_TODAY.isoformat()]}
    assert trash.pickups("san_francisco", "1 Main St", _TODAY) == {}  # no trash: block


def test_failure_is_cached_briefly(monkeypatch):
    trash.cache.clear()

    def _down(*a, **k):
        raise requests.ConnectionError

    monkeypatch.setattr(net.session, "get", _down)
    monkeypatch.setattr(feeds.time, "time", lambda: 1000.0)
    assert trash.pickups("oakland", "1 Main St", _TODAY) == {}
    ((expires, value),) = trash.cache._data.values()
    assert value is None and expires == 1000.0 + trash.cache.failure_ttl
    trash.cache.clear()


def test_feed_looks_up_trash_for_a_few_homes_only(monkeypatch):
    """A feed makes at most calfeed.TRASH_LOOKUPS uncached lookups; cached homes are free."""
    trash.cache.clear()
    asked = []

    def _get(url, params, timeout):
        if url.endswith("address-suggest"):
            asked.append(params["q"])
            return _Resp([{"place_id": params["q"]}])
        return _Resp({"events": [_event(_TODAY, "Trash")]})

    monkeypatch.setattr(net.session, "get", _get)
    homes = [
        {"id": f"h{i}", "lat": 37.8113, "lon": -122.2580, "address": f"{i} Lakeshore Ave"}
        for i in range(5)
    ]
    now = datetime.datetime(2026, 9, 30, 19, tzinfo=datetime.UTC)  # noon in Oakland
    ics = calfeed.build({"homes": homes}, lambda lat, lon: None, now=now)
    assert len(asked) == calfeed.TRASH_LOOKUPS == 3
    assert ics.count("UID:trash-") == 3
    ics = calfeed.build({"homes": homes}, lambda lat, lon: None, now=now)
    assert len(asked) == 5  # the 3 cached homes did not use up this feed's lookups
    assert ics.count("UID:trash-") == 5
    trash.cache.clear()


def test_busy_slots_are_not_cached_as_an_outage(monkeypatch):
    trash.cache.clear()
    full = threading.BoundedSemaphore(1)
    full.acquire()  # every ReCollect slot taken: answered at once, nothing sent
    monkeypatch.setitem(net._slots, "api.recollect.net", full)
    monkeypatch.setattr(net.session, "get", lambda *a, **k: pytest.fail("sent while busy"))
    assert trash.pickups("oakland", "1 Main St", _TODAY) == {}
    assert not trash.cache._data
    trash.cache.clear()


def test_check_home_endpoint(monkeypatch, app_client):
    monkeypatch.setattr(
        trash,
        "pickups",
        lambda city, address, today, day=None: (
            {"Trash": ["2026-10-01"]} if city == "oakland" else {}
        ),
    )
    data = app_client.post(
        "/api/check-home",
        json={"lat": 37.8113, "lon": -122.2580, "address": "1200 Lakeshore Ave, Oakland"},
    ).json()
    assert data == {
        "region": "bay_area",
        "city": "oakland",
        "address": "1200 Lakeshore Ave, Oakland",
        "trash_kind": "recollect",
        "pickups": {"Trash": ["2026-10-01"]},
    }


def test_check_home_weekly_city(app_client):
    data = app_client.post(
        "/api/check-home",
        json={
            "lat": 37.8716,
            "lon": -122.2727,
            "address": "2180 Milvia St, Berkeley",
            "collection_day": 2,
        },
    ).json()
    assert data["city"] == "berkeley" and data["trash_kind"] == "weekly"
    (dates,) = data["pickups"].values()
    assert all(d >= "2026" for d in dates) and len(dates) >= 7
