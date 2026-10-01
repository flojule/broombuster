"""Trash pickup days from ReCollect (recollect.net), for cities with a `trash:`
block in their manifest (area + service_id of the city's ReCollect widget).

Unofficial API, best effort: any failure yields no pickups. Answers are cached
(failures briefly) so a home re-check never hammers the service.
"""

import datetime
import threading
import time

import requests

from broombuster.cities import CITIES

_API = "https://api.recollect.net/api"
_TIMEOUT_S = 3
_PLACE_TTL_S = 30 * 24 * 3600
_PICKUPS_TTL_S = 12 * 3600
_FAILURE_TTL_S = 5 * 60

_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: tuple, ttl: float, fetch):
    """fetch() once per ttl; a failed fetch (None) is retried after _FAILURE_TTL_S."""
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now < hit[0]:
            return hit[1]
    value = fetch()
    with _lock:
        _cache[key] = (now + (ttl if value is not None else _FAILURE_TTL_S), value)
    return value


def _get(path: str, **params):
    try:
        r = requests.get(f"{_API}/{path}", params={**params, "locale": "en-US"},
                         timeout=_TIMEOUT_S)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def parse_pickups(events, today: datetime.date) -> dict[str, list[str]]:
    """ReCollect events -> {stream label: sorted ISO dates from today on}."""
    out: dict[str, set[str]] = {}
    for e in events or []:
        try:
            day = datetime.date.fromisoformat(e.get("day"))
        except (TypeError, ValueError):
            continue
        if day < today:
            continue
        for flag in e.get("flags") or []:
            label = flag.get("subject") or flag.get("name")
            if flag.get("event_type") == "pickup" and label:
                out.setdefault(str(label), set()).add(day.isoformat())
    return {k: sorted(v) for k, v in out.items()}


def pickups(city_key: str, address: str, today: datetime.date) -> dict[str, list[str]]:
    """{stream: [ISO dates]} for the next three weeks at an address, or {}."""
    cfg = CITIES[city_key].get("trash")
    if not cfg or not address.strip():
        return {}
    area, service = cfg["area"], cfg["service_id"]

    def place():
        hits = _get(f"areas/{area}/services/{service}/address-suggest", q=address)
        return hits[0].get("place_id") if isinstance(hits, list) and hits else None

    place_id = _cached(("place", area, " ".join(address.lower().split())), _PLACE_TTL_S, place)
    if not place_id:
        return {}

    def events():
        data = _get(f"places/{place_id}/services/{service}/events",
                    nomerge=1, hide="reminder_only", after=today.isoformat(),
                    before=(today + datetime.timedelta(days=21)).isoformat())
        if data is None:
            return None
        return parse_pickups(data.get("events", []) if isinstance(data, dict) else data, today)

    return _cached(("events", place_id, today.isoformat()), _PICKUPS_TTL_S, events) or {}
