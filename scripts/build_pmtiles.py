#!/usr/bin/env python3
"""
Build per-region PMTiles vector-tile archives from the normalized city data.

Each region's combined GeoDataFrame is merged to one feature per physical
segment/zone (tiles.merge_segment_rows), written as newline-delimited GeoJSON,
and handed to tippecanoe. Tile features carry RAW schedule codes only — the
frontend (urgency.js) colors them against the current date, so archives stay
date-independent and only need rebuilding when the source data refreshes.

| Zoom | Street archive (frontend/tiles/<region>.pmtiles) |
|---|---|
| < _MINZOOM | none (the map shows area labels, core.js AREA_ZOOM) |
| _MINZOOM to _LOW_MAXZOOM | one line per city and schedule: render_type, city, sched, street "" |
| > _LOW_MAXZOOM | one feature per street segment or zone, every property |

A block's line stops CORNER_TRIM_M short of a crossing with another street (trim_at_crossings),
so the curb lines the map draws from js/map.js CURB_ZOOM end at the corners; the low-zoom lines
keep the whole geometry. pl / pr: the permit area color of the curb left / right of the line's
drawing direction (permit_sides, from the region's permit curbs).

The large map layers (frontend/layers/<region>/<theme>.geojson, scripts/build_layers.py)
are built as PMTiles too (frontend/tiles/<region>/<theme>.<GeoJSON hash>.pmtiles), so the
map loads only the tiles in view; the catalog (layers.catalog) lists an archive only while
it matches its GeoJSON.

Usage (from repo root):

    python scripts/build_pmtiles.py                 # all regions
    python scripts/build_pmtiles.py --region chicago

Requires the `tippecanoe` binary on PATH, release TIPPECANOE_VERSION (brew install tippecanoe).
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np
import shapely
import shapely.geometry
import shapely.ops

from broombuster import analysis, data_loader, layers, tiles
from broombuster.cities import REGIONS
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)

_TILES_DIR = _ROOT / "frontend" / "tiles"
_LAYERS_DIR = _ROOT / "frontend" / "layers"
_SOURCE_LAYER = "zones"
_MINZOOM = 10
_LOW_MAXZOOM = 11
_MAXZOOM = 14
_LAYER_TILES_MIN_BYTES = 100_000  # a smaller map layer stays one GeoJSON download
_LAYER_MINZOOM = {"meters": 14}  # the map shows meters from z16 (js/layers.js LAYER_STYLE)
CORNER_TRIM_M = 7.0  # a block's line ends this far short of a crossing street
_NODE_M = 2.0  # line ends this close together meet
_CURB_NEAR_M = 25.0  # a permit curb names the side of the nearest parallel line within this
_PARALLEL_COS = 0.85  # a curb and a line this close to parallel (|cos| of their angle)
_M_PER_DEG = 111_320.0
TIPPECANOE_VERSION = "2.79.0"  # tiles differ between releases


def _line_parts(geom) -> list:
    """A line geometry's coordinate lists (one per part)."""
    g = shapely.geometry.mapping(geom)
    if g["type"] == "LineString":
        return [g["coordinates"]]
    if g["type"] == "MultiLineString":
        return list(g["coordinates"])
    raise ValueError(f"line record with {g['type']} geometry")


def _lines(records: list[dict]) -> list[dict]:
    """The line records with a geometry."""
    return [
        r
        for r in records
        if r["render_type"] == "line" and r["geometry"] is not None and not r["geometry"].is_empty
    ]


def _meters(lines: list[dict]):
    """(to_m, from_m): lon/lat <-> local meters (equirectangular at the lines' mean latitude)."""
    lat0 = float(np.mean([r["geometry"].centroid.y for r in lines]))
    k = np.array([_M_PER_DEG * math.cos(math.radians(lat0)), _M_PER_DEG])
    return (lambda g: shapely.transform(g, lambda c: c * k)), (
        lambda g: shapely.transform(g, lambda c: c / k)
    )


def trim_at_crossings(records: list[dict]) -> None:
    """Cut each line record CORNER_TRIM_M (at most 30% of its length) off every end that meets
    a line of another street, in place; `full` keeps the whole geometry (low zooms)."""
    lines = _lines(records)
    if not lines:
        return
    to_m, from_m = _meters(lines)
    geoms = [to_m(r["geometry"]) for r in lines]
    cells: dict[tuple[int, int], set] = {}
    unnamed = ("", "N/A")
    names = [r["street"] if r["street"] not in unnamed else f"#{i}" for i, r in enumerate(lines)]
    for g, name in zip(geoms, names, strict=True):
        for x, y in (g.coords[0], g.coords[-1]):
            cells.setdefault((round(x / _NODE_M), round(y / _NODE_M)), set()).add(name)

    def crossing(pt) -> bool:
        cx, cy = round(pt[0] / _NODE_M), round(pt[1] / _NODE_M)
        near = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near |= cells.get((cx + dx, cy + dy), set())
        return len(near) > 1

    for r, g in zip(lines, geoms, strict=True):
        cut = min(CORNER_TRIM_M, 0.3 * g.length)
        a = cut if crossing(g.coords[0]) else 0.0
        b = cut if crossing(g.coords[-1]) else 0.0
        if a or b:
            r["full"] = r["geometry"]
            r["geometry"] = from_m(shapely.ops.substring(g, a, g.length - b))


