"""City + region configs, loaded from data/manifests/*.yaml.

CITIES maps city_key -> config: name, center, manual_default, schema, bbox,
fgb_path, source {local_path, url?, files?, sha256?, build?, notes}, plus
optional stale_after_days / schedule_pdf_url / trash. REGIONS groups cities
geographically (name, cities, center, tz, overview frame).

A manifest names a normaliser profile via `schema`; it never remaps columns,
so each city's distinct format stays handled by its own
schemas.SCHEMA_PROFILES entry. Add a city by dropping a new YAML in
data/manifests/ (and a new SCHEMA_PROFILES entry if its format is novel).
Loading fails loudly on a missing required field, a malformed center, or a
region naming an absent city.
"""

from __future__ import annotations

import os

import yaml

from broombuster.config import REPO_ROOT

MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests")
_REGIONS_FILE = "regions.yaml"
_REQUIRED_CITY_FIELDS = ("name", "center", "schema", "fgb_path", "source")
_REQUIRED_REGION_FIELDS = ("name", "cities", "center", "tz")


def _read_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest {path} must parse to a mapping, got {type(data).__name__}")
    return data


def _check_center(what: str, center) -> None:
    if not isinstance(center, dict) or "lat" not in center or "lon" not in center:
        raise ValueError(f"{what} has a malformed center (need lat/lon): {center!r}")


def load_all(directory: str = MANIFEST_DIR) -> tuple[dict, dict]:
    """(CITIES, REGIONS) parsed and validated from a manifest directory."""
    cities: dict = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".yaml") or fname == _REGIONS_FILE:
            continue
        key = fname[: -len(".yaml")]
        city = _read_yaml(os.path.join(directory, fname))
        missing = [f for f in _REQUIRED_CITY_FIELDS if f not in city]
        if missing or "local_path" not in (city.get("source") or {}):
            raise ValueError(f"City manifest '{key}' missing required field(s): "
                             f"{missing or ['source.local_path']}")
        _check_center(f"City manifest '{key}'", city["center"])
        cities[key] = city
    if not cities:
        raise ValueError(f"No city manifests found in {directory}")

    regions = _read_yaml(os.path.join(directory, _REGIONS_FILE))
    for key, region in regions.items():
        if not isinstance(region, dict):
            raise ValueError(f"Region '{key}' must be a mapping, got {type(region).__name__}")
        missing = [f for f in _REQUIRED_REGION_FIELDS if f not in region]
        if missing:
            raise ValueError(f"Region '{key}' missing required field(s): {missing}")
        absent = [c for c in region["cities"] if c not in cities]
        if absent:
            raise ValueError(f"Region '{key}' references unknown cities: {absent}")
        _check_center(f"Region '{key}'", region["center"])
    return cities, regions


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
