"""Geocoding (Nominatim), cached and throttled to its 1 request/s policy.

Both directions go through here, so the browser never calls Nominatim itself: the
policy's limit holds for the whole app, and a typed address stays on our server.
"""

import re
import threading
import time
from collections import OrderedDict

from broombuster import net, normalize

_NOMINATIM = "https://nominatim.openstreetmap.org"
_TIMEOUT_S = 5
_MIN_DELAY_S = 1.0  # Nominatim's policy: 1 request/s, for reverse and search together
_throttle = threading.Lock()
_last_call = 0.0

# Lookup key -> answer, for both directions. Only answers are cached, so an
# outage is retried.
_CACHE_MAX = 1024
# Callers queue behind the 1 req/s throttle while holding a worker thread, so
# only a few may wait; the rest skip the lookup instead.
_slots = threading.BoundedSemaphore(3)
_cache: OrderedDict = OrderedDict()
_lock = threading.Lock()


def _key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 4), round(lon, 4)  # ~11 m


def _cached(key, fetch, network: bool = True):
    """Cached answer for `key`, else fetch() (None: cache miss with network=False,
    the throttle is backed up, or the geocoder failed)."""
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if not network or not _slots.acquire(blocking=False):
        return None
    try:
        answer = fetch()
    except Exception:
        return None  # any geocoder failure means "no answer"
    finally:
        _slots.release()
    with _lock:
        _cache[key] = answer
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return answer


def _get(path: str, **params):
    """Nominatim's JSON answer, at most one call per _MIN_DELAY_S; raises on failure."""
    global _last_call
    with _throttle:  # callers wait their turn here (at most _slots of them)
        time.sleep(max(0.0, _last_call + _MIN_DELAY_S - time.monotonic()))
        _last_call = time.monotonic()
    data = net.get_json(f"{_NOMINATIM}/{path}", _TIMEOUT_S, format="json", **params)
    if data is None:
        raise ConnectionError(f"Nominatim {path} did not answer")
    return data


def _nominatim(lat: float, lon: float) -> dict:
    """Nominatim's address dict for a point; raises on network failure."""
    data = _get("reverse", lat=lat, lon=lon, addressdetails=1)
    return (data.get("address") or {}) if isinstance(data, dict) else {}


def _nominatim_search(query: str, bbox: tuple | None) -> dict:
    """{"lat", "lon"} of the best match inside bbox, {} if none; raises on network failure."""
    box = {"viewbox": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}", "bounded": 1} if bbox else {}
    hits = _get("search", q=query, limit=1, **box)
    if not isinstance(hits, list) or not hits:
        return {}
    return {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"])}


def _lookup(lat: float, lon: float, network: bool = True) -> dict | None:
    key = _key(lat, lon)
    return _cached(key, lambda: _nominatim(*key), network)


def search(query: str, bbox: tuple | None = None) -> dict | None:
    """{"lat", "lon"} for a typed address, {} when not found, None when the geocoder
    could not answer. bbox = (lat_min, lon_min, lat_max, lon_max) bounds the search."""
    q = " ".join(query.split())
    return _cached(("search", q.lower(), bbox), lambda: _nominatim_search(q, bbox))


def address_parts(lat: float, lon: float, network: bool = True) -> dict | None:
    """Nominatim's address dict for a point (house_number, road, city, …), or None.
    network=False answers from the cache only."""
    return _lookup(lat, lon, network) or None


def house_number_cached(lat: float, lon: float) -> bool:
    with _lock:
        return _key(lat, lon) in _cache


def reverse_address(lat: float, lon: float) -> str | None:
    """ "<number> <road>, <city>" for a point, or None."""
    addr = _lookup(lat, lon)
    if not addr:
        return None
    hn = re.split(r"[;,]", addr.get("house_number") or "")[0].strip()  # "2412;2414": 2412
    street = " ".join(str(p) for p in (hn, addr.get("road")) if p)
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb")
    return ", ".join(p for p in (street, city) if p) or None


def maybe_house_number(
    lat: float, lon: float, expected_street: str, *, network: bool = True
) -> int | None:
    """Nominatim house number, only when its road is `expected_street`.

    Guards corners: if the resolver chose "Grand Ave" but Nominatim answers
    "5th St", the number is dropped. network=False answers from the cache only.
    """
    addr = _lookup(lat, lon, network) if expected_street else None
    if not addr or not addr.get("house_number") or not addr.get("road"):
        return None
    if normalize.street_name(addr["road"]) != normalize.street_name(expected_street):
        return None
    return normalize.house_number(addr["house_number"])
