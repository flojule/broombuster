"""Live feeds (closures, Chicago vehicles and permit zones), Nearby, alerts and the
calendar feed. No network: every fetch is monkeypatched."""

import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from broombuster import alerts, calfeed, feeds, gps, net

NOW = datetime.datetime(2026, 10, 17, 18, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _clean_cache():
    for d in (feeds._snapshots, feeds._last_good, feeds._running):
        d.clear()
    feeds.cache.clear()
    yield
    for d in (feeds._snapshots, feeds._last_good, feeds._running):
        d.clear()
    feeds.cache.clear()


def test_ttl_cache_keeps_failures_briefly(monkeypatch):
    c = feeds.TTLCache(max_items=3, failure_ttl=5)
    monkeypatch.setattr(feeds.time, "time", lambda: 100.0)
    calls = []
    assert c.get("k", 60, lambda: calls.append(1)) is None
    assert c.get("k", 60, lambda: calls.append(1) or "x") is None  # failure still cached
    monkeypatch.setattr(feeds.time, "time", lambda: 106.0)
    assert c.get("k", 60, lambda: "x") == "x"
    for i in range(10):
        c.get(i, 60, lambda i=i: i)
    assert len(c._data) <= 3


_LINE = {"type": "LineString", "coordinates": [[-122.45, 37.77], [-122.449, 37.77]]}
_SF = {
    "features": [
        {
            "type": "Feature",
            "geometry": _LINE,
            "properties": {
                "case_name": "Street Fair",
                "type": "Special Event",
                "status": "Permitted",
                "start_utc": "2026-10-18T17:00:00.000",
                "end_utc": "2026-10-19T01:00:00.000",
                "loc_desc": "OAK ST between A and B",
            },
        }
    ]
}


def _fake_closures(monkeypatch, sf=_SF, oakland=None):
    def get_json(url, **params):
        if "8x25-yybr" in url:
            return sf
        if "Latest_Street_Closures" in url:
            return oakland if oakland is not None else {"features": []}
        raise AssertionError(url)

    monkeypatch.setattr(feeds, "get_json", get_json)


def test_closures_and_rules_near(monkeypatch):
    _fake_closures(monkeypatch)
    assert (
        feeds.closures("bay_area") == [] and feeds.closures_near("bay_area", 37.77, -122.45) == []
    )
    assert feeds.refresh("bay_area", NOW)
    got = feeds.closures("bay_area")
    assert len(got) == 1 and got[0]["properties"]["title"] == "Street Fair"
    near = feeds.closures_near("bay_area", 37.77, -122.4495)
    assert near and near[0]["warn"] and near[0]["rule"].startswith("Sun Oct 18, 10 AM–6 PM")
    assert feeds.closures_near("bay_area", 37.78, -122.4495) == []  # ~1 km away


def test_closures_failure_is_empty(monkeypatch):
    monkeypatch.setattr(feeds, "get_json", lambda url, **p: None)
    assert not feeds.refresh("bay_area", NOW) and feeds.closures("bay_area") == []
    assert feeds.refresh("chicago", NOW) and feeds.closures("chicago") == []


def test_oakland_closures_dates(monkeypatch):
    ms = int(datetime.datetime(2026, 10, 31, tzinfo=datetime.UTC).timestamp() * 1000)
    oak = {
        "features": [
            {
                "type": "Feature",
                "geometry": _LINE,
                "properties": {
                    "ST_NAME": "MITCHELL",
                    "Start_date": ms,
                    "End_date": ms,
                    "Event_type": "Nightmare on Mitchell Street",
                },
            }
        ]
    }
    _fake_closures(monkeypatch, sf={"features": []}, oakland=oak)
    feeds.refresh("bay_area", NOW)
    (f,) = feeds.closures("bay_area")
    assert f["properties"]["start"] == "2026-10-31" and f["properties"]["street"] == "Mitchell"
    assert feeds._when("2026-10-31", "2026-10-31", "bay_area") == "Sat Oct 31"


def test_oakland_closures_use_the_local_date(monkeypatch):
    seen = []
    monkeypatch.setattr(feeds, "get_json", lambda url, **p: seen.append(p["where"]) or {})
    feeds._oakland_closures(datetime.datetime(2026, 10, 18, 2, tzinfo=datetime.UTC))
    assert seen == ["End_date >= DATE '2026-10-17'"]  # Sat Oct 17, 7 PM PDT


def test_511_naive_dates_and_a_failing_source(monkeypatch):
    """A WZDx date without a zone is local time; a source that raises gives nothing."""

    def zone(end):
        props = {"start_date": "2026-10-16T08:00:00", "end_date": end, "core_details": {}}
        return {"type": "Feature", "geometry": _LINE, "properties": props}

    wzdx = {"features": [zone("2026-10-17T12:00:00"), zone("2026-10-17T10:00:00")]}
    monkeypatch.setattr(feeds.config, "api_511_key", lambda: "k")
    monkeypatch.setattr(feeds, "get_json", lambda url, **p: wzdx)

    def broken(now):
        raise TypeError("bad feed")

    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (broken, feeds._511_work_zones))
    assert not feeds.refresh("bay_area", NOW)  # NOW is 11 AM PDT
    got = feeds.closures("bay_area")
    assert [f["properties"]["end"] for f in got] == ["2026-10-17T12:00:00"]


