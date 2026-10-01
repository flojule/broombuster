"""Reverse geocoding (Nominatim), cached and throttled to its 1 request/s policy."""

import threading
from collections import OrderedDict

from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from broombuster import normalize

_reverse_call = RateLimiter(Nominatim(user_agent="broombuster", timeout=5).reverse,
                            min_delay_seconds=1, max_retries=0, swallow_exceptions=False)

# (lat, lon) rounded to 4 decimals (~11 m) -> Nominatim address dict. Only
# answers are cached, so an outage is retried.
_CACHE_MAX = 1024
_cache: OrderedDict = OrderedDict()
_lock = threading.Lock()


def _key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 4), round(lon, 4)


def _nominatim(lat: float, lon: float) -> dict:
    """Nominatim's address dict for a point; raises on network failure."""
    location = _reverse_call((lat, lon), exactly_one=True)
    return location.raw.get("address", {}) if location else {}


def _lookup(lat: float, lon: float, network: bool = True) -> dict | None:
    """Cached address dict, or None (cache miss with network=False, or failure)."""
    key = _key(lat, lon)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if not network:
        return None
    try:
        addr = _nominatim(*key)
    except Exception:  # noqa: BLE001 — any geocoder failure means "no address"
        return None
    with _lock:
        _cache[key] = addr
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return addr


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