def _direction(line, s: float) -> np.ndarray:
    """Unit direction of a (meter) line at distance s along it."""
    a = line.interpolate(max(s - 1.0, 0.0))
    b = line.interpolate(min(s + 1.0, line.length))
    v = np.array([b.x - a.x, b.y - a.y])
    n = float(np.hypot(*v))
    return v / n if n else v


def permit_sides(records: list[dict], curbs: list[dict]) -> None:
    """Set each line record's `permit` {"l"/"r": color} in place: the permit color of the curb
    on that side (left / right of the drawing direction). A curb (a GeoJSON feature, kind
    curb) names the side of the nearest parallel line within _CURB_NEAR_M of its middle;
    the longest curb on a side wins."""
    lines = _lines(records)
    if not lines or not curbs:
        return
    to_m, _ = _meters(lines)
    geoms = [to_m(r.get("full", r["geometry"])) for r in lines]
    tree = shapely.STRtree(geoms)
    best: dict[tuple[int, str], tuple[float, str]] = {}
    for f in curbs:
        color = f["properties"]["color"]
        for part in _line_parts(shapely.geometry.shape(f["geometry"])):
            c = to_m(shapely.geometry.LineString(part))
            if c.length < 3.0:
                continue
            mid = c.interpolate(0.5, normalized=True)
            cdir = _direction(c, c.length / 2)
            near = tree.query(mid, predicate="dwithin", distance=_CURB_NEAR_M)
            for j in sorted(near.tolist(), key=lambda j: geoms[j].distance(mid)):
                g = geoms[j]
                s = g.project(mid)
                ldir = _direction(g, s)
                if abs(float(ldir @ cdir)) < _PARALLEL_COS:
                    continue
                p = g.interpolate(s)
                side = "l" if ldir[0] * (mid.y - p.y) - ldir[1] * (mid.x - p.x) > 0 else "r"
                if c.length > best.get((j, side), (0.0, ""))[0]:
                    best[j, side] = (c.length, color)
                break
    for (j, side), (_, color) in best.items():
        lines[j].setdefault("permit", {})[side] = color


def street_features(records: Iterable[dict]) -> Iterator[dict]:
    """Tile features of merged records, in record order (--generate-ids numbers them so),
    lines from _LOW_MAXZOOM + 1; then, up to _LOW_MAXZOOM, one line per city and schedule."""
    low: dict[tuple[str, str], list] = {}
    for rec in records:
        geom = rec["geometry"]
        if geom is None or geom.is_empty:
            continue
        props = {
            "render_type": rec["render_type"],
            "street": rec["street"],
            "city": rec["city"],
            # Raw schedule codes as a JSON string (MVT props are scalar).
            "sched": json.dumps(rec["schedule"], separators=(",", ":")),
        }
        # Side display labels (e.g. SF "North"/"South") when not Even/Odd.
        labels = rec.get("labels")
        if labels and tuple(labels) != analysis.DEFAULT_SIDE_LABELS:
            props["side_even"], props["side_odd"] = labels
        # Lines whose sides differ: the even side is left (true) or right of the
        # drawing direction, so the map can draw each side as its own line.
        if rec.get("even_left") is not None:
            props["even_left"] = rec["even_left"]
        for side, color in sorted((rec.get("permit") or {}).items()):
            props["p" + side] = color
        feature = {
            "type": "Feature",
            "geometry": shapely.geometry.mapping(geom),
            "properties": props,
        }
        if rec["render_type"] == "line":
            feature["tippecanoe"] = {"minzoom": _LOW_MAXZOOM + 1}
            whole = rec.get("full", geom)
            low.setdefault((props["city"], props["sched"]), []).extend(_line_parts(whole))
        yield feature
    for (city, sched), parts in low.items():
        yield {
            "type": "Feature",
            "tippecanoe": {"maxzoom": _LOW_MAXZOOM},
            "geometry": {"type": "MultiLineString", "coordinates": parts},
            # street "": no name (js/signs.js), as the line is many streets.
            "properties": {"render_type": "line", "street": "", "city": city, "sched": sched},
        }


def _write_ndjson(features: Iterable[dict], path: Path) -> int:
    """Write features as newline-delimited GeoJSON; return count."""
    n = 0
    with open(path, "w") as fh:
        for feature in features:
            fh.write(json.dumps(feature, separators=(",", ":")))
            fh.write("\n")
            n += 1
    return n