def test_ttl_cache_fetches_once_for_concurrent_callers():
    c, gate, calls = feeds.TTLCache(), threading.Event(), []

    def fetch():
        calls.append(1)
        gate.wait(5)
        return "v"

    with ThreadPoolExecutor(8) as pool:
        futs = [pool.submit(c.get, "k", 60, fetch) for _ in range(8)]
        time.sleep(0.2)  # every caller is waiting on the first fetch
        gate.set()
        assert [f.result() for f in futs] == ["v"] * 8
    assert len(calls) == 1


def _feature(title):
    props = {"title": title, "start": "2026-10-18", "end": "2026-10-18", "street": ""}
    return {"type": "Feature", "geometry": _LINE, "properties": {**props, "source": "s"}}


def test_refresh_wait_is_bounded(monkeypatch):
    gate = threading.Event()

    def slow(now):
        gate.wait(5)
        return []

    monkeypatch.setattr(feeds, "TIMEOUT_S", 0.3)
    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (slow, slow, lambda now: [_LINE_F]))
    t0 = time.monotonic()
    try:
        assert not feeds.refresh("bay_area", NOW)
    finally:
        gate.set()
    assert len(feeds.closures("bay_area")) == 1 and time.monotonic() - t0 < 2


_LINE_F = {"type": "Feature", "geometry": _LINE, "properties": {}}


def test_requests_never_wait_for_a_hung_source(monkeypatch):
    """During a hung refresh, requests answer from the last snapshot at once, and the next
    refresh does not start the hung source again."""
    gate, calls, answer = threading.Event(), [], [[_feature("Old")]]

    def hung(now):
        calls.append(1)
        if len(calls) > 1:
            gate.wait(5)
        return answer[0]

    monkeypatch.setattr(feeds, "TIMEOUT_S", 0.3)
    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (hung,))
    assert feeds.refresh("bay_area", NOW)
    answer[0] = [_feature("New")]
    refresher = threading.Thread(target=feeds.refresh, args=("bay_area", NOW))
    try:
        refresher.start()
        time.sleep(0.05)  # the refresh is waiting on the hung source
        t0 = time.monotonic()
        with ThreadPoolExecutor(20) as callers:
            got = list(
                callers.map(lambda _: feeds.closures_near("bay_area", 37.77, -122.4495), range(20))
            )
        assert time.monotonic() - t0 < 0.2
        refresher.join()
        assert not feeds.refresh("bay_area", NOW)  # still hung: not fetched a third time
    finally:
        gate.set()
    assert {r["label"] for g in got for r in g} == {"Street closed: Old"} and len(calls) == 2
    assert feeds.closures("bay_area")[0]["properties"]["title"] == "Old"  # kept while it fails


