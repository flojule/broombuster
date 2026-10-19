"""Live feeds (closures, Chicago vehicles and permit zones), Nearby, alerts and the
calendar feed. No network: every fetch is monkeypatched."""

import datetime

import pandas as pd
import pytest

from broombuster import alerts, around, calfeed, feeds, gps

NOW = datetime.datetime(2026, 10, 17, 18, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _clean_cache():
    feeds.cache.clear()
    yield
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
    got = feeds.closures("bay_area", NOW)
    assert len(got) == 1 and got[0]["properties"]["title"] == "Street Fair"
    monkeypatch.setattr(feeds, "closures", lambda region, now=None: got)
    near = feeds.closures_near("bay_area", 37.77, -122.4495)
    assert near and near[0]["warn"] and near[0]["rule"].startswith("Sun Oct 18, 10 AM–6 PM")
    assert feeds.closures_near("bay_area", 37.78, -122.4495) == []  # ~1 km away


def test_closures_failure_is_empty(monkeypatch):
    monkeypatch.setattr(feeds, "get_json", lambda url, **p: None)
    assert feeds.closures("bay_area", NOW) == []
    assert feeds.closures("chicago", NOW) == []


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
    (f,) = feeds.closures("bay_area", NOW)
    assert f["properties"]["start"] == "2026-10-31" and f["properties"]["street"] == "Mitchell"
    assert feeds._when("2026-10-31", "2026-10-31", "bay_area") == "Sat Oct 31"


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
                {"street": "NORTH WOLCOTT", "low": 1600, "high": 1688, "side": "E", "zone": "154"},
                {"street": "NORTH WOLCOTT", "low": 1601, "high": 1699, "side": "O", "zone": "155"},
            ]
        ),
    )
    addr = {"house_number": "1640", "road": "North Wolcott Avenue"}
    monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: addr)
    (rule,) = feeds.chicago_permit_zone(41.9, -87.67)
    assert rule["label"] == "Permit zone 154"
    monkeypatch.setattr(gps, "address_parts", lambda lat, lon, network=True: None)
    assert feeds.chicago_permit_zone(41.9, -87.67) == []


def test_rules_without_geocode_never_ask_nominatim(app_client, monkeypatch):
    # The map's street card asks at every stop: no new address lookups (Nominatim's policy).
    asked = []
    monkeypatch.setattr(gps, "_nominatim", lambda *a: asked.append(a))
    params = {"lat": 41.9123, "lon": -87.6789}
    assert app_client.get("/api/rules", params={**params, "geocode": "false"}).status_code == 200
    assert not asked
    app_client.get("/api/rules", params=params)  # a car's rules still look its address up
    assert asked


def test_spare_the_air_parse():
    assert alerts.parse_spare_the_air("<p>There is no Spare the Air Alert in effect.</p>") is False
    assert alerts.parse_spare_the_air("<h2>Spare the Air Alert in effect</h2>") is True
    assert alerts.parse_spare_the_air("<p>Redesigned page</p>") is None


def test_nws_keeps_parking_relevant_events(monkeypatch):
    monkeypatch.setattr(
        feeds,
        "get_json",
        lambda url, **p: {
            "features": [
                {"properties": {"event": "Red Flag Warning", "ends": "2026-10-18T05:00:00-07:00"}},
                {"properties": {"event": "Small Craft Advisory"}},
            ]
        },
    )
    monkeypatch.setattr(alerts, "_spare_the_air", list)
    assert [a["title"] for a in alerts.at("bay_area", 37.87, -122.27)] == ["Red Flag Warning"]


def test_markets_next_day_and_distance():
    sat = datetime.date(2026, 10, 17)
    got = around.markets(37.8697, -122.2730, sat)
    names = {m["title"]: m["date"] for m in got}
    assert names["Downtown Berkeley Farmers' Market"] == "2026-10-17"
    assert names["North Berkeley Farmers' Market"] == "2026-10-22"
    assert all(m["km"] <= around.NEAR_KM for m in got)


def test_around_endpoint(app_client, monkeypatch):
    monkeypatch.setattr(feeds, "closures", lambda region, now=None: [])
    monkeypatch.setattr(
        around,
        "_legistar",
        lambda client, today: [
            {
                "kind": "meeting",
                "title": "City Council",
                "date": today.isoformat(),
                "time": "6:00 PM",
                "place": "",
                "url": "https://oakland.legistar.com/x",
                "source": "Oakland Legistar",
            }
        ],
    )
    j = app_client.get("/api/around", params={"lat": 37.8106, "lon": -122.2479}).json()
    assert j["city"] == "oakland"
    kinds = {i["kind"] for i in j["items"]}
    assert {"market", "meeting"} <= kinds
    assert any(link["title"].startswith("Oaklandside") for link in j["links"])


# ── Calendar feed ────────────────────────────────────────────────────────────


def _spot(lat, lon):
    return calfeed.CarSpot(
        "bay_area",
        "oakland",
        "Chestnut St, Oakland",
        {"even": [["W", "", "8AM-10AM"]], "odd": [["TH", "", "N/A"]]},
        "even",
    )


def test_feed_events_holidays_and_format(monkeypatch):
    prefs = {
        "cars": [{"id": "c1", "name": "Civic", "lat": 37.82, "lon": -122.28}],
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


def test_feed_line_folding_keeps_utf8():
    line = "SUMMARY:" + "é" * 60
    folded = calfeed._fold(line)
    assert all(len(p.encode()) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line
