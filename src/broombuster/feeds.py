"""Live third-party feeds, fetched on demand behind a small TTL cache (failures are
cached briefly so an outage is not hammered): street closures, Chicago's towed and
relocated vehicles, and Chicago's permit zones by address.

Every feed is best effort: a failure answers "nothing known", never an error.
"""

import datetime
import threading
import time

import pandas as pd
import requests
import shapely

from broombuster import config, gps, normalize
from broombuster.cities import REGIONS

TIMEOUT_S = 8
_UA = {"User-Agent": "BroomBuster (https://github.com/flojule/broombuster)"}


class TTLCache:
    """fetch() once per ttl per key; a failed fetch (None) is retried after failure_ttl.
    Bounded: the oldest expired (else oldest) entries go first."""

    def __init__(self, max_items: int = 512, failure_ttl: float = 300):
        self._data: dict = {}
        self._lock = threading.Lock()
        self.max_items, self.failure_ttl = max_items, failure_ttl

    def get(self, key, ttl: float, fetch):
        now = time.time()
        with self._lock:
            hit = self._data.get(key)
            if hit and now < hit[0]:
                return hit[1]
        value = fetch()
        with self._lock:
            if len(self._data) >= self.max_items:
                stale = [k for k, (exp, _) in self._data.items() if exp <= now]
                for k in stale or [next(iter(self._data))]:
                    del self._data[k]
            self._data[key] = (now + (ttl if value is not None else self.failure_ttl), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


cache = TTLCache()


def get_json(url: str, **params):
    """GET url -> parsed JSON, or None on any failure."""
    try:
        r = requests.get(url, params=params, headers=_UA, timeout=TIMEOUT_S)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def _iso_utc(ms_or_iso) -> str:
    """An ArcGIS epoch-ms or ISO timestamp -> ISO date-time ("" when unreadable)."""
    if isinstance(ms_or_iso, int | float):
        return datetime.datetime.fromtimestamp(ms_or_iso / 1000, datetime.UTC).isoformat()
    return str(ms_or_iso or "")


# ── Street closures ───────────────────────────────────────────────────────────
# Each closure: a GeoJSON Feature with properties {title, start, end, street, kind,
# source}; start/end are ISO date-times (UTC when they end in +00:00 / Z, else local).
CLOSURE_DAYS = 14  # closures starting within this many days


def _sf_closures(now: datetime.datetime):
    soon = now + datetime.timedelta(days=CLOSURE_DAYS)
    where = (f"end_utc >= '{now:%Y-%m-%dT%H:%M:%S}' AND start_utc <= '{soon:%Y-%m-%dT%H:%M:%S}'"
             " AND type != 'Roadway Shared Spaces'")
    data = get_json("https://data.sf.gov/resource/8x25-yybr.geojson",
                    **{"$where": where, "$limit": 5000})
    if data is None:
        return None
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        out.append({"type": "Feature", "geometry": f.get("geometry"), "properties": {
            "title": p.get("case_name") or p.get("type") or "Street closure",
            "start": (p.get("start_utc") or "")[:19] + "Z",
            "end": (p.get("end_utc") or "")[:19] + "Z",
            "street": p.get("loc_desc") or p.get("street") or "", "kind": p.get("type") or "",
            "status": p.get("status") or "", "source": "DataSF temporary street closures"}})
    return out


_OAKLAND_CLOSURES = ("https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/"
                     "Latest_Street_Closures_Nov24/FeatureServer/0/query")


def _oakland_closures(now: datetime.datetime):
    data = get_json(_OAKLAND_CLOSURES, where=f"End_date >= DATE '{now:%Y-%m-%d}'",
                 outFields="ST_NAME,Start_date,End_date,Event_type,Event_time,Status",
                 outSR=4326, f="geojson")
    if data is None:
        return None
    soon = now + datetime.timedelta(days=CLOSURE_DAYS)
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        start = p.get("Start_date")
        if isinstance(start, int | float) and start / 1000 > soon.timestamp():
            continue
        out.append({"type": "Feature", "geometry": f.get("geometry"), "properties": {
            "title": p.get("Event_type") or "Street closure",
            "start": _iso_utc(start)[:10], "end": _iso_utc(p.get("End_date"))[:10],
            "street": str(p.get("ST_NAME") or "").title(), "kind": p.get("Event_time") or "",
            "status": p.get("Status") or "", "source": "City of Oakland special event closures"}})
    return out


def _511_work_zones(now: datetime.datetime):
    """511 SF Bay WZDx work zones on local streets (needs BB_511_API_KEY)."""
    key = config.api_511_key()
    if not key:
        return []
    data = get_json("https://api.511.org/traffic/wzdx", api_key=key)
    if data is None:
        return None
    soon = now + datetime.timedelta(days=CLOSURE_DAYS)
    out = []
    for f in data.get("features") or []:
        core = ((f.get("properties") or {}).get("core_details") or {})
        p = f.get("properties") or {}
        start, end = p.get("start_date") or "", p.get("end_date") or ""
        try:
            if end and datetime.datetime.fromisoformat(end.replace("Z", "+00:00")) < now:
                continue
            if start and datetime.datetime.fromisoformat(start.replace("Z", "+00:00")) > soon:
                continue
        except ValueError:
            pass
        out.append({"type": "Feature", "geometry": f.get("geometry"), "properties": {
            "title": core.get("description") or "Work zone", "start": start, "end": end,
            "street": ", ".join(core.get("road_names") or []), "kind": "Work zone",
            "status": "", "source": "511 SF Bay work zones (WZDx)"}})
    return out


_CLOSURE_SOURCES = {"bay_area": (_sf_closures, _oakland_closures, _511_work_zones), "chicago": ()}


def closures(region_key: str, now: datetime.datetime | None = None) -> list[dict]:
    """Closures in a region that are on now or start within CLOSURE_DAYS (GeoJSON
    Features with a geometry). Each source is cached 15 minutes."""
    now = now or datetime.datetime.now(datetime.UTC)
    out = []
    for src in _CLOSURE_SOURCES.get(region_key, ()):
        got = cache.get(("closures", src.__name__, now.strftime("%Y%m%d%H")), 15 * 60,
                        lambda src=src: src(now))
        out += [f for f in got or [] if f.get("geometry")]
    return out


def closures_near(region_key: str, lat: float, lon: float, meters: float = 15) -> list[dict]:
    """Rules (as /api/rules gives them) for closures within `meters` of a point."""
    pt = shapely.Point(lon, lat)
    deg = meters / 111_000
    out, seen = [], set()
    for f in closures(region_key):
        try:
            geom = shapely.geometry.shape(f["geometry"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if geom.distance(pt) > deg:
            continue
        p = f["properties"]
        key = (p["title"], p["start"][:10])
        if key in seen:
            continue
        seen.add(key)
        when = _when(p["start"], p["end"], region_key)
        out.append({"theme": "closures", "label": f"Street closed: {p['title']}",
                    "rule": f"{when}. {p['street']}".strip(" ."), "asof": "", "city": "",
                    "warn": True, "source": p["source"]})
    return out


def _when(start: str, end: str, region_key: str) -> str:
    """"Sun Feb 7, 7–10 AM" style text for a closure's span, in the region's time."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(REGIONS[region_key]["tz"])

    def parse(s):
        if not s:
            return None
        try:
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d.astimezone(tz) if d.tzinfo else d

    a, b = parse(start), parse(end)
    if a is None:
        return ""
    day = f"{a:%a %b} {a.day}"
    if len(start) <= 10:  # dates only
        return day if b is None or b.date() == a.date() else f"{day} to {b:%a %b} {b.day}"
    def hm(d):
        minutes = f":{d.minute:02d}" if d.minute else ""
        return f"{d.hour % 12 or 12}{minutes} {'AM' if d.hour < 12 else 'PM'}"

    if b is None:
        return f"{day}, from {hm(a)}"
    if b.date() == a.date():
        return f"{day}, {hm(a)}–{hm(b)}"
    return f"{day} {hm(a)} to {b:%a %b} {b.day} {hm(b)}"


# ── Chicago: towed and relocated vehicles ─────────────────────────────────────

def _plate(plate: str) -> str:
    return "".join(ch for ch in plate.upper() if ch.isalnum())


def chicago_vehicle(plate: str, state: str = "IL") -> dict | None:
    """Recent tows and relocations of a plate in Chicago (last 90 days), or None when
    the portal could not answer. The plate is only kept in this short cache."""
    p = _plate(plate)
    st = (state or "IL").upper()[:2]
    if not (len(st) == 2 and st.isalpha()):
        st = "IL"
    if not p:
        return {"towed": [], "relocated": []}

    def fetch():
        since = (datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=90)).isoformat()
        towed = get_json("https://data.cityofchicago.org/resource/ygr5-vcbg.json",
                      **{"$where": f"plate = '{p}' AND state = '{st}' AND tow_date >= '{since}'",
                         "$order": "tow_date DESC", "$limit": 5})
        moved = get_json("https://data.cityofchicago.org/resource/5k2z-suxx.json",
                      **{"$where": f"plate = '{p}' AND state = '{st}'"
                                   f" AND relocated_date >= '{since}'",
                         "$order": "relocated_date DESC", "$limit": 5})
        if towed is None or moved is None:
            return None
        return {
            "towed": [{"date": str(t.get("tow_date") or "")[:10],
                       "to": t.get("towed_to_address") or "",
                       "phone": t.get("tow_facility_phone") or ""} for t in towed],
            "relocated": [{"date": str(m.get("relocated_date") or "")[:16],
                           "to": " ".join(str(m.get(k) or "") for k in (
                               "relocated_to_address_number", "relocated_to_direction",
                               "relocated_to_street_name")).strip(),
                           "reason": m.get("relocated_reason") or ""} for m in moved],
        }

    return cache.get(("chicago_vehicle", p, st), 10 * 60, fetch)


# ── Chicago: permit zones by address ──────────────────────────────────────────
_zones: pd.DataFrame | None = None


def _permit_zones() -> pd.DataFrame:
    global _zones
    if _zones is None:
        from broombuster import layers

        path = layers.city_dir("chicago_all") / "permit_zones.csv"
        _zones = (pd.read_csv(path, dtype={"zone": str}) if path.exists()
                  else pd.DataFrame(columns=["street", "low", "high", "side", "zone"]))
    return _zones


def chicago_permit_zone(lat: float, lon: float, network: bool = True) -> list[dict]:
    """The permit zone at a Chicago point, by its address (Nominatim, cached), as rules.
    network=False: only when the address is already cached."""
    addr = gps.address_parts(lat, lon, network)
    if not addr or not addr.get("house_number") or not addr.get("road"):
        return []
    number = normalize.house_number(addr["house_number"])
    if number is None:
        return []
    street = normalize.street_name(addr["road"])
    z = _permit_zones()
    side = "E" if number % 2 == 0 else "O"
    hit = z[(z["street"] == street) & (z["low"] <= number) & (z["high"] >= number)
            & z["side"].isin((side, "B"))]
    return [{"theme": "parking", "label": f"Permit zone {zone}",
             "rule": "Residential permit parking: without zone permit, follow the posted hours.",
             "asof": "", "city": "chicago_all"} for zone in sorted(set(hit["zone"]))]
