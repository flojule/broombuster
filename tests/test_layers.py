"""Extra map layers: the committed files, /api/rules lookups and the map catalogue."""

import itertools
import json
from pathlib import Path

import geopandas
import pytest
import shapely

from broombuster import layer_sources, layers
from broombuster.cities import CITIES, REGIONS

_ROOT = Path(__file__).parent.parent


def _manifest_layers():
    return [(ck, t) for ck, c in CITIES.items() for t in c.get("layers") or []]


@pytest.mark.parametrize("city,theme", _manifest_layers())
def test_every_manifest_layer_has_a_source(city, theme):
    assert theme in layers.THEMES
    assert city in layer_sources.SOURCES[theme]


@pytest.mark.parametrize(
    "city,theme", [(c, t) for c, t in _manifest_layers() if layers.fgb_path(c, t).exists()]
)
def test_committed_layer_files_keep_only_the_schema(city, theme):
    gdf = geopandas.read_file(layers.fgb_path(city, theme))
    extra = [layer_sources.COLOR] if layer_sources.COLOR in gdf else []  # a city's own colours
    assert list(gdf.columns) == [*layer_sources.COLUMNS, *extra, "geometry"]
    assert len(gdf) and gdf.crs.to_epsg() == 4326
    lat0, lon0, lat1, lon1 = CITIES[city]["bbox"]
    minx, miny, maxx, maxy = gdf.total_bounds
    eps = 1e-6
    assert lon0 - eps <= minx and maxx <= lon1 + eps and lat0 - eps <= miny and maxy <= lat1 + eps


def test_core_layers_are_committed():
    for city, theme in [
        ("berkeley", "parking"),
        ("oakland", "parking"),
        ("san_francisco", "parking"),
        ("oakland", "meters"),
        ("chicago_all", "winter"),
        ("berkeley", "fire"),
    ]:
        assert layers.fgb_path(city, theme).exists(), (city, theme)
    assert (layers.city_dir("chicago_all") / "permit_zones.csv").exists()


@pytest.mark.parametrize("region", sorted(REGIONS))
def test_client_geojson_is_small_and_has_no_rule_text(region):
    for path in (_ROOT / "frontend" / "layers" / region).glob("*.geojson"):
        assert path.stat().st_size < 3_000_000, path
        fc = json.loads(path.read_text())
        assert fc["features"]
        props = set(fc["features"][0]["properties"])
        assert props == (
            {"label", "city", "color", "edge"} if path.stem == "parking" else {"label", "city"}
        ), path


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
    row = {
        "regulation": "Time limited",
        "hrlimit": "2",
        "days": "M-F",
        "from_time": "9am",
        "to_time": "6pm",
        "rpparea1": "N",
    }
    assert layer_sources._sf_rule(row) == "2 h limit, M–F 9am–6pm; permit area N holders exempt."
    assert layer_sources._sf_label(row) == "Permit area N"
    assert layer_sources._sf_label({"regulation": "No overnight parking"}) == "No overnight parking"


def test_sf_missing_fields_are_not_nan():
    # Socrata rows read into a frame carry NaN for an empty field: never "Permit area nan".
    row = {
        "regulation": "Time limited",
        "hrlimit": "4",
        "days": "M-Sa",
        "from_time": "8am",
        "to_time": "6pm",
        "rpparea1": float("nan"),
    }
    assert layer_sources._sf_label(row) == "4 h limit"
    assert layer_sources._sf_rule(row) == "4 h limit, M–Sa 8am–6pm."


