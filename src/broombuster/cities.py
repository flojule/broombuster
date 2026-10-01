"""Cities and regions from data/manifests/*.yaml (one file per city + regions.yaml)."""

import os

import yaml

from broombuster.config import REPO_ROOT

MANIFEST_DIR = os.path.join(REPO_ROOT, "data", "manifests")
_CITY_FIELDS = ("name", "center", "schema", "bbox", "fgb_path", "source")
_REGION_FIELDS = ("name", "cities", "center", "zoom", "tz")


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_all(directory: str = MANIFEST_DIR) -> tuple[dict, dict]:
    """(CITIES, REGIONS), failing loudly on a missing field or unknown city."""
    cities = {f[:-5]: _read(os.path.join(directory, f))
              for f in sorted(os.listdir(directory))
              if f.endswith(".yaml") and f != "regions.yaml"}
    regions = _read(os.path.join(directory, "regions.yaml"))
    for kind, items, fields in (("city", cities, _CITY_FIELDS),
                                ("region", regions, _REGION_FIELDS)):
        for key, item in items.items():
            missing = [f for f in fields if f not in item]
            if missing:
                raise ValueError(f"{kind} '{key}' is missing {missing}")
    for key, region in regions.items():
        unknown = [c for c in region["cities"] if c not in cities]
        if unknown:
            raise ValueError(f"region '{key}' lists unknown cities {unknown}")
    return cities, regions


CITIES, REGIONS = load_all()


def _dist2(center: dict, lat: float, lon: float) -> float:
    return (center["lat"] - lat) ** 2 + (center["lon"] - lon) ** 2


def in_bbox(city_key: str, lat: float, lon: float) -> bool:
    lat_min, lon_min, lat_max, lon_max = CITIES[city_key]["bbox"]
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def region_for_point(lat: float, lon: float) -> str:
    """Region whose center is nearest (lat, lon)."""
    return min(REGIONS, key=lambda rk: _dist2(REGIONS[rk]["center"], lat, lon))


def city_for_point(lat: float, lon: float, region_key: str) -> str:
    """City by bbox (smallest containing one), else nearest center.

    For points without a street match; resolve.locate prefers the segment's city.
    """
    keys = REGIONS[region_key]["cities"]
    inside = [ck for ck in keys if in_bbox(ck, lat, lon)]
    if inside:
        def _area(ck):
            b = CITIES[ck]["bbox"]
            return (b[2] - b[0]) * (b[3] - b[1])
        return min(inside, key=_area)
    return min(keys, key=lambda ck: _dist2(CITIES[ck]["center"], lat, lon))
