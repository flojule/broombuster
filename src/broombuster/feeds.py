"""Live third-party feeds: street closures, refreshed in a background thread (requests
read the last snapshot); Chicago's towed and relocated vehicles and permit zones by
address, fetched on demand behind a small TTL cache (failures are cached briefly so an
outage is not hammered).

Every feed is best effort: a failure answers "nothing known", never an error.
"""

import concurrent.futures
import datetime
import json
import logging
import math
import threading
import time
from typing import NamedTuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyproj
import requests
import shapely

from broombuster import config, gps, normalize
from broombuster.cities import REGIONS

TIMEOUT_S = 8
_UA = {"User-Agent": "BroomBuster (https://github.com/flojule/broombuster)"}
log = logging.getLogger(__name__)
# Closure sources are fetched side by side, off the request path.
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="feeds")


class TTLCache:
    """fetch() once per ttl per key; a failed fetch (None) is retried after failure_ttl.
    One fetch per key at a time: concurrent callers wait up to wait_s for its result and
    get None when it times out or raises (a raised fetch is not cached).
    Bounded: the oldest expired (else oldest) entries go first."""

    def __init__(self, max_items: int = 512, failure_ttl: float = 300, wait_s: float = 30):
        self._data: dict = {}
        self._fetching: dict = {}  # key -> Event set when its one fetch ends
        self._lock = threading.Lock()
        self.max_items, self.failure_ttl, self.wait_s = max_items, failure_ttl, wait_s

    def peek(self, key, default=None):
        """The fresh cached value of key, else default (never fetches)."""
        with self._lock:
            hit = self._data.get(key)
            return hit[1] if hit and time.time() < hit[0] else default

    def get(self, key, ttl: float, fetch):
        with self._lock:
            hit = self._data.get(key)
            if hit and time.time() < hit[0]:
                return hit[1]
            done = self._fetching.get(key)
            owner = done is None
            if owner:
                done = self._fetching[key] = threading.Event()
        if not owner:
            done.wait(self.wait_s)
            return self.peek(key)
        try:
            value = fetch()
            now = time.time()
            with self._lock:
                if len(self._data) >= self.max_items:
                    stale = [k for k, (exp, _) in self._data.items() if exp <= now]
                    for k in stale or [next(iter(self._data))]:
                        del self._data[k]
                expires = now + (ttl if value is not None else self.failure_ttl)
                self._data[key] = (expires, value)
            return value
        finally:
            with self._lock:
                del self._fetching[key]
            done.set()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


cache = TTLCache(wait_s=2 * TIMEOUT_S)


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
    where = (
        f"end_utc >= '{now:%Y-%m-%dT%H:%M:%S}' AND start_utc <= '{soon:%Y-%m-%dT%H:%M:%S}'"
        " AND type != 'Roadway Shared Spaces'"
    )
    data = get_json(
        "https://data.sf.gov/resource/8x25-yybr.geojson", **{"$where": where, "$limit": 5000}
    )
    if data is None:
        return None
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        out.append(
            {
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": {
                    "title": p.get("case_name") or p.get("type") or "Street closure",
                    "start": (p.get("start_utc") or "")[:19] + "Z",
                    "end": (p.get("end_utc") or "")[:19] + "Z",
                    "street": p.get("loc_desc") or p.get("street") or "",
                    "kind": p.get("type") or "",
                    "status": p.get("status") or "",
                    "source": "DataSF temporary street closures",
                },
            }
        )
    return out


_BAY_TZ = ZoneInfo(REGIONS["bay_area"]["tz"])
_OAKLAND_CLOSURES = (
    "https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/"
    "Latest_Street_Closures_Nov24/FeatureServer/0/query"
)


def _oakland_closures(now: datetime.datetime):
    today = now.astimezone(_BAY_TZ).date()  # its dates are Oakland days
    data = get_json(
        _OAKLAND_CLOSURES,
        where=f"End_date >= DATE '{today:%Y-%m-%d}'",
        outFields="ST_NAME,Start_date,End_date,Event_type,Event_time,Status",
        outSR=4326,
        f="geojson",
    )
    if data is None:
        return None
    soon = now + datetime.timedelta(days=CLOSURE_DAYS)
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        start = p.get("Start_date")
        if isinstance(start, int | float) and start / 1000 > soon.timestamp():
            continue
        out.append(
            {
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": {
                    "title": p.get("Event_type") or "Street closure",
                    "start": _iso_utc(start)[:10],
                    "end": _iso_utc(p.get("End_date"))[:10],
                    "street": str(p.get("ST_NAME") or "").title(),
                    "kind": p.get("Event_time") or "",
                    "status": p.get("Status") or "",
                    "source": "City of Oakland special event closures",
                },
            }
        )
    return out