def _build():
    import importlib.util

    path = _ROOT / "scripts" / "build_layers.py"
    spec = importlib.util.spec_from_file_location("build_layers", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def test_permit_area_codes_and_sf_limits():
    assert layer_sources.permit_area("AREA A") == "A"
    assert layer_sources.permit_area(" Q ") == "Q"
    assert layer_sources.permit_area("0") == layer_sources.permit_area(float("nan")) == ""
    assert layer_sources._limit("0.330000013") == "20 min limit"
    assert layer_sources._limit("2.0") == "2 h limit"
    assert layer_sources._limit("0") == ""


def test_arcgis_colors_read_the_city_renderer(monkeypatch):
    meta = {
        "drawingInfo": {
            "renderer": {
                "type": "uniqueValue",
                "uniqueValueInfos": [
                    {"value": "AREA A", "symbol": {"color": [115, 178, 255, 255]}}
                ],
            }
        }
    }

    class R:
        def json(self):
            return meta

    monkeypatch.setattr(layer_sources, "_get", lambda url, **p: R())
    assert layer_sources.arcgis_colors("x") == {"AREA A": "#73b2ff"}


def test_permit_areas_take_the_city_colour_else_one_their_neighbours_lack():
    build = _build()
    sq = [shapely.box(-122.42 + i * 0.002, 37.77, -122.4185 + i * 0.002, 37.7715) for i in range(4)]
    gdf = geopandas.GeoDataFrame(
        {
            "label": [f"Permit area {c}" for c in "ABCD"],
            "city": ["oakland"] * 4,
            "asof": ["2019-01-09"] * 4,
            "color": ["#ffff73", None, None, None],
        },
        geometry=sq,
        crs="EPSG:4326",
    )
    out = build.permit_areas(gdf).set_index("label")
    assert out.loc["Permit area A", "color"] == "#ffff73"
    assert out.loc["Permit area A", "edge"] == "#999945"  # pale: a darker edge
    colors = list(out["color"])
    assert all(a != b for a, b in itertools.pairwise(colors))  # touching areas differ
    assert set(colors[1:]) <= set(build.AREA_PALETTE)


def test_rules_inside_a_permit_area_away_from_its_curbs(app_client, monkeypatch):
    # Away from any permit curb, the area as drawn on the map still applies.
    from broombuster import feeds

    monkeypatch.setattr(feeds, "closures", lambda region, now=None: [])
    fc = json.loads((_ROOT / "frontend" / "layers" / "bay_area" / "parking.geojson").read_text())
    f = next(f for f in fc["features"] if f["properties"]["city"] == "oakland")
    p = shapely.geometry.shape(f["geometry"]).representative_point()
    rules = app_client.get("/api/rules", params={"lat": p.y, "lon": p.x}).json()["rules"]
    hit = [r for r in rules if r["label"] == f["properties"]["label"]]
    assert hit and hit[0]["color"] == f["properties"]["color"] and "exempt" in hit[0]["rule"]
    assert hit[0]["asof"]


@pytest.mark.parametrize("city", sorted(layer_sources.AREAS))
def test_committed_permit_areas_are_one_clean_polygon_each(city):
    gdf = geopandas.read_file(layers.fgb_path(city, layers.AREAS))
    assert list(gdf.columns[:3]) == list(layer_sources.COLUMNS)
    assert gdf["label"].is_unique and gdf["label"].str.match(r"Permit area \w+$").all()
    assert gdf.geometry.is_valid.all()
    assert set(gdf.geometry.geom_type) <= {"Polygon", "MultiPolygon"}
    m = gdf.to_crs(3857)
    for (_, a), (_, b) in itertools.combinations(m.iterrows(), 2):
        assert a.geometry.intersection(b.geometry).area < 1, (a["label"], b["label"])


def test_map_permit_areas_do_not_overlap_across_cities():
    g = geopandas.read_file(_ROOT / "frontend" / "layers" / "bay_area" / "parking.geojson")
    by_city = [part.to_crs(3857).union_all() for _, part in g.groupby("city")]
    for a, b in itertools.combinations(by_city, 2):
        assert a.intersection(b).area < 500  # the 1e-6° coordinate rounding only


def _square(x0, y0, size_m):
    m = 1 / 111_000  # ° per metre, near enough for a test
    return shapely.box(
        -122.42 + x0 * m, 37.77 + y0 * m, -122.42 + (x0 + size_m) * m, 37.77 + (y0 + size_m) * m
    )


def test_areas_close_over_streets_and_never_overlap():
    # Parcel sources (SF): one area's lots across a 30 m street become one area; a lot
    # drawn in two areas goes to the first by label; a lone sliver is dropped.
    lots = [_square(0, 0, 100), _square(130, 0, 100), _square(220, 0, 100), _square(500, 0, 5)]
    gdf = geopandas.GeoDataFrame({"area": ["A", "A", "B", "A"]}, geometry=lots, crs="EPSG:4326")
    out = layer_sources._areas(gdf, "area", "2024-01-15", close_m=20).set_index("label")
    a, b = out.geometry["Permit area A"], out.geometry["Permit area B"]
    assert len(a.geoms) == 1  # the street between the lots closed, the sliver gone
    assert a.contains(_square(100, 40, 30).centroid)
    assert b.intersection(a).area < 1e-12 and not b.is_empty
    assert set(out["rule"]) == {layer_sources.AREA_RULE} and set(out["asof"]) == {"2024-01-15"}


def test_map_areas_give_a_cross_city_overlap_to_the_newer_source():
    build = _build()
    gdf = geopandas.GeoDataFrame(
        {
            "label": ["Permit area A", "Permit area D"],
            "city": ["berkeley", "oakland"],
            "asof": ["2026-08-19", "2019-01-09"],
        },
        geometry=[_square(0, 0, 100), _square(50, 0, 100)],
        crs="EPSG:4326",
    )
    out = build.permit_areas(gdf).set_index("city")
    assert out.geometry["berkeley"].symmetric_difference(gdf.geometry[0]).area < 1e-12
    assert not out.geometry["oakland"].contains(_square(60, 40, 10).centroid)
    assert out.geometry["oakland"].contains(_square(120, 40, 10).centroid)
