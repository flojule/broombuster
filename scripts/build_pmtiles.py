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
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import shapely.geometry

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
_CURB_MINZOOM = 14  # permit curbs show from z14.5 (js/layers.js CURB_ZOOM)
TIPPECANOE_VERSION = "2.79.0"  # tiles differ between releases


def _line_parts(geom) -> list:
    """A line geometry's coordinate lists (one per part)."""
    g = shapely.geometry.mapping(geom)
    if g["type"] == "LineString":
        return [g["coordinates"]]
    if g["type"] == "MultiLineString":
        return list(g["coordinates"])
    raise ValueError(f"line record with {g['type']} geometry")


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
        feature = {
            "type": "Feature",
            "geometry": shapely.geometry.mapping(geom),
            "properties": props,
        }
        if rec["render_type"] == "line":
            feature["tippecanoe"] = {"minzoom": _LOW_MAXZOOM + 1}
            low.setdefault((props["city"], props["sched"]), []).extend(_line_parts(geom))
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


def _region_features(region_key: str) -> Iterator[dict]:
    records = tiles.merge_segment_rows(data_loader.load_region_data(region_key))
    # Dissolved ward outlines (Chicago) drawn over the per-section fills.
    return street_features(records + tiles.ward_boundary_features(records))


def layer_features(collection: dict) -> Iterator[dict]:
    """A map layer's GeoJSON features for tippecanoe (none without geometry): permit curbs
    from _CURB_MINZOOM."""
    for f in collection["features"]:
        if f.get("geometry") is None:
            continue
        if (f.get("properties") or {}).get("kind") == "curb":
            f = {**f, "tippecanoe": {"minzoom": _CURB_MINZOOM}}
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
