"""Manifest migration is lossless.

CITIES/REGIONS now load from data/manifests/*.yaml. These assert the loaded
config equals the pre-refactor hardcoded dicts byte-for-byte (frozen snapshot
below), that every region city exists, and that every city schema maps to a
known SCHEMA_PROFILES normaliser. Guards against silently dropping a field or
collapsing the distinct Oakland/SF/Chicago formats.
"""

from broombuster import data_loader
from broombuster.cities import CITIES, REGIONS
from broombuster.manifest import load_all

# Expected CITIES: the pre-refactor config plus intentional post-refactor
# additions (Oakland's `trash` ReCollect block). Guards against accidental drift.
_EXPECTED_CITIES = {
    "oakland": {
        "name": "Oakland, CA",
        "center": {"lat": 37.8044, "lon": -122.2712},
        "manual_default": {"lat": 37.821326, "lon": -122.280705},
        "local_path": "data/oakland/StreetSweeping.shp",
        "url": None,
        "schema": "oakland",
        "bbox": [37.69, -122.38, 37.90, -122.11],
        "fgb_path": "data/oakland/StreetSweeping.fgb",
        "trash": {"kind": "recollect", "area": "OaklandCA", "service_id": 608},
    },
    "san_francisco": {
        "name": "San Francisco, CA",
        "center": {"lat": 37.7749, "lon": -122.4194},
        "manual_default": {"lat": 37.7749, "lon": -122.4194},
        "local_path": "data/san_francisco/StreetSweeping.geojson",
        "url": "https://data.sfgov.org/resource/yhqp-riqs.geojson?$limit=200000",
        "schema": "sf",
        "bbox": [37.70, -122.53, 37.84, -122.35],
        "stale_after_days": 30,
        "fgb_path": "data/san_francisco/StreetSweeping.fgb",
    },
    "berkeley": {
        "name": "Berkeley, CA",
        "center": {"lat": 37.8716, "lon": -122.2727},
        "manual_default": {"lat": 37.8716, "lon": -122.2727},
        "local_path": "data/berkeley/StreetSweeping.geojson",
        "url": None,
        "schema": "berkeley",
        "bbox": [37.84, -122.32, 37.91, -122.23],
        "fgb_path": "data/berkeley/StreetSweeping.fgb",
    },
    "alameda": {
        "name": "Alameda, CA",
        "center": {"lat": 37.7652, "lon": -122.2416},
        "manual_default": {"lat": 37.7652, "lon": -122.2416},
        "local_path": "data/alameda/StreetSweeping.geojson",
        "url": None,
        "schema": "alameda",
        "bbox": [37.73, -122.33, 37.79, -122.21],
        "fgb_path": "data/alameda/StreetSweeping.fgb",
    },
    "chicago_all": {
        "name": "Chicago, IL",
        "center": {"lat": 41.8781, "lon": -87.6298},
        "manual_default": {"lat": 41.9951, "lon": -87.6593},
        "local_path": "data/chicago/StreetSweepingZones.geojson",
        "url": "https://data.cityofchicago.org/resource/2r7q-emq3.geojson?$limit=50000",
        "schema": "chicago",
        "bbox": [41.644, -87.848, 42.024, -87.524],
        "stale_after_days": 90,
        "fgb_path": "data/chicago/StreetSweepingZones.fgb",
        "schedule_pdf_url": (
            "https://www.chicago.gov/content/dam/city/depts/streets/supp_info/"
            "2026-Street-Sweeping/2026-Sweeping-Schedules/"
            "{ward}-Ward-Sweeping-Schedule-2026.pdf"
        ),
    },
}

_EXPECTED_REGIONS = {
    "bay_area": {
        "name": "Bay Area",
        "cities": ["oakland", "san_francisco", "berkeley", "alameda"],
        "center": {"lat": 37.820, "lon": -122.295},
        "manual_default": {"lat": 37.821326, "lon": -122.280705},
        "overview_zoom": 11.5,
        "tz": "America/Los_Angeles",
    },
    "chicago": {
        "name": "Chicago",
        "cities": ["chicago_all"],
        "center": {"lat": 41.975, "lon": -87.660},
        "manual_default": {"lat": 41.996593, "lon": -87.665282},
        "overview_zoom": 13,
        "tz": "America/Chicago",
    },
}


def test_loaded_cities_match_frozen_snapshot():
    assert CITIES == _EXPECTED_CITIES


def test_loaded_regions_match_frozen_snapshot():
    assert REGIONS == _EXPECTED_REGIONS


def test_load_all_is_pure_returns_same_config():
    cities, regions = load_all()
    assert cities == _EXPECTED_CITIES
    assert regions == _EXPECTED_REGIONS


def test_every_region_city_exists():
    for region_key, region in REGIONS.items():
        for city_key in region["cities"]:
            assert city_key in CITIES, f"{region_key} references missing city {city_key}"


def test_every_city_schema_has_a_profile():
    for city_key, city in CITIES.items():
        assert city["schema"] in data_loader.SCHEMA_PROFILES, (
            f"{city_key} uses schema {city['schema']!r} with no SCHEMA_PROFILES entry"
        )