def test_a_failing_source_keeps_its_last_answer_until_too_stale(monkeypatch):
    answer = [[_feature("Fair")]]
    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (lambda now: answer[0],))
    assert feeds.refresh("bay_area", NOW)
    answer[0] = None
    assert not feeds.refresh("bay_area", NOW) and len(feeds.closures("bay_area")) == 1
    t = time.time() + feeds.MAX_STALE_S + 1
    monkeypatch.setattr(feeds.time, "time", lambda: t)
    assert not feeds.refresh("bay_area", NOW) and feeds.closures("bay_area") == []


def test_unreadable_geometry_is_left_out(monkeypatch):
    bad = {**_feature("Bad"), "geometry": {"type": "LineString", "coordinates": "x"}}
    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (lambda now: [bad, _feature("Ok")],))
    feeds.refresh("bay_area", NOW)
    assert [f["properties"]["title"] for f in feeds.closures("bay_area")] == ["Ok"]
    assert len(feeds.closures_near("bay_area", 37.77, -122.4495)) == 1


def test_background_refresh_serves_the_map_and_stops(app_client, monkeypatch):
    monkeypatch.setitem(feeds._CLOSURE_SOURCES, "bay_area", (lambda now: [_feature("Fair")],))
    stop = feeds.start_refresh()
    try:
        deadline = time.monotonic() + 5
        while not feeds.closures("bay_area") and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
    r = app_client.get("/api/closures", params={"region": "bay_area"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert [f["properties"]["title"] for f in r.json()["features"]] == ["Fair"]
    rules = app_client.get("/api/rules", params={"lat": 37.77, "lon": -122.4495}).json()
    assert "Street closed: Fair" in {r["label"] for r in rules["rules"]}


def test_ttl_cache_waiter_gives_up_with_the_failure_result():
    c, gate = feeds.TTLCache(wait_s=0.1), threading.Event()
    with ThreadPoolExecutor(1) as pool:
        owner = pool.submit(c.get, "k", 60, lambda: gate.wait(5) and "v")
        time.sleep(0.05)
        assert c.get("k", 60, lambda: "again") is None  # waited 0.1 s, did not refetch
        gate.set()
        assert owner.result() == "v"


def test_chicago_vehicle(monkeypatch):
    seen = []

    def get_json(url, **params):
        seen.append(params["$where"])
        if "ygr5" in url:
            return [
                {
                    "tow_date": "2026-10-17T00:00:00",
                    "towed_to_address": "10300 S. Doty",
                    "tow_facility_phone": "(773) 568-8495",
                }
            ]
        return []

    monkeypatch.setattr(feeds, "get_json", get_json)
    got = feeds.chicago_vehicle("ab-12'3; drop", "il")
    tow = {"date": "2026-10-17", "to": "10300 S. Doty", "phone": "(773) 568-8495"}
    assert got == {"towed": [tow], "relocated": []}
    assert all("plate = 'AB123DROP' AND state = 'IL'" in w for w in seen)  # sanitised
    feeds.cache.clear()
    monkeypatch.setattr(feeds, "get_json", lambda url, **p: None)
    assert feeds.chicago_vehicle("AB123") is None


def test_busy_portal_is_nothing_known_not_an_outage(monkeypatch):
    """With every slot to the portal taken, a lookup answers None at once, uncached."""
    feeds.cache.clear()
    full = threading.BoundedSemaphore(1)
    full.acquire()
    monkeypatch.setitem(net._slots, "data.cityofchicago.org", full)
    monkeypatch.setattr(net.session, "get", lambda *a, **k: pytest.fail("sent while busy"))
    assert feeds.chicago_vehicle("AB123") is None
    assert not feeds.cache._data


def test_vehicle_endpoint(app_client, monkeypatch):
    monkeypatch.setattr(feeds, "chicago_vehicle", lambda plate, state: None)
    assert app_client.get("/api/vehicle", params={"plate": "AB123"}).status_code == 503
    assert app_client.get("/api/vehicle", params={"plate": "A'B"}).status_code == 422


def test_chicago_permit_zone(monkeypatch):
    monkeypatch.setattr(
        feeds,
        "_zones",
        pd.DataFrame(
            [
                {
                    "street": "NORTH WOLCOTT AVE",
                    "low": 1600,
                    "high": 1688,
                    "side": "E",
                    "zone": "154",
                },
                {
                    "street": "NORTH WOLCOTT AVE",
                    "low": 1601,
                    "high": 1699,
                    "side": "O",
                    "zone": "155",
                },
            ]
        ),
    )
    addr = {"house_number": "1640", "road": "North Wolcott Avenue"}
    monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: addr)
    (rule,) = feeds.chicago_permit_zone(41.9, -87.67)
    assert rule["label"] == "Permit zone 154"
    monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: None)
    assert feeds.chicago_permit_zone(41.9, -87.67) == []


