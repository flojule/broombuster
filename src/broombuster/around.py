"""What's on near a point in the coming days (the Nearby tab): weekly farmers markets
(data/places/markets.yaml), events from official feeds (DataSF Our415, Chicago Public
Library), street events (the closure feeds) and council meetings (Legistar), plus
links to the calendars that have no feed BroomBuster may reuse.

Everything is best effort and cached (feeds.cache); a failing source is left out."""

import datetime
import functools
import math
import os
from zoneinfo import ZoneInfo

import shapely
import yaml

from broombuster import feeds
from broombuster.cities import CITIES, REGIONS, city_for_point
from broombuster.config import REPO_ROOT

DAYS = 7
NEAR_KM = 3.0

# Legistar clients (webapi.legistar.com) by city; the meetings' public page.
LEGISTAR = {"oakland": "https://oakland.legistar.com/Calendar.aspx",
            "alameda": "https://alameda.legistar.com/Calendar.aspx"}

# Calendars without a feed we may reuse: shown as links only.
LINKS = {
    "berkeley": [("Berkeleyside events", "https://www.berkeleyside.org/events"),
                 ("Berkeley Public Library events", "https://www.berkeleypubliclibrary.org/events"),
                 ("City Council agendas", "https://berkeleyca.gov/your-government/city-council/city-council-agendas")],
    "oakland": [("Oaklandside events", "https://oaklandside.org/events/"),
                ("Oakland Public Library events", "https://oaklandlibrary.bibliocommons.com/events")],
    "alameda": [("Alameda Free Library events", "https://www.alamedafree.org/events")],
    "san_francisco": [("Board of Supervisors meetings", "https://sfgov.legistar.com/Calendar.aspx"),
                      ("SF Public Library events", "https://sfpl.org/events")],
    "chicago_all": [("City Council meetings", "https://chicityclerkelms.chicago.gov/Meetings/"),
                    ("Chicago Park District events", "https://www.chicagoparkdistrict.com/events")],
}


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