def _aware(iso: str) -> datetime.datetime:
    """An ISO date-time; one without a zone is Bay Area local time (as _when shows it)."""
    d = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=_BAY_TZ)


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
        core = (f.get("properties") or {}).get("core_details") or {}
        p = f.get("properties") or {}
        start, end = p.get("start_date") or "", p.get("end_date") or ""
        try:
            if end and _aware(end) < now:
                continue
            if start and _aware(start) > soon:
                continue
        except ValueError:
            pass
        out.append(
            {
                "type": "Feature",
                "geometry": f.get("geometry"),
                "properties": {
                    "title": core.get("description") or "Work zone",
                    "start": start,
                    "end": end,
                    "street": ", ".join(core.get("road_names") or []),
                    "kind": "Work zone",
                    "status": "",
                    "source": "511 SF Bay work zones (WZDx)",
                },
            }
        )
    return out


_CLOSURE_SOURCES = {"bay_area": (_sf_closures, _oakland_closures, _511_work_zones), "chicago": ()}


def _safe(src, now: datetime.datetime):
    """src(now), or None (logged) when it raises: one bad feed answers nothing."""
    try:
        return src(now)
    except Exception:
        log.exception("closure source %s failed", src.__name__)
        return None


class _Snapshot(NamedTuple):
    features: list[dict]  # GeoJSON Features, EPSG:4326
    tree: shapely.STRtree  # over their geometries in EPSG:3857, same order
    body: bytes  # the /api/closures FeatureCollection


def _snapshot(features: list[dict]) -> _Snapshot:
    """Parse each feature's geometry once (EPSG:3857); unreadable ones are left out."""
    kept, geoms = [], []
    for f in features:
        try:
            geoms.append(shapely.geometry.shape(f["geometry"]))
        except (KeyError, TypeError, ValueError, AttributeError, shapely.errors.GEOSException):
            continue
        kept.append(f)
    xy = shapely.transform(
        np.array(geoms, dtype=object), lambda c: np.column_stack(_T.transform(*c.T))
    )
    body = json.dumps({"type": "FeatureCollection", "features": kept}).encode()
    return _Snapshot(kept, shapely.STRtree(xy), body)


_T = pyproj.Transformer.from_crs(4326, 3857, always_xy=True)
REFRESH_S = 15 * 60  # closures are fetched again this often
RETRY_S = 5 * 60  # this soon after a source failed
MAX_STALE_S = 6 * 3600  # a failing source's last answer is kept this long
_EMPTY = _snapshot([])
_snapshots: dict[str, _Snapshot] = {}  # region -> the last refresh's closures
_last_good: dict[tuple[str, str], tuple[float, list]] = {}  # (region, source) -> (time, answer)
_running: dict[tuple[str, str], concurrent.futures.Future] = {}  # a source's fetch in progress


def refresh(region_key: str, now: datetime.datetime | None = None) -> bool:
    """Fetch a region's closure sources side by side and publish a new snapshot; False when
    a source failed or did not answer within TIMEOUT_S (its last answer is kept for
    MAX_STALE_S). A source still fetching from an earlier refresh is not started again."""
    now = now or datetime.datetime.now(datetime.UTC)
    srcs = _CLOSURE_SOURCES.get(region_key, ())
    futs = {}
    for src in srcs:
        key = (region_key, src.__name__)
        if key not in _running or _running[key].done():
            _running[key] = _pool.submit(_safe, src, now)
        futs[key] = _running[key]
    done, late = concurrent.futures.wait(futs.values(), timeout=TIMEOUT_S)
    if late:
        log.warning("%d closure source(s) still fetching after %s s", len(late), TIMEOUT_S)
    t, ok = time.time(), not late
    for key, fut in futs.items():
        got = fut.result() if fut in done else None
        if got is not None:
            _last_good[key] = (t, got)
        else:
            ok = False
            if key in _last_good and t - _last_good[key][0] > MAX_STALE_S:
                del _last_good[key]
    features = [
        f
        for src in srcs
        for f in _last_good.get((region_key, src.__name__), (0, []))[1]
        if f.get("geometry")
    ]
    _snapshots[region_key] = _snapshot(features)
    return ok


def _refresh_loop(stop: threading.Event) -> None:
    while True:
        ok = True
        for rk, srcs in _CLOSURE_SOURCES.items():
            if srcs:
                try:
                    ok = refresh(rk) and ok
                except Exception:
                    log.exception("closures refresh of %s failed", rk)
                    ok = False
        if stop.wait(REFRESH_S if ok else RETRY_S):
            return


