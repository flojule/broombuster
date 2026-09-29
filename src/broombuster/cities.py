"""City + region configs, loaded from data/manifests/*.yaml.

CITIES maps city_key -> config (name, center, manual_default, local_path,
url, schema, bbox, fgb_path, plus optional stale_after_days / schedule_pdf_url).
REGIONS groups cities geographically (name, cities, center, tz, overview frame).

A manifest names a normaliser profile via `schema`; it never remaps columns,
so each city's distinct format stays handled by its own
data_loader.SCHEMA_PROFILES entry. Add a city by dropping a new YAML in
data/manifests/ (and a new SCHEMA_PROFILES entry if its format is novel).
"""

from broombuster.manifest import load_all

CITIES, REGIONS = load_all()


def _dist2(center: dict, lat: float, lon: float) -> float:
    return (center["lat"] - lat) ** 2 + (center["lon"] - lon) ** 2


def in_bbox(city_key: str, lat: float, lon: float) -> bool:
    """True if (lat, lon) lies inside the city's manifest bbox."""
    bbox = CITIES[city_key].get("bbox")
    if not bbox:
        return False
    lat_min, lon_min, lat_max, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def region_for_point(lat: float, lon: float) -> str:
    """Region whose center is nearest (lat, lon)."""
    return min(REGIONS, key=lambda rk: _dist2(REGIONS[rk]["center"], lat, lon))


def region_of(city_key: str) -> str | None:
    """Region containing the city, or None."""
    return next((rk for rk, rv in REGIONS.items() if city_key in rv["cities"]), None)


def city_for_point(lat: float, lon: float, region_key: str) -> str:
    """Best-guess city with no street data: smallest bbox containing the point,
    else nearest center. Prefer resolve.locate, which uses the street segment."""
    keys = REGIONS[region_key]["cities"]
    inside = [ck for ck in keys if in_bbox(ck, lat, lon)]
    if inside:
        def _area(ck):
            b = CITIES[ck]["bbox"]
            return (b[2] - b[0]) * (b[3] - b[1])
        return min(inside, key=_area)
    return min(keys, key=lambda ck: _dist2(CITIES[ck]["center"], lat, lon))