@functools.cache
def _markets() -> list[dict]:
    with open(os.path.join(REPO_ROOT, "data", "places", "markets.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)["markets"]


def markets(lat: float, lon: float, today: datetime.date) -> list[dict]:
    """Markets within NEAR_KM, each on its next day in [today, today + DAYS)."""
    out = []
    for m in _markets():
        km = _km(lat, lon, m["lat"], m["lon"])
        if km > NEAR_KM:
            continue
        day = today + datetime.timedelta(days=(m["weekday"] - today.weekday()) % 7)
        out.append({"kind": "market", "title": m["name"], "date": day.isoformat(),
                    "time": m["hours"], "place": "", "km": round(km, 1), "url": m.get("url", "")})
    return out


def _sf_events(today: datetime.date):
    end = today + datetime.timedelta(days=DAYS)
    rows = feeds.get_json("https://data.sf.gov/resource/8i3s-ih2a.json", **{
        "$where": f"event_start_date >= '{today}' AND event_start_date < '{end}'",
        "$order": "event_start_date", "$limit": 2000})
    if rows is None:
        return None
    out = []
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        start = str(r.get("start_time") or "")[:5]
        out.append({"kind": "event", "title": r.get("event_name") or "Event",
                    "date": str(r.get("event_start_date") or "")[:10], "start": _minutes(start),
                    "time": _hhmm(start), "place": r.get("site_location_name") or "",
                    "lat": lat, "lon": lon, "url": _url(r.get("more_info")),
                    "source": r.get("org_name") or "DataSF Our415"})
    return out


def _chicago_library(today: datetime.date):
    end = today + datetime.timedelta(days=DAYS)
    rows = feeds.get_json("https://data.cityofchicago.org/resource/vsdy-d8k7.json", **{
        "$where": f"start >= '{today}' AND start < '{end}' AND cancelled = false",
        "$order": "start", "$limit": 3000})
    branches = feeds.get_json("https://data.cityofchicago.org/resource/x8fc-8rcq.json",
                              **{"$limit": 500})
    if rows is None or branches is None:
        return None
    where = {}  # branch name -> (lat, lon)
    for b in branches:
        try:
            where[b["branch_"].strip().lower()] = (float(b["location"]["latitude"]),
                                                   float(b["location"]["longitude"]))
        except (KeyError, TypeError, ValueError):
            pass
    out = []
    for r in rows:
        pos = where.get(str(r.get("location_name") or "").strip().lower())
        if pos is None:  # "All CPL Locations", online, off-site: no place to be near
            continue
        out.append({"kind": "event", "title": r.get("title") or "Library event",
                    "date": str(r.get("start") or "")[:10],
                    "start": _minutes(str(r.get("start") or "")[11:16]),
                    "time": _hhmm(str(r.get("start") or "")[11:16]),
                    "place": f"{r.get('location_name')} Library", "lat": pos[0], "lon": pos[1],
                    "url": _url(r.get("event_page")), "source": "Chicago Public Library"})
    return out


def _legistar(client: str, today: datetime.date):
    end = today + datetime.timedelta(days=DAYS * 2)
    rows = feeds.get_json(f"https://webapi.legistar.com/v1/{client}/events", **{
        "$filter": f"EventDate ge datetime'{today}' and EventDate lt datetime'{end}'",
        "$orderby": "EventDate"})
    if rows is None:
        return None
    return [{"kind": "meeting", "title": r.get("EventBodyName", "").lstrip("* ") or "Meeting",
             "date": str(r.get("EventDate") or "")[:10], "time": r.get("EventTime") or "",
             "place": (r.get("EventLocation") or "").strip(),
             "url": r.get("EventInSiteURL") or LEGISTAR[client],
             "source": f"{CITIES[client]['name'].split(',')[0]} Legistar"}
            for r in rows if r.get("EventAgendaStatusName", "").lower() != "cancelled"]


def _street_events(region_key: str, lat: float, lon: float, today: datetime.date) -> list[dict]:
    """Event closures (street fairs, festivals) near the point, from the closure feeds."""
    out, seen = [], set()
    for f in feeds.closures(region_key):
        p = f["properties"]
        if "event" not in (p.get("kind", "") + p.get("source", "")).lower():
            continue
        try:
            c = shapely.geometry.shape(f["geometry"]).representative_point()
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        km = _km(lat, lon, c.y, c.x)
        day = p["start"][:10]
        last = (today + datetime.timedelta(days=DAYS)).isoformat()
        if km > NEAR_KM or (p["title"], day) in seen or day > last:
            continue
        seen.add((p["title"], day))
        out.append({"kind": "street", "title": p["title"], "date": max(day, today.isoformat()),
                    "time": "", "place": p["street"], "km": round(km, 1), "url": "",
                    "source": p["source"]})
    return out


def _minutes(s: str) -> int | None:
    """"14:30" -> 870 (None when unreadable)."""
    try:
        h, m = (int(x) for x in s.split(":")[:2])
    except ValueError:
        return None
    return h * 60 + m


def _hhmm(s: str) -> str:
    try:
        h, m = (int(x) for x in s.split(":")[:2])
    except ValueError:
        return ""
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''} {'AM' if h < 12 else 'PM'}"


def _url(v) -> str:
    if isinstance(v, dict):
        v = v.get("url")
    v = str(v or "").strip()
    if v and not v.startswith(("http://", "https://")):
        v = "https://" + v
    return v if v.startswith(("https://", "http://")) else ""


def around(region_key: str, lat: float, lon: float) -> dict:
    """{city, items: [...] sorted by date, links: [{title, url}]} near a point."""
    tz = ZoneInfo(REGIONS[region_key]["tz"])
    today = datetime.datetime.now(tz).date()
    city = city_for_point(lat, lon, region_key)
    items = markets(lat, lon, today) + _street_events(region_key, lat, lon, today)

    def near(rows):
        """Rows with a position: those within NEAR_KM, with their distance."""
        out = []
        for r in [dict(r) for r in rows or []]:  # the cached rows stay untouched
            if "lat" in r:
                km = _km(lat, lon, r.pop("lat"), r.pop("lon"))
                if km > NEAR_KM:
                    continue
                r["km"] = round(km, 1)
            out.append(r)
        return out

    key = today.isoformat()
    if city == "san_francisco":
        items += near(feeds.cache.get(("sf_events", key), 3600, lambda: _sf_events(today)))
    if city == "chicago_all":
        items += near(feeds.cache.get(("cpl", key), 3600, lambda: _chicago_library(today)))
    if city in LEGISTAR:
        items += feeds.cache.get(("legistar", city, key), 6 * 3600,
                                 lambda: _legistar(city, today)) or []
    # Today's events that began over an hour ago are over or nearly so.
    now = datetime.datetime.now(tz)
    late = now.hour * 60 + now.minute - 60
    items = [r for r in items if not (r["date"] == today.isoformat()
                                      and r.get("start") is not None and r["start"] < late)]
    for r in items:
        r.pop("start", None)
    items.sort(key=lambda r: (r["date"], r.get("km", 99), r["title"]))
    return {"city": city, "items": items[:60],
            "links": [{"title": t, "url": u} for t, u in LINKS.get(city, [])]}
