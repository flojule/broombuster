"""Loads city + region configs from data/manifests/*.yaml.

A manifest only names a normaliser profile (schema:); it never remaps
columns, so each city's distinct format stays handled by its own
data_loader.SCHEMA_PROFILES entry. Validation fails loudly on a missing
required field, a malformed center, or a region naming an absent city.
"""

from __future__ import annotations

import os

import yaml

# This file is <repo>/src/broombuster/manifest.py — walk up three levels.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MANIFEST_DIR = os.path.join(_ROOT, "data", "manifests")
_REGIONS_FILE = "regions.yaml"

_REQUIRED_CITY_FIELDS = ("name", "center", "schema", "local_path")
_REQUIRED_REGION_FIELDS = ("name", "cities", "tz")


def load_all(manifest_dir: str | None = None) -> tuple[dict, dict]:
    """Return (CITIES, REGIONS) parsed from the manifest directory."""
    directory = manifest_dir or _MANIFEST_DIR
    cities = _load_cities(directory)
    regions = _load_regions(directory, cities)
    return cities, regions


def _read_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest {path} must parse to a mapping, got {type(data).__name__}")
    return data


def _load_cities(directory: str) -> dict:
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"Manifest directory not found: {directory}")
    cities: dict = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".yaml") or fname == _REGIONS_FILE:
            continue
        key = fname[: -len(".yaml")]
        city = _read_yaml(os.path.join(directory, fname))
        _validate_city(key, city)
        cities[key] = city
    if not cities:
        raise ValueError(f"No city manifests found in {directory}")
    return cities


def _validate_city(key: str, city: dict) -> None:
    missing = [f for f in _REQUIRED_CITY_FIELDS if f not in city]
    if missing:
        raise ValueError(f"City manifest '{key}' missing required field(s): {missing}")
    center = city["center"]
    if not isinstance(center, dict) or "lat" not in center or "lon" not in center:
        raise ValueError(f"City manifest '{key}' has a malformed center (need lat/lon): {center!r}")


def _load_regions(directory: str, cities: dict) -> dict:
    path = os.path.join(directory, _REGIONS_FILE)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Region manifest not found: {path}")
    regions = _read_yaml(path)
    for region_key, region in regions.items():
        _validate_region(region_key, region, cities)
    return regions


def _validate_region(region_key: str, region: dict, cities: dict) -> None:
    if not isinstance(region, dict):
        raise ValueError(f"Region '{region_key}' must be a mapping, got {type(region).__name__}")
    missing = [f for f in _REQUIRED_REGION_FIELDS if f not in region]
    if missing:
        raise ValueError(f"Region '{region_key}' missing required field(s): {missing}")
    absent = [c for c in region["cities"] if c not in cities]
    if absent:
        raise ValueError(f"Region '{region_key}' references unknown cities: {absent}")
