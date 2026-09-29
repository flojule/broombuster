"""iCalendar feed: DST-correct UTC times, side filtering, folding, endpoint."""
import datetime
import os

os.environ.setdefault("DEV_MODE", "1")

from fastapi.testclient import TestClient

from broombuster import ics

_TZ = "America/Los_Angeles"
_START = datetime.date(2026, 10, 25)


def _events(text):
    """Unfolded VEVENTs as {property: value} dicts (last value wins)."""
    lines = text.replace("\r\n ", "").split("\r\n")
    out, cur = [], None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT":
            out.append(cur)
            cur = None
        elif cur is not None and ":" in ln and not ln.startswith(("BEGIN:VALARM", "END:VALARM")):
            k, v = ln.split(":", 1)
            cur.setdefault(k, v)
    return out


def _build(even, odd, sides=("even", "odd"), labels=("North", "South"), street="Main St"):
    return ics.build_calendar(even, odd, sides, labels, street, _TZ, "seed", _START)


def test_utc_times_follow_dst():
    ev = _events(_build([("ME", "", "8AM-10AM")], []))
    starts = {e["DTSTART"] for e in ev}
    assert "20261026T150000Z" in starts   # Mon Oct 26, PDT (UTC-7)
    assert "20261102T160000Z" in starts   # Mon Nov 2, PST (UTC-8)
    assert all(e["SUMMARY"] == "Street sweeping (North side)" for e in ev)


def test_midnight_end_and_untimed_all_day():
    ev = _events(_build([("ME", "", "10PM-12AM")], [("TE", "", "")]))
    mon = next(e for e in ev if e["DTSTART"] == "20261027T050000Z")
    assert mon["DTEND"] == "20261027T065959Z"
    tue = next(e for e in ev if e.get("DTSTART;VALUE=DATE") == "20261027")
    assert tue["DTEND;VALUE=DATE"] == "20261028"
    assert "time not published" in tue["DESCRIPTION"]


def test_pdf_artifact_time_is_timed():
    ev = _events(_build([("ME", "", "8:00 AM -11 :00 AM")], []))
    assert ev[0]["DTSTART"] == "20261026T150000Z" and ev[0]["DTEND"] == "20261026T180000Z"


def test_side_filter_and_shared_window():
    even, odd = [("ME", "", "8AM-10AM")], [("M13", "", "8AM-10AM"), ("WE", "", "8AM-10AM")]
    both = _events(_build(even, odd))
    nov2 = [e for e in both if e["DTSTART"] == "20261102T160000Z"]
    assert [e["SUMMARY"] for e in nov2] == ["Street sweeping (both sides)"]
    only_even = _events(_build(even, odd, sides=("even",)))
    assert {e["SUMMARY"] for e in only_even} == {"Street sweeping (North side)"}
    assert len({e["UID"] for e in both}) == len(both)


def test_lines_folded_and_text_escaped():
    street = "Very Long Boulevard, Upper Section; Near The Park Entrance With Émigré Café"
    text = _build([("ME", "", "8AM-10AM")], [], street=street)
    assert text.endswith("\r\n")
    assert all(len(ln.encode()) <= 75 for ln in text.split("\r\n"))
    ev = _events(text)[0]
    assert ev["DESCRIPTION"].startswith("Very Long Boulevard\\, Upper Section\\; Near")


def test_endpoint_serves_feed():
    from broombuster.api import app as api_mod
    with TestClient(api_mod.app) as client:
        ok = client.get("/calendar.ics", params={"lat": 37.7599, "lon": -122.4214})
        bad_side = client.get("/calendar.ics", params={"lat": 37.7599, "lon": -122.4214,
                                                       "side": "left"})
        ocean = client.get("/calendar.ics", params={"lat": 37.70, "lon": -122.60,
                                                    "region": "bay_area"})
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"].startswith("text/calendar")
    assert ok.text.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in ok.text
    assert bad_side.status_code == 422
    assert ocean.status_code == 404
