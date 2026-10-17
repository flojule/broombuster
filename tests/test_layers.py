"""Extra map layers: the committed files, /api/rules lookups and the map catalogue."""

import json
from pathlib import Path

import geopandas
import pytest

from broombuster import layer_sources, layers
from broombuster.cities import CITIES, REGIONS

_ROOT = Path(__file__).parent.parent


def _manifest_layers():
    return [(ck, t) for ck, c in CITIES.items() for t in c.get("layers") or []]


@pytest.mark.parametrize("city,theme", _manifest_layers())
def test_every_manifest_layer_has_a_source(city, theme):
    assert theme in layers.THEMES
    assert city in layer_sources.SOURCES[theme]


@pytest.mark.parametrize("city,theme", [(c, t) for c, t in _manifest_layers()
                                        if layers.fgb_path(c, t).exists()])
def test_committed_layer_files_keep_only_the_schema(city, theme):
    gdf = geopandas.read_file(layers.fgb_path(city, theme))
    assert list(gdf.columns) == [*layer_sources.COLUMNS, "geometry"]
    assert len(gdf) and gdf.crs.to_epsg() == 4326
    lat0, lon0, lat1, lon1 = CITIES[city]["bbox"]
    minx, miny, maxx, maxy = gdf.total_bounds
    eps = 1e-6
    assert lon0 - eps <= minx and maxx <= lon1 + eps and lat0 - eps <= miny and maxy <= lat1 + eps


def test_core_layers_are_committed():
    for city, theme in [("berkeley", "parking"), ("oakland", "parking"),
                        ("san_francisco", "parking"), ("oakland", "meters"),
                        ("chicago_all", "winter"), ("berkeley", "fire")]:
        assert layers.fgb_path(city, theme).exists(), (city, theme)
    assert (layers.city_dir("chicago_all") / "permit_zones.csv").exists()


@pytest.mark.parametrize("region", sorted(REGIONS))
def test_client_geojson_is_small_and_has_no_rule_text(region):
    for path in (_ROOT / "frontend" / "layers" / region).glob("*.geojson"):
        assert path.stat().st_size < 3_000_000, path
        fc = json.loads(path.read_text())
        assert fc["features"]
        assert set(fc["features"][0]["properties"]) == {"label", "city"}, path


def test_catalogue_lists_hashed_urls_and_live_closures(app_client):
    regions = app_client.get("/api/cities").json()
    bay = {lyr["id"]: lyr for lyr in regions["bay_area"]["layers"]}
    assert {"parking", "meters", "fire", "markets", "closures"} <= set(bay)
    assert bay["parking"]["url"].startswith("/layers/bay_area/parking.geojson?v=")
    assert bay["closures"]["url"] == "/api/closures?region=bay_area"
    assert "closures" not in {lyr["id"] for lyr in regions["chicago"]["layers"]}
    r = app_client.get(bay["parking"]["url"])
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]


def _inside(city, theme, label_prefix=""):
    gdf = geopandas.read_file(layers.fgb_path(city, theme))
    row = gdf[gdf["label"].str.startswith(label_prefix)].iloc[0]
    p = row.geometry.representative_point() if row.geometry.geom_type != "Point" else row.geometry
    return p.y, p.x, row["label"]


def test_rules_at_a_berkeley_permit_area(app_client, monkeypatch):
    from broombuster import feeds
    monkeypatch.setattr(feeds, "closures", lambda region, now=None: [])
    lat, lon, label = _inside("berkeley", "parking", "Permit area")
    rules = app_client.get("/api/rules", params={"lat": lat, "lon": lon}).json()["rules"]
    hit = [r for r in rules if r["theme"] == "parking"]
    assert hit and hit[0]["label"] == label and "2 hours" in hit[0]["rule"]
    assert hit[0]["asof"] and hit[0]["city"] == "berkeley"


def test_home_rules_are_area_facts(app_client):
    lat, lon, label = _inside("berkeley", "fire")
    rules = app_client.get("/api/rules", params={"lat": lat, "lon": lon, "kind": "home"}).json()
    assert label in {r["label"] for r in rules["rules"]}
    assert all(r["theme"] in ("fire", "evac") for r in rules["rules"])


def test_rules_reject_bad_kind(app_client):
    params = {"lat": 37.8, "lon": -122.2, "kind": "x"}
    assert app_client.get("/api/rules", params=params).status_code == 422


def test_sf_rule_text():
    row = {"regulation": "Time limited", "hrlimit": "2", "days": "M-F", "from_time": "9am",
           "to_time": "6pm", "rpparea1": "N"}
    assert layer_sources._sf_rule(row) == "2 h limit, M–F 9am–6pm; permit area N holders exempt."
    assert layer_sources._sf_label(row) == "Permit area N"
    assert layer_sources._sf_label({"regulation": "No overnight parking"}) == "No overnight parking"
