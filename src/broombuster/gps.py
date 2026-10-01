import threading
from collections import OrderedDict
from functools import lru_cache

import pyproj
from geopy.geocoders import Nominatim
from shapely.geometry import Point as _Point

from broombuster import normalize as _normalize

_TRANSFORMER = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_GEOLOCATOR = Nominatim(user_agent="broombuster")


class GeocodeError(Exception):
    """Nominatim could not be reached; the lookup is not cached."""


def _reverse_geocode(lat: float, lon: float):
    """(road, house_number) from Nominatim; raises GeocodeError on failure."""
    try:
        location = _GEOLOCATOR.reverse((lat, lon), exactly_one=True)
    except Exception as exc:
        raise GeocodeError(str(exc)) from exc
    if location is None:
        return None, None
    myStreetName = location.raw['address'].get('road')  # type: ignore[union-attr]
    raw_num = location.raw['address'].get('house_number')  # type: ignore[union-attr]
    myNumber = _normalize.house_number(raw_num) if raw_num else None
    return myStreetName, myNumber


# (lat, lon) rounded to 4 decimals (~11 m) -> (road, house_number). Successful
# answers only, so a Nominatim outage is retried rather than remembered.
_GEOCODE_CACHE_MAX = 1024
_geocode_cache: OrderedDict = OrderedDict()
_geocode_lock = threading.Lock()


def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 4), round(lon, 4)


def house_number_cached(lat: float, lon: float) -> bool:
    """True when maybe_house_number(lat, lon, ...) will answer without a network call."""
    with _geocode_lock:
        return _cache_key(lat, lon) in _geocode_cache


def clear_cache() -> None:
    """Drop cached reverse-geocode answers (tests)."""
    with _geocode_lock:
        _geocode_cache.clear()
    _reverse_address.cache_clear()


def _road_and_number(lat: float, lon: float, network: bool):
    key = _cache_key(lat, lon)
    with _geocode_lock:
        if key in _geocode_cache:
            _geocode_cache.move_to_end(key)
            return _geocode_cache[key]
    if not network:
        return None, None
    try:
        hit = _reverse_geocode(*key)
    except GeocodeError:
        return None, None
    with _geocode_lock:
        _geocode_cache[key] = hit
        while len(_geocode_cache) > _GEOCODE_CACHE_MAX:
            _geocode_cache.popitem(last=False)
    return hit


def reverse_address(lat: float, lon: float) -> str | None:
    """Human-readable street address for a coordinate, or None.

    Backend reverse geocode for home pins dropped by map tap / right-click, where
    the frontend supplies no address string. Returns "<number> <road>, <city>"
    when available, falling back to road, then the full display name.
    """
    try:
        return _reverse_address(round(lat, 5), round(lon, 5))
    except GeocodeError:
        return None


@lru_cache(maxsize=1024)
def _reverse_address(lat: float, lon: float) -> str | None:
    # Raises GeocodeError on failure; lru_cache does not cache exceptions.
    try:
        location = _GEOLOCATOR.reverse((lat, lon), exactly_one=True, timeout=5)
    except Exception as exc:
        raise GeocodeError(str(exc)) from exc
    if location is None:
        return None
    addr = location.raw.get('address', {})  # type: ignore[union-attr]
    street = ' '.join(p for p in (addr.get('house_number'), addr.get('road')) if p)
    city = (addr.get('city') or addr.get('town') or addr.get('village')
            or addr.get('suburb'))
    parts = [p for p in (street, city) if p]
    if parts:
        return ', '.join(parts)
    return location.address or None  # type: ignore[union-attr]


def maybe_house_number(lat: float, lon: float, expected_street: str,
                       *, network: bool = True) -> int | None:
    """Return a Nominatim house number ONLY when the geocoded road matches
    `expected_street` under street_name() canonicalisation.

    This is the gate that prevents corner-case bleed: if the resolver chose
    "Grand Ave" but Nominatim returns "5th St" for the same coordinates,
    the house number from "5th St" is dropped.

    network=False answers from the cache only (None on a miss), so callers on
    the latency-sensitive path never wait on Nominatim.

    Returns None on any failure or mismatch — never raises.
    """
    if not expected_street:
        return None
    road, number = _road_and_number(lat, lon, network)
    if number is None or not road:
        return None
    if _normalize.street_name(road) != _normalize.street_name(expected_street):
        return None
    return number


def get_nearby_streets_from_gdf(lat: float, lon: float, gdf_3857) -> list:
    """Return [(street_name, distance_m), ...] using the in-memory GDF spatial index.

    Replaces the Overpass API call entirely — no network request, uses the
    already-loaded street data with a spatial index query for speed.
    """
    x, y = _TRANSFORMER.transform(lon, lat)
    pt = _Point(x, y)

    # Candidates within 250 m bounding box
    candidates = gdf_3857.sindex.query(pt.buffer(250))
    # Use STREET_KEY for uniqueness/matching, but return STREET_DISPLAY (or STREET_NAME) for UI
    best: dict = {}  # street_key -> (display_name, distance)
    for i in candidates:
        row = gdf_3857.iloc[i]
        # Prefer explicit STREET_KEY / STREET_DISPLAY columns when present
        key = row.get("STREET_KEY") or None
        display = row.get("STREET_DISPLAY") or row.get("STREET_NAME")
        if not isinstance(display, str) or display.strip().upper() in ("", "NAN", "NONE"):
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        d = pt.distance(geom)
        # If no explicit key, derive one for uniqueness
        if not key:
            key = _normalize.street_name(display)
        cur = best.get(key)
        if cur is None or d < cur[1]:
            best[key] = (display, d)

    # Return list of (display, distance) sorted by distance
    return sorted(((v[0], v[1]) for v in best.values()), key=lambda kv: kv[1])