def _permit_curbs(region_key: str) -> list[dict]:
    """The region's permit curbs (parking layer features of kind curb), each in its area's
    edge color when the area is pale."""
    path = _LAYERS_DIR / region_key / "parking.geojson"
    if not path.exists():
        return []
    feats = [f for f in json.loads(path.read_text())["features"] if f.get("geometry")]
    edge = {
        (f["properties"]["city"], f["properties"]["label"]): f["properties"]["edge"]
        for f in feats
        if f["properties"].get("edge")
    }
    out = []
    for f in feats:
        p = f["properties"]
        if p.get("kind") == "curb":
            color = edge.get((p["city"], p["label"]), p["color"])
            out.append({**f, "properties": {**p, "color": color}})
    return out


def _region_features(region_key: str) -> Iterator[dict]:
    records = tiles.merge_segment_rows(data_loader.load_region_data(region_key))
    permit_sides(records, _permit_curbs(region_key))
    trim_at_crossings(records)
    # Dissolved ward outlines (Chicago) drawn over the per-section fills.
    return street_features(records + tiles.ward_boundary_features(records))


def layer_features(collection: dict) -> Iterator[dict]:
    """A map layer's GeoJSON features for tippecanoe: none without geometry, and no permit
    curbs (the street tiles carry them as each curb's permit color, permit_sides)."""
    for f in collection["features"]:
        if f.get("geometry") is None or (f.get("properties") or {}).get("kind") == "curb":
            continue
        yield f


def _run_tippecanoe(ndjson: Path, out: Path, layer: str, minzoom: int, *extra: str) -> None:
    cmd = [
        "tippecanoe",
        "-q",  # quiet: no progress spam
        "-o",
        str(out),
        "-l",
        layer,
        "-Z",
        str(minzoom),
        "-z",
        str(_MAXZOOM),
        "--generate-ids",  # stable numeric ids for feature-state
        "--no-tiny-polygon-reduction",
        "--no-feature-limit",  # never drop features silently (-q hides the warning)
        "--no-tile-size-limit",
        *extra,
        "--force",  # overwrite existing archive
        str(ndjson),
    ]
    print("    " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def _build(features: Iterable[dict], out: Path, layer: str, minzoom: int, *extra: str) -> int:
    """Write one archive; return its feature count (0: nothing written)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as tf:
        ndjson = Path(tf.name)
    try:
        count = _write_ndjson(features, ndjson)
        if count:
            _run_tippecanoe(ndjson, out, layer, minzoom, *extra)
    finally:
        ndjson.unlink(missing_ok=True)
    if count:
        print(f"    ✓ {out.name}  ({count:,} features, {out.stat().st_size / 1_048_576:.1f} MB)")
    return count


def _build_region(region_key: str) -> bool:
    out = _TILES_DIR / f"{region_key}.pmtiles"
    print(f"\n{REGIONS[region_key]['name']} → {out.name}")
    if not _build(_region_features(region_key), out, _SOURCE_LAYER, _MINZOOM):
        print(f"    no features for {region_key} — skipping")
        return False
    _build_layers(region_key)
    return True


def _build_layers(region_key: str) -> None:
    """The region's large map layers as PMTiles (layers.tiles_path); older builds removed."""
    for path in sorted((_LAYERS_DIR / region_key).glob("*.geojson")):
        theme = path.stem
        out = layers.tiles_path(region_key, theme, layers.file_version(path))
        for old in out.parent.glob(f"{theme}.*.pmtiles"):
            if old != out:
                old.unlink()
        if path.stat().st_size < _LAYER_TILES_MIN_BYTES:
            continue
        collection = json.loads(path.read_text())
        minzoom = _LAYER_MINZOOM.get(theme, _MINZOOM)
        _build(layer_features(collection), out, theme, minzoom, "-r1")  # -r1: every point


def _tippecanoe_version() -> str:
    """`tippecanoe --version` without the leading "v" ("" when not on PATH)."""
    if not shutil.which("tippecanoe"):
        return ""
    out = subprocess.run(["tippecanoe", "--version"], capture_output=True, text=True, check=True)
    return (out.stdout + out.stderr).strip().removeprefix("tippecanoe v")


def main() -> int:
    found = _tippecanoe_version()
    if found != TIPPECANOE_VERSION:
        print(
            f"ERROR: needs tippecanoe {TIPPECANOE_VERSION} (found {found or 'none'}) for "
            "reproducible tiles: install it, or set TIPPECANOE_VERSION and rebuild every region."
        )
        return 2

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--region",
        action="append",
        choices=list(REGIONS),
        help="Region key to build (repeatable; default: all)",
    )
    args = ap.parse_args()

    failed = []
    for rk in args.region or list(REGIONS):
        try:
            if not _build_region(rk):
                failed.append(rk)
        except subprocess.CalledProcessError as exc:
            print(f"    ⚠  tippecanoe failed for {rk}: {exc}")
            failed.append(rk)
    if failed:
        print(f"\nFailed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
