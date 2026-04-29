from functools import lru_cache

import numpy as np
import pyproj
import requests
from geopy.geocoders import Nominatim
from shapely.geometry import Point as _Point

import normalize as _normalize

_TRANSFORMER = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_GEOLOCATOR = Nominatim(user_agent="broombuster")


@lru_cache(maxsize=1024)
def _reverse_geocode(lat: float, lon: float):
    location = _GEOLOCATOR.reverse((lat, lon), exactly_one=True)
    if location is None:
        return None, None
    myStreetName = location.raw['address'].get('road')  # type: ignore[union-attr]
    raw_num = location.raw['address'].get('house_number')  # type: ignore[union-attr]
    myNumber = _normalize.house_number(raw_num) if raw_num else None
    return myStreetName, myNumber


def get_street_info(myCar):
    return _reverse_geocode(round(myCar.lat, 4), round(myCar.lon, 4))

def get_nearby_streets(myCar):
    """Legacy Overpass-based lookup — kept for offline/CLI use only.
    The API server uses get_nearby_streets_from_gdf() instead."""
    point = _TRANSFORMER.transform(myCar.lon, myCar.lat)
    radius = 100

    query = f"""
    [out:json];
    way(around:{radius},{myCar.lat},{myCar.lon})["highway"];
    out geom;
    """

    url = "https://overpass-api.de/api/interpreter"
    try:
        response = requests.post(url, data={'data': query})
        data = response.json()
    except Exception:
        data = {"elements": []}

    myStreets = []
    for road in data['elements']:
        name = road['tags'].get('name')
        polyline = road.get('geometry')
        if name and polyline:
            distance = get_distance_point_polyline(point, polyline)
            myStreets.append((name, distance))

    myStreets.sort(key=lambda x: x[1])
    return myStreets


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


def get_distance_point_polyline(point, polyline):
    if len(polyline) < 2:
        return float('inf')

    # Batch-transform all vertices in one pyproj call instead of one per vertex.
    lons = np.array([v['lon'] for v in polyline])
    lats = np.array([v['lat'] for v in polyline])
    xs, ys = _TRANSFORMER.transform(lons, lats)
    pts = np.column_stack([xs, ys])  # shape (N, 2)

    # Vectorised point-to-segment distances across all segments at once.
    p1s = pts[:-1]  # segment starts, shape (N-1, 2)
    p2s = pts[1:]   # segment ends,   shape (N-1, 2)
    d12 = np.hypot(p2s[:, 1] - p1s[:, 1], p2s[:, 0] - p1s[:, 0])
    areas = np.abs(
        (p2s[:, 1] - p1s[:, 1]) * point[0]
        - (p2s[:, 0] - p1s[:, 0]) * point[1]
        + p2s[:, 0] * p1s[:, 1]
        - p2s[:, 1] * p1s[:, 0]
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        dists = np.where(d12 > 0, areas / d12, np.inf)
    return float(np.min(dists))


