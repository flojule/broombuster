"""City/region manifests load, validate and point at real data."""

import os

import pytest

from broombuster.cities import CITIES, REGIONS, load_all
from broombuster.config import REPO_ROOT
from broombuster.schemas import SCHEMA_PROFILES


def test_every_region_city_exists():
    for region_key, region in REGIONS.items():
        for city_key in region["cities"]:
            assert city_key in CITIES, f"{region_key} references missing city {city_key}"


@pytest.mark.parametrize("city_key", sorted(CITIES))
def test_city_manifest_is_complete(city_key):
    city = CITIES[city_key]
    assert city["schema"] in SCHEMA_PROFILES
    assert os.path.exists(os.path.join(REPO_ROOT, city["fgb_path"])), city["fgb_path"]
    src = city["source"]
    assert src["local_path"] and src.get("notes")
    if src.get("build"):
        assert os.path.exists(os.path.join(REPO_ROOT, src["build"])), src["build"]


def test_missing_source_is_rejected(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "name: X\ncenter: {lat: 1, lon: 2}\nschema: sf\nfgb_path: x.fgb\nsource: {}\n")
    (tmp_path / "regions.yaml").write_text(
        "r: {name: R, cities: [x], center: {lat: 1, lon: 2}, tz: UTC}\n")
    with pytest.raises(ValueError, match="source.local_path"):
        load_all(str(tmp_path))
