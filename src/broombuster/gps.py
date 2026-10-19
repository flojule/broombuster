"""Geocoding (Nominatim), cached and throttled to its 1 request/s policy.

Both directions go through here, so the browser never calls Nominatim itself: the
policy's limit holds for the whole app, and a typed address stays on our server.
"""

import threading
from collections import OrderedDict

from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from broombuster import normalize

_geocoder = Nominatim(user_agent="broombuster", timeout=5)
# One throttle for every call (reverse and search share Nominatim's 1 request/s).
_call = RateLimiter(lambda fn, *a, **kw: fn(*a, **kw),
                    min_delay_seconds=1, max_retries=0, swallow_exceptions=False)

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


def _nominatim(lat: float, lon: float) -> dict:
    """Nominatim's address dict for a point; raises on network failure."""
    location = _call(_geocoder.reverse, (lat, lon), exactly_one=True)
    return location.raw.get("address", {}) if location else {}


def _nominatim_search(query: str, bbox: tuple | None) -> dict:
    """{"lat", "lon"} of the best match inside bbox, {} if none; raises on network failure."""
    viewbox = [(bbox[0], bbox[1]), (bbox[2], bbox[3])] if bbox else None
    location = _call(_geocoder.geocode, query, exactly_one=True,
                     viewbox=viewbox, bounded=bool(bbox))
    return {"lat": location.latitude, "lon": location.longitude} if location else {}


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
    """"<number> <road>, <city>" for a point, or None."""
    addr = _lookup(lat, lon)
    if not addr:
        return None
    street = " ".join(p for p in (addr.get("house_number"), addr.get("road")) if p)
    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb")
    return ", ".join(p for p in (street, city) if p) or None


def maybe_house_number(lat: float, lon: float, expected_street: str,
                       *, network: bool = True) -> int | None:
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
