"""Validate the PMTiles build inputs: merge_segment_rows, the ward dividers, the low-zoom
lines and the map layers' archives."""

import sys
from pathlib import Path

import shapely.geometry as sg

from broombuster import data_loader, layers, tiles

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_pmtiles


def _rec(coords, street, sched, render_type="line", city="x"):
    geom = sg.LineString(coords) if render_type == "line" else sg.box(*coords)
    return {
        "geometry": geom,
        "render_type": render_type,
        "street": street,
        "city": city,
        "schedule": sched,
    }


def test_street_features_merge_lines_per_schedule_below_street_zoom():
    """Each record keeps its place (feature ids are the input order) and, a line, starts at
    street zoom; below it one line per city and schedule carries every part."""
    tue = [{"code": "TE", "time": "", "desc": "", "side": "even"}]
    recs = [
        _rec([(0, 0), (0, 1)], "A ST", tue),
        _rec((0, 0, 1, 1), "ZONE 1", tue, render_type="polygon"),
        _rec([(1, 0), (1, 1)], "B ST", tue),
        _rec([(2, 0), (2, 1)], "C ST", []),
        _rec([(3, 0), (3, 1)], "D ST", tue, city="y"),
        {**_rec([(4, 0), (4, 1)], "E ST", tue), "geometry": sg.LineString()},
    ]
    feats = list(build_pmtiles.street_features(recs))
    high, low = feats[:5], feats[5:]
    assert [f["properties"]["street"] for f in high] == ["A ST", "ZONE 1", "B ST", "C ST", "D ST"]
    line = {"minzoom": build_pmtiles._LOW_MAXZOOM + 1}
    assert [f.get("tippecanoe") for f in high] == [line, None, line, line, line]
    assert all(f["tippecanoe"] == {"maxzoom": build_pmtiles._LOW_MAXZOOM} for f in low)
    by_key = {(f["properties"]["city"], f["properties"]["sched"]): f["geometry"] for f in low}
    assert len(by_key) == len(low) == 3
    assert len(by_key[("x", high[0]["properties"]["sched"])]["coordinates"]) == 2
    assert {f["properties"]["street"] for f in low} == {""}


def test_layer_features_skip_curbs_and_null_geometry():
    pt = {"type": "Point", "coordinates": [0, 0]}
    coll = {
        "features": [
            {"type": "Feature", "properties": {"kind": "curb"}, "geometry": pt},
            {"type": "Feature", "properties": {"kind": "curb"}, "geometry": None},
            {"type": "Feature", "properties": None, "geometry": pt},
        ]
    }
    assert [f["properties"] for f in build_pmtiles.layer_features(coll)] == [None]


_DEG = 1 / build_pmtiles._M_PER_DEG  # degrees per meter at latitude 0


def _len_m(geom):
    return geom.length / _DEG


def test_trim_at_crossings_cuts_only_ends_that_meet_another_street():
    """A block ends CORNER_TRIM_M short of a crossing street; an end that continues the same
    street, or meets nothing, stays; a short block loses at most 30% per end; the low-zoom
    line keeps the whole geometry."""
    n = 100 * _DEG
    recs = [
        _rec([(0, 0), (0, n)], "A ST", []),
        _rec([(0, n), (0, 2 * n)], "A ST", []),
        _rec([(0, n), (n, n)], "B ST", []),
        _rec([(n, n), (n, n + 10 * _DEG)], "C ST", []),
    ]
    build_pmtiles.trim_at_crossings(recs)
    trim = build_pmtiles.CORNER_TRIM_M
    assert [round(_len_m(r["geometry"]), 1) for r in recs] == [
        100 - trim,
        100 - trim,
        100 - 2 * trim,
        7.0,
    ]
    assert recs[0]["geometry"].coords[0] == (0, 0)  # the end that meets nothing stays
    assert round(recs[1]["geometry"].coords[0][1] / _DEG, 1) == 100 + trim
    feats = list(build_pmtiles.street_features(recs))
    low = next(f for f in feats if f.get("tippecanoe", {}).get("maxzoom"))
    assert round(_len_m(sg.MultiLineString(low["geometry"]["coordinates"]))) == 310


def test_permit_sides_name_each_curb_side_of_the_nearest_parallel_line():
    """A permit curb names the side it is on (left / right of the drawing direction) of the
    nearest parallel line; a crossing curb and a far one name nothing; the tile feature
    carries pl / pr."""
    n = 100 * _DEG

    def curb(coords, color):
        geom = {"type": "LineString", "coordinates": coords}
        return {"type": "Feature", "properties": {"kind": "curb", "color": color}, "geometry": geom}

    recs = [_rec([(0, 0), (n, 0)], "A ST", []), _rec([(0, 3 * n), (n, 3 * n)], "B ST", [])]
    curbs = [
        curb([(10 * _DEG, 12 * _DEG), (90 * _DEG, 12 * _DEG)], "#111111"),  # north: left
        curb([(90 * _DEG, -12 * _DEG), (10 * _DEG, -12 * _DEG)], "#222222"),  # south, drawn back
        curb([(50 * _DEG, -40 * _DEG), (50 * _DEG, 40 * _DEG)], "#333333"),  # crossing
        curb([(10 * _DEG, 60 * _DEG), (90 * _DEG, 60 * _DEG)], "#444444"),  # 60 m off: none
    ]
    build_pmtiles.permit_sides(recs, curbs)
    assert recs[0]["permit"] == {"l": "#111111", "r": "#222222"}
    assert "permit" not in recs[1]
    props = next(build_pmtiles.street_features(recs))["properties"]
    assert (props["pl"], props["pr"]) == ("#111111", "#222222")


