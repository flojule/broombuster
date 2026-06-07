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
    python scripts/build_pmtiles.py --force         # ignore mtime, rebuild all

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

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))

from broombuster import data_loader, maps  # noqa: E402
from broombuster.cities import REGIONS  # noqa: E402

_TILES_DIR = _ROOT / "frontend" / "tiles"
_MANIFEST = _TILES_DIR / "manifest.json"
_SOURCE_LAYER = "zones"
_MINZOOM = 8
_MAXZOOM = 14


def _region_source_mtime(region_key: str) -> float:
    """Newest source FGB mtime across the region's cities (0 if none on disk)."""
    from broombuster.cities import CITIES

    newest = 0.0
    for ck in REGIONS[region_key]["cities"]:
        fgb = _ROOT / CITIES[ck]["fgb_path"]
        if fgb.exists():
            newest = max(newest, fgb.stat().st_mtime)
    return newest


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
            feature = {
                "type": "Feature",
                "geometry": shapely.geometry.mapping(geom),
                "properties": {
                    "render_type": rec["render_type"],
                    "street":      rec["street"],
                    "city":        rec["city"],
                    # Raw schedule codes as a JSON string (MVT props are scalar).
                    "sched":       json.dumps(rec["schedule"], separators=(",", ":")),
                },
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


def _build_region(region_key: str, force: bool, manifest: dict) -> bool:
    out = _TILES_DIR / f"{region_key}.pmtiles"
    src_mtime = _region_source_mtime(region_key)
    prev = manifest.get(region_key)
    if (not force and out.exists() and prev
            and abs(prev.get("source_mtime", -1) - src_mtime) < 1e-6):
        print(f"{region_key}: up to date (source unchanged) — skipping")
        return True

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

    size_mb = out.stat().st_size / 1_048_576
    print(f"    ✓ {out.name}  ({count:,} features, {size_mb:.1f} MB)")
    manifest[region_key] = {
        "archive": out.name,
        "features": count,
        "source_mtime": src_mtime,
    }
    return True


def main() -> int:
    if not shutil.which("tippecanoe"):
        print("ERROR: tippecanoe not found on PATH (brew install tippecanoe).")
        return 2

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", action="append",
                    help="Region key to build (repeatable; default: all)")
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if the source FGBs are unchanged")
    args = ap.parse_args()

    targets = args.region or list(REGIONS.keys())
    manifest = {}
    if _MANIFEST.exists():
        try:
            manifest = json.loads(_MANIFEST.read_text())
        except (json.JSONDecodeError, OSError):
            manifest = {}

    failed = []
    for rk in targets:
        if rk not in REGIONS:
            print(f"Unknown region: {rk}")
            failed.append(rk)
            continue
        try:
            if not _build_region(rk, args.force, manifest):
                failed.append(rk)
        except subprocess.CalledProcessError as exc:
            print(f"    ⚠  tippecanoe failed for {rk}: {exc}")
            failed.append(rk)

    _TILES_DIR.mkdir(parents=True, exist_ok=True)
    _MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    print()
    if failed:
        print(f"Done with {len(failed)} failure(s): {', '.join(failed)}")
        return 1
    print(f"Done — {len(targets)} region(s) built. Manifest: {_MANIFEST.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