def test_chicago_permit_zone_row_without_street_type(monkeypatch):
    """A typed row wins; a row without a type ("NORTH KILDARE") is the fallback."""
    rows = [
        {"street": "NORTH KILDARE", "low": 3201, "high": 3299, "side": "O", "zone": "1"},
        {"street": "NORTH KILDARE AVE", "low": 3201, "high": 3255, "side": "O", "zone": "2"},
    ]
    monkeypatch.setattr(feeds, "_zones", pd.DataFrame(rows))

    def zones(number):
        addr = {"house_number": number, "road": "North Kildare Avenue"}
        monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: addr)
        return [r["label"] for r in feeds.chicago_permit_zone(41.94, -87.73)]

    assert zones("3255") == ["Permit zone 2"]
    assert zones("3257") == ["Permit zone 1"]


@pytest.mark.parametrize(
    ("number", "road", "zone"),
    [
        ("2610", "West 23rd Place", "1201"),  # W 23rd St 2600-2652 is zone 567
        ("2610", "West 23rd Street", "567"),
        ("2610A", "West 23rd Place", "1201"),
    ],
)
def test_chicago_permit_zone_by_street_type(monkeypatch, number, road, zone):
    monkeypatch.setattr(feeds, "_zones", None)  # the committed table
    addr = {"house_number": number, "road": road}
    monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: addr)
    assert [r["label"] for r in feeds.chicago_permit_zone(41.85, -87.69)] == [f"Permit zone {zone}"]


def test_rules_without_geocode_never_ask_nominatim(app_client, monkeypatch):
    # The map's street card asks at every stop: no new address lookups (Nominatim's policy).
    asked = []
    monkeypatch.setattr(gps, "_nominatim", lambda *a: asked.append(a))
    params = {"lat": 41.9123, "lon": -87.6789}
    assert app_client.get("/api/rules", params={**params, "geocode": "false"}).status_code == 200
    assert not asked
    app_client.get("/api/rules", params=params)  # a car's rules still look its address up
    assert asked


def test_nws_keeps_parking_relevant_events(monkeypatch):
    monkeypatch.setattr(
        feeds,
        "get_json",
        lambda url, **p: {
            "features": [
                {"properties": {"event": "Red Flag Warning", "ends": "2026-10-18T05:00:00-07:00"}},
                {"properties": {"event": "Small Craft Advisory"}},
                {"properties": {"event": "Heat Advisory"}},
            ]
        },
    )
    assert [a["title"] for a in alerts.at(37.87, -122.27)] == ["Red Flag Warning"]


# ── Calendar feed ────────────────────────────────────────────────────────────


def _spot(lat, lon):
    return calfeed.CarSpot(
        "bay_area",
        "oakland",
        "Chestnut St, Oakland",
        {"even": [["W", "", "8AM-10AM"]], "odd": [["TH", "", "N/A"]]},
        ("North", "South"),
    )