def start_refresh() -> threading.Event:
    """Refresh every region's closures now and then every REFRESH_S, in a background
    thread; set the returned event to stop it."""
    stop = threading.Event()
    threading.Thread(target=_refresh_loop, args=(stop,), name="closures", daemon=True).start()
    return stop


def closures(region_key: str) -> list[dict]:
    """The last refresh's closures in a region (GeoJSON Features with a geometry) that were
    on or started within CLOSURE_DAYS; empty before the first refresh. Never fetches."""
    return _snapshots.get(region_key, _EMPTY).features


def closures_json(region_key: str) -> bytes:
    """closures() as a GeoJSON FeatureCollection, serialized once per refresh."""
    return _snapshots.get(region_key, _EMPTY).body


def closures_near(region_key: str, lat: float, lon: float, meters: float = 15) -> list[dict]:
    """Rules (as /api/rules gives them) for closures within `meters` of a point."""
    snap = _snapshots.get(region_key, _EMPTY)
    if not snap.features:
        return []
    pt = shapely.Point(_T.transform(lon, lat))
    k = 1.0 / math.cos(math.radians(lat))  # EPSG:3857 units per ground meter
    out, seen = [], set()
    for i in sorted(snap.tree.query(pt, predicate="dwithin", distance=meters * k).tolist()):
        p = snap.features[i]["properties"]
        key = (p["title"], p["start"][:10])
        if key in seen:
            continue
        seen.add(key)
        when = _when(p["start"], p["end"], region_key)
        out.append(
            {
                "theme": "closures",
                "label": f"Street closed: {p['title']}",
                "rule": f"{when}. {p['street']}".strip(" ."),
                "asof": "",
                "city": "",
                "warn": True,
                "source": p["source"],
            }
        )
    return out


def _when(start: str, end: str, region_key: str) -> str:
    """ "Sun Feb 7, 7–10 AM" style text for a closure's span, in the region's time."""
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
        since = (
            datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=90)
        ).isoformat()
        towed = get_json(
            "https://data.cityofchicago.org/resource/ygr5-vcbg.json",
            **{
                "$where": f"plate = '{p}' AND state = '{st}' AND tow_date >= '{since}'",
                "$order": "tow_date DESC",
                "$limit": 5,
            },
        )
        moved = get_json(
            "https://data.cityofchicago.org/resource/5k2z-suxx.json",
            **{
                "$where": f"plate = '{p}' AND state = '{st}' AND relocated_date >= '{since}'",
                "$order": "relocated_date DESC",
                "$limit": 5,
            },
        )
        if towed is None or moved is None:
            return None
        return {
            "towed": [
                {
                    "date": str(t.get("tow_date") or "")[:10],
                    "to": t.get("towed_to_address") or "",
                    "phone": t.get("tow_facility_phone") or "",
                }
                for t in towed
            ],
            "relocated": [
                {
                    "date": str(m.get("relocated_date") or "")[:16],
                    "to": " ".join(
                        str(m.get(k) or "")
                        for k in (
                            "relocated_to_address_number",
                            "relocated_to_direction",
                            "relocated_to_street_name",
                        )
                    ).strip(),
                    "reason": m.get("relocated_reason") or "",
                }
                for m in moved
            ],
        }

    return cache.get(("chicago_vehicle", p, st), 10 * 60, fetch)


# ── Chicago: permit zones by address ──────────────────────────────────────────
_zones: pd.DataFrame | None = None


def _permit_zones() -> pd.DataFrame:
    global _zones
    if _zones is None:
        from broombuster import layers

        path = layers.city_dir("chicago_all") / "permit_zones.csv"
        _zones = (
            pd.read_csv(path, dtype={"zone": str})
            if path.exists()
            else pd.DataFrame(columns=["street", "low", "high", "side", "zone"])
        )
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
    z = _permit_zones()
    side = "E" if number % 2 == 0 else "O"

    def rows(street: str) -> pd.DataFrame:
        return z[
            (z["street"] == street)
            & (z["low"] <= number)
            & (z["high"] >= number)
            & z["side"].isin((side, "B"))
        ]

    hit = rows(normalize.street_name_typed(addr["road"]))
    if hit.empty:  # rows without a street type ("NORTH KILDARE")
        hit = rows(normalize.street_name(addr["road"]))
    return [
        {
            "theme": "parking",
            "label": f"Permit zone {zone}",
            "rule": "Residential permit parking: without zone permit, follow the posted hours.",
            "asof": "",
            "city": "chicago_all",
        }
        for zone in sorted(set(hit["zone"]))
    ]
