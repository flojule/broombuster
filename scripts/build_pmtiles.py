#!/usr/bin/env python3
"""
Build per-region PMTiles vector-tile archives from the normalised city data.

Each region's combined GeoDataFrame is merged to one feature per physical
segment/zone (maps.merge_segment_rows), written as newline-delimited GeoJSON,
and handed to tippecanoe. Tile features carry RAW schedule codes only — the
frontend (urgency.js) colours them against the current date, so archives stay
date-independent and only need rebuilding when the source data refreshes.

Usage (from repo root):

    python scripts/build_pmtiles.py                 # all regions
    python scripts/build_pmtiles.py --region chicago

Requires the `tippecanoe` binary on PATH (brew install tippecanoe).
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import shapely.geometry

from broombuster import analysis, data_loader, maps
from broombuster.cities import REGIONS
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)

_TILES_DIR = _ROOT / "frontend" / "tiles"
_SOURCE_LAYER = "zones"
_MINZOOM = 8
_MAXZOOM = 14


def _write_ndjson(region_key: str, path: Path) -> int:
    """Write merged region features as newline-delimited GeoJSON; return count."""
    gdf = data_loader.load_region_data(region_key)
    records = maps.merge_segment_rows(gdf)
    # Dissolved ward outlines (Chicago) drawn over the per-section fills.
    records = records + maps.ward_boundary_features(records)
    n = 0
    with open(path, "w") as fh:
        for rec in records:
            geom = rec["geometry"]
            if geom is None or geom.is_empty:
                continue
            props = {
                "render_type": rec["render_type"],
                "street":      rec["street"],
                "city":        rec["city"],
                # Raw schedule codes as a JSON string (MVT props are scalar).
                "sched":       json.dumps(rec["schedule"], separators=(",", ":")),
            }
            # Side display labels (e.g. SF "North"/"South") when not Even/Odd.
            labels = rec.get("labels")
            if labels and tuple(labels) != analysis.DEFAULT_SIDE_LABELS:
                props["side_even"], props["side_odd"] = labels
            feature = {
                "type": "Feature",
                "geometry": shapely.geometry.mapping(geom),
                "properties": props,
            }
            fh.write(json.dumps(feature, separators=(",", ":")))
            fh.write("\n")
            n += 1
    return n


def _run_tippecanoe(ndjson: Path, out: Path) -> None:
    cmd = [
        "tippecanoe",
        "-q",                      # quiet: no progress spam
        "-o", str(out),
        "-l", _SOURCE_LAYER,
        "-Z", str(_MINZOOM),
        "-z", str(_MAXZOOM),
        "--generate-ids",          # stable numeric ids for feature-state
        "--no-feature-limit",
        "--no-tile-size-limit",
        "--no-tiny-polygon-reduction",
        "--force",                 # overwrite existing archive
        str(ndjson),
    ]
    print("    " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def _build_region(region_key: str) -> bool:
    out = _TILES_DIR / f"{region_key}.pmtiles"
    print(f"\n{REGIONS[region_key]['name']} → {out.name}")
    _TILES_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False) as tf:
        ndjson = Path(tf.name)
    try:
        count = _write_ndjson(region_key, ndjson)
        if count == 0:
            print(f"    no features for {region_key} — skipping")
            return False
        _run_tippecanoe(ndjson, out)
    finally:
        ndjson.unlink(missing_ok=True)
    print(f"    ✓ {out.name}  ({count:,} features, {out.stat().st_size / 1_048_576:.1f} MB)")
    return True


def main() -> int:
    if not shutil.which("tippecanoe"):
        print("ERROR: tippecanoe not found on PATH (brew install tippecanoe).")
        return 2

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", action="append", choices=list(REGIONS),
                    help="Region key to build (repeatable; default: all)")
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