def test_feed_events_holidays_and_format(monkeypatch):
    prefs = {
        "cars": [{"id": "c1", "name": "Civic", "lat": 37.82, "lon": -122.28, "side": "even"}],
        "homes": [
            {"id": "h1", "lat": 37.8716, "lon": -122.2727, "address": "", "collection_day": 3}
        ],
    }
    ics = calfeed.build(prefs, _spot, now=datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.UTC))
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert "DTSTART:20261104T160000Z" in ics  # Wed Nov 4, 8 AM PST
    assert "UID:sweep-c1-20261111" not in ics  # Veterans Day: no sweeping in Oakland
    assert "No street sweeping: Veterans Day" in ics
    assert "sweep-c1-20261105" not in ics  # the odd side is not the car's
    assert "UID:trash-h1-20261127" in ics  # Thanksgiving week: Thu -> Fri
    assert ics.count("BEGIN:VALARM") >= 8
    week = calfeed.build(
        prefs, _spot, now=datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.UTC), days=7
    )
    assert "Veterans Day" not in week  # Nov 11 is past the 7 days


def test_feed_line_folding_keeps_utf8():
    line = "SUMMARY:" + "é" * 60
    folded = calfeed._fold(line)
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line


def test_feed_without_side_and_next_sweep_only():
    """No picked side: both sides' days, each named; first_only keeps the next sweep not
    over yet, with its two reminders."""
    prefs = {"cars": [{"id": "c1", "lat": 37.82, "lon": -122.28}]}
    ics = calfeed.build(prefs, _spot, now=datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.UTC))
    assert "UID:sweep-c1-20261104-even" in ics and "UID:sweep-c1-20261105-odd" in ics
    assert "North side" in ics and "South side" in ics

    def first(utc_hour):
        now = datetime.datetime(2026, 11, 4, utc_hour, tzinfo=datetime.UTC)
        return calfeed.build(prefs, _spot, now=now, first_only=True)

    running = first(17)  # Wed 9 AM PST, inside 8-10 AM
    assert running.count("BEGIN:VEVENT") == 1 and "UID:sweep-c1-20261104-even" in running
    assert running.count("BEGIN:VALARM") == len(calfeed.TIMED_ALARMS)
    over = first(19)  # Wed 11 AM PST: next is Thursday's all-day odd side
    assert over.count("BEGIN:VEVENT") == 1 and "UID:sweep-c1-20261105-odd" in over


def test_next_sweep_keeps_last_nights_window_while_it_runs():
    def spot(lat, lon):
        night = {"even": [["W", "", "10PM-2AM"], ["W", "", "8AM-10AM"]], "odd": []}
        return calfeed.CarSpot("bay_area", "oakland", "Chestnut St", night, ("Even", "Odd"))

    def first(utc_hour):
        now = datetime.datetime(2026, 11, 5, utc_hour, tzinfo=datetime.UTC)
        car = {"id": "c1", "lat": 0, "lon": 0}
        return calfeed.build({"cars": [car]}, spot, now, first_only=True)

    running = first(9)  # Thu 1 AM PST: Wednesday's 10 PM-2 AM is on, its 8-10 AM is over
    assert running.count("BEGIN:VEVENT") == 1
    assert "DTSTART:20261105T060000Z" in running and "DTEND:20261105T100000Z" in running
    assert "UID:sweep-c1-20261118" in first(11)  # 3 AM: over; Nov 11 is Veterans Day


def test_feed_one_event_when_both_sides_share_the_time():
    def spot(lat, lon):
        both = [["W", "", "8AM-10AM"]]
        return calfeed.CarSpot(
            "bay_area", "oakland", "Chestnut St", {"even": both, "odd": both}, ("Even", "Odd")
        )

    now = datetime.datetime(2026, 11, 1, 12, tzinfo=datetime.UTC)
    ics = calfeed.build(
        {"cars": [{"id": "c1", "lat": 0, "lon": 0}]}, spot, now=now, first_only=True
    )
    assert ics.count("BEGIN:VEVENT") == 1 and "UID:sweep-c1-20261104-both" in ics
    assert " side" not in ics
