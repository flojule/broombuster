#!/usr/bin/env python3
"""
Rebuild a city's normalised FlatGeobuf (.fgb) from its raw source files.

The repository ships only the .fgb runtime artifacts, not the raw shapefiles,
PDFs, or intermediate GeoJSONs they were built from. Use this script when a
.fgb is missing or stale and needs to be regenerated.

Usage (from repo root):

    # Rebuild every city's .fgb that has a known source:
    python scripts/rebuild_city_data.py

    # Rebuild a specific city:
    python scripts/rebuild_city_data.py oakland

    # Rebuild and force-redownload (ignore SHA cache):
    python scripts/rebuild_city_data.py san_francisco --force

What it does, per city:
  1. Reads data/sources.yaml to find the raw input URL(s) and expected SHA256.
  2. For URL-backed cities (SF, Chicago): downloads the GeoJSON directly.
  3. For PDF-driven cities (Berkeley, Alameda): downloads the PDFs, then
     invokes the existing scripts/build_<city>_geojson.py to produce the
     intermediate GeoJSON.
  4. For shapefile cities (Oakland): expects the shapefile to be present in
     data/oakland/ already (no public direct-download URL). Prints a clear
     instruction otherwise.
  5. Calls data_loader.load_city_data(city_key, force_refresh=True), which
     normalises the raw input and writes the .fgb.
  6. Verifies the .fgb opens and contains rows.

Cities whose `url` is null and whose raw files are absent are skipped with a
clear message — the user has to drop the file in by hand.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import requests
import yaml

# Allow importing from src/
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT / "src"))

from broombuster import data_loader  # noqa: E402  (after sys.path insert)
from broombuster.cities import CITIES  # noqa: E402

_SOURCES_YAML = _ROOT / "data" / "sources.yaml"


def _load_sources() -> dict:
    with open(_SOURCES_YAML) as fh:
        return yaml.safe_load(fh)["cities"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_to(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"    GET {url}")
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"    → {dest.relative_to(_ROOT)}  ({dest.stat().st_size / 1024:.1f} KB)")


def _verify_sha(path: Path, expected_sha: str | None) -> None:
    if not expected_sha:
        return
    actual = _sha256(path)
    if actual != expected_sha:
        print(
            f"    ⚠  SHA mismatch for {path.name}\n"
            f"       expected: {expected_sha}\n"
            f"       actual:   {actual}\n"
            f"       (the upstream source has changed; run with --force to accept.)"
        )


def _ensure_oakland_raw(city_meta: dict, force: bool) -> bool:
    """Oakland has no direct-download URL. Verify the shapefile sidecars exist."""
    missing = []
    for rel in city_meta["files"]:
        p = _ROOT / rel
        if not p.exists():
            missing.append(rel)
    if missing:
        print("    ⚠  Oakland shapefile missing — manual download required:")
        print(f"       {city_meta['notes'].strip()}")
        for m in missing:
            print(f"       Missing: {m}")
        return False
    return True


def _ensure_url_geojson_raw(city_key: str, city_meta: dict, force: bool) -> bool:
    """Cities served by a single direct GeoJSON URL (SF, Chicago)."""
    url = city_meta.get("url")
    if not url:
        return False
    # Use the exact local_path the runtime expects.
    local_rel = CITIES[city_key]["local_path"]
    local_path = _ROOT / local_rel
    if local_path.exists() and not force:
        print(f"    {local_rel} already present  (use --force to redownload)")
        return True
    _download_to(url, local_path)
    return True


def _ensure_berkeley_raw(city_meta: dict, force: bool) -> bool:
    """Berkeley has three PDFs at known URLs."""
    pdf_dir = _ROOT / "data" / "berkeley"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    sha_map = city_meta.get("sha256") or {}
    ok = True
    for url in city_meta["files"]:
        # The build script reads PDFs by basename; preserve original filenames.
        fname = url.rsplit("/", 1)[-1]
        dest = pdf_dir / fname
        if dest.exists() and not force:
            print(f"    {fname} already present  (use --force to redownload)")
        else:
            try:
                _download_to(url, dest)
            except requests.HTTPError as exc:
                print(f"    ⚠  could not fetch {url}: {exc}")
                ok = False
                continue
        # Match SHA against the path key in sources.yaml (also basename-only).
        expected = sha_map.get(f"data/berkeley/{fname}")
        _verify_sha(dest, expected)

    # Run the existing PDF→GeoJSON build script.
    script = _ROOT / "scripts" / "build_berkeley_geojson.py"
    print(f"    running {script.name} …")
    res = subprocess.run([sys.executable, str(script)], cwd=_ROOT)
    if res.returncode != 0:
        print(f"    ⚠  {script.name} exited with {res.returncode}")
        ok = False
    return ok


def _ensure_alameda_raw(city_meta: dict, force: bool) -> bool:
    """Alameda PDF has no stable URL — must be present locally."""
    pdf = _ROOT / "data" / "alameda" / "street-sweeping-schedule.pdf"
    if not pdf.exists():
        print("    ⚠  Alameda PDF missing — manual download required:")
        print(f"       {city_meta['notes'].strip()}")
        return False
    sha_map = city_meta.get("sha256") or {}
    expected = sha_map.get("data/alameda/street-sweeping-schedule.pdf")
    _verify_sha(pdf, expected)

    script = _ROOT / "scripts" / "build_alameda_geojson.py"
    print(f"    running {script.name} …")
    res = subprocess.run([sys.executable, str(script)], cwd=_ROOT)
    if res.returncode != 0:
        print(f"    ⚠  {script.name} exited with {res.returncode}")
        return False
    return True


def _rebuild_one(city_key: str, sources: dict, force: bool) -> bool:
    if city_key not in CITIES:
        print(f"  Unknown city key: {city_key}")
        return False
    if city_key not in sources:
        print(f"  {city_key}: no entry in data/sources.yaml — skipping")
        return False

    city = CITIES[city_key]
    schema = city.get("schema")
    meta = sources[city_key]
    print(f"\n{city['name']}  (schema={schema})")

    # Step 1: ensure the raw input exists (download or build via PDF script).
    if schema == "oakland":
        ok = _ensure_oakland_raw(meta, force)
    elif schema == "sf" or schema == "chicago":
        ok = _ensure_url_geojson_raw(city_key, meta, force)
    elif schema == "berkeley":
        ok = _ensure_berkeley_raw(meta, force)
    elif schema == "alameda":
        ok = _ensure_alameda_raw(meta, force)
    else:
        print(f"  ⚠  unknown schema {schema!r} — skipping")
        return False

    if not ok:
        return False

    # Step 2: run the data_loader normaliser, which writes the .fgb.
    print("    normalising → .fgb …")
    try:
        gdf = data_loader.load_city_data(city_key, force_refresh=True)
    except Exception as exc:
        print(f"    ⚠  load_city_data failed: {exc}")
        return False

    fgb_path = _ROOT / city["fgb_path"]
    if not fgb_path.exists():
        print(f"    ⚠  expected {fgb_path.relative_to(_ROOT)} but file is missing")
        return False
    size_mb = fgb_path.stat().st_size / 1_048_576
    print(f"    ✓ {fgb_path.relative_to(_ROOT)}  ({len(gdf):,} rows, {size_mb:.1f} MB)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cities", nargs="*",
                    help="City keys to rebuild (default: every city in sources.yaml)")
    ap.add_argument("--force", action="store_true",
                    help="Redownload raw inputs even if present locally")
    args = ap.parse_args()

    sources = _load_sources()
    targets = args.cities or list(sources.keys())

    failed = []
    for k in targets:
        if not _rebuild_one(k, sources, args.force):
            failed.append(k)

    print()
    if failed:
        print(f"Done with {len(failed)} failure(s): {', '.join(failed)}")
        return 1
    print(f"Done — {len(targets)} city/cities rebuilt successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
