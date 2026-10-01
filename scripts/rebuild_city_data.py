#!/usr/bin/env python3
"""
Rebuild a city's normalised FlatGeobuf (.fgb) from its raw source.

The repository ships only the .fgb runtime artifacts. Each city manifest's
`source:` block says how to get the raw input back:

  url        download straight to source.local_path (SF, Chicago)
  files      extra inputs: URLs are downloaded into data/<city>/, local paths
             must already be present (manual downloads; see notes)
  sha256     expected hashes of those inputs (a mismatch is reported)
  build      script turning the inputs into source.local_path (PDF cities)
  notes      where to get the data by hand

Usage (from repo root):
    python scripts/rebuild_city_data.py                   # every city
    python scripts/rebuild_city_data.py oakland           # one city
    python scripts/rebuild_city_data.py san_francisco --force   # redownload

After rebuilding, regenerate the map tiles: python scripts/build_pmtiles.py
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import requests

from broombuster import data_loader
from broombuster.cities import CITIES
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, force: bool) -> None:
    if dest.exists() and not force:
        print(f"    {dest.relative_to(_ROOT)} already present  (use --force to redownload)")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"    GET {url}")
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"    → {dest.relative_to(_ROOT)}  ({dest.stat().st_size / 1024:.1f} KB)")


def _fetch_inputs(city_key: str, src: dict, force: bool) -> bool:
    """Download / verify the raw inputs and run the build step; True when ready."""
    local = _ROOT / src["local_path"]
    if src.get("url"):
        _download(src["url"], local, force)
    for f in src.get("files", []):
        if f.startswith(("http://", "https://")):
            _download(f, local.parent / f.rsplit("/", 1)[-1], force)
        elif not (_ROOT / f).exists():
            print(f"    ⚠  missing {f} — manual download required:\n{src['notes']}")
            return False
    for rel, expected in (src.get("sha256") or {}).items():
        p = _ROOT / rel
        if p.exists() and _sha256(p) != expected:
            print(f"    ⚠  SHA mismatch for {rel}: the upstream source has changed")
    if src.get("build"):
        print(f"    running {src['build']} …")
        if subprocess.run([sys.executable, str(_ROOT / src["build"])], cwd=_ROOT).returncode:
            print(f"    ⚠  {src['build']} failed")
            return False
    if not local.exists():
        print(f"    ⚠  missing {src['local_path']} — manual download required:\n{src['notes']}")
        return False
    return True


def _rebuild_one(city_key: str, force: bool) -> bool:
    if city_key not in CITIES:
        print(f"  Unknown city key: {city_key}")
        return False
    city = CITIES[city_key]
    print(f"\n{city['name']}  (schema={city['schema']})")
    if not _fetch_inputs(city_key, city["source"], force):
        return False
    print("    normalising → .fgb …")
    try:
        gdf = data_loader.build_city_fgb(city_key)
    except Exception as exc:  # noqa: BLE001 — report and continue with the next city
        print(f"    ⚠  build failed: {exc}")
        return False
    size_mb = (_ROOT / city["fgb_path"]).stat().st_size / 1_048_576
    print(f"    ✓ {city['fgb_path']}  ({len(gdf):,} rows, {size_mb:.1f} MB)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cities", nargs="*", help="City keys to rebuild (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="Redownload raw inputs even if present locally")
    args = ap.parse_args()

    failed = [k for k in (args.cities or list(CITIES)) if not _rebuild_one(k, args.force)]
    print()
    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1
    print("All requested cities rebuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