def test_catalog_lists_layer_tiles_only_for_the_current_geojson(tmp_path, monkeypatch):
    """A layer's archive is offered only while it was built from the GeoJSON served."""
    # catalog is cached per process: call it uncached so the cache keeps the real tiles
    monkeypatch.setattr(layers, "_TILES", tmp_path)
    v = layers.file_version(layers._FRONTEND / "bay_area" / "parking.geojson")
    stale = tmp_path / "bay_area" / "parking.000000000000.pmtiles"
    stale.parent.mkdir()
    stale.write_bytes(b"")
    assert (
        "tiles" not in {lyr["id"]: lyr for lyr in layers.catalog.__wrapped__("bay_area")}["parking"]
    )
    layers.tiles_path("bay_area", "parking", v).write_bytes(b"")
    entry = {lyr["id"]: lyr for lyr in layers.catalog.__wrapped__("bay_area")}["parking"]
    assert entry["tiles"] == f"/tiles/bay_area/parking.{v}.pmtiles?v={v}"


def test_merge_segment_rows_chicago_shape():
    gdf = data_loader.load_region_data("chicago")
    records = tiles.merge_segment_rows(gdf)
    assert records, "no merged records for chicago"
    for rec in records:
        assert set(rec) >= {"geometry", "render_type", "street", "city", "schedule"}
        assert rec["render_type"] in ("line", "polygon")
        assert isinstance(rec["schedule"], list)
        for entry in rec["schedule"]:
            assert set(entry) == {"code", "time", "desc", "side"}
    # Chicago is polygons, one record per zone.
    assert all(r["render_type"] == "polygon" for r in records)


def test_merge_keeps_chicago_polygons_one_to_one():
    """Chicago zones are polygons: one tile record per non-empty zone row."""
    gdf = data_loader.load_region_data("chicago")
    merged = tiles.merge_segment_rows(gdf)
    assert len(merged) == int((~gdf.geometry.is_empty & gdf.geometry.notna()).sum())


def test_ward_boundary_features_chicago():
    """Chicago yields one merged line feature of the ward-vs-ward dividers."""
    gdf = data_loader.load_region_data("chicago")
    records = tiles.merge_segment_rows(gdf)
    wards = tiles.ward_boundary_features(records)
    assert len(wards) == 1, "ward dividers must merge into a single feature"
    w = wards[0]
    assert w["render_type"] == "ward_boundary"
    assert w["geometry"].geom_type in ("LineString", "MultiLineString")
    assert not w["geometry"].is_empty
    # Line-only regions (no polygons) yield none.
    assert not tiles.ward_boundary_features([r for r in records if r["render_type"] == "line"])


def _rows(*rows):
    """A line GeoDataFrame: one row per dict (its "coords" become the geometry)."""
    import geopandas
    import shapely.geometry as sg

    blank = {
        "STREET_NAME": "MAIN ST",
        "_city": "x",
        "DESC_EVEN": "",
        "TIME_EVEN": "",
        "DESC_ODD": "",
        "TIME_ODD": "",
    }
    return geopandas.GeoDataFrame(
        [
            {
                **blank,
                **{k: v for k, v in r.items() if k != "coords"},
                "geometry": sg.LineString(r["coords"]),
            }
            for r in rows
        ],
        crs="EPSG:4326",
    )


def test_even_left_follows_address_parity_and_direction():
    """A line whose sides differ records which side the even addresses are on, relative to
    the merged line's direction; a row drawn the other way round still agrees."""
    ab, ba = [(-122.0, 37.0), (-122.0, 37.001)], [(-122.0, 37.001), (-122.0, 37.0)]
    even_left = {"L_F_ADD": 100, "L_T_ADD": 198, "R_F_ADD": 101, "R_T_ADD": 199}
    even_right = {"L_F_ADD": 101, "L_T_ADD": 199, "R_F_ADD": 100, "R_T_ADD": 198}
    recs = tiles.merge_segment_rows(
        _rows(
            {"coords": ab, "DAY_EVEN": "ME", "DAY_ODD": None, **even_left},
            {"coords": ba, "DAY_EVEN": None, "DAY_ODD": "TE", **even_right},
        )
    )
    assert [r["even_left"] for r in recs] == [True]


def test_even_left_unset_when_sides_match_or_unknown():
    ab = [(-122.0, 37.0), (-122.0, 37.001)]
    cd = [(-122.1, 37.0), (-122.1, 37.001)]
    recs = tiles.merge_segment_rows(
        _rows(
            {"coords": ab, "DAY_EVEN": "ME", "DAY_ODD": "ME", "L_F_ADD": 100, "L_T_ADD": 198},
            {"coords": cd, "DAY_EVEN": "ME", "DAY_ODD": "TE"},
        )
    )
    assert [r["even_left"] for r in recs] == [None, None]


def test_lines_merge_by_street_and_endpoints():
    # Two streets between the same corners (SF's La Salle and Osceola): one line each, as the card.
    ab = [(-122.0, 37.0), (-122.0, 37.001)]
    curve = [(-122.0, 37.0), (-121.9995, 37.0005), (-122.0, 37.001)]
    recs = tiles.merge_segment_rows(
        _rows(
            {"coords": ab, "DAY_EVEN": "ME", "DAY_ODD": None},
            {"coords": ab, "DAY_EVEN": None, "DAY_ODD": "TE"},
            {"coords": curve, "STREET_NAME": "SIDE ST", "DAY_EVEN": "FE", "DAY_ODD": None},
        )
    )
    got = {(r["street"], tuple(s["code"] for s in r["schedule"])) for r in recs}
    assert got == {("MAIN ST", ("ME", "TE")), ("SIDE ST", ("FE",))}
    assert len(next(r for r in recs if r["street"] == "SIDE ST")["geometry"].coords) == 3
