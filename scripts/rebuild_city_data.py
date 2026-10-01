#!/usr/bin/env python3
"""Rebuild cities' .fgb from the raw source in their manifest `source:` block.

    python scripts/rebuild_city_data.py [city ...]   # default: every city
    python scripts/build_pmtiles.py                  # then rebuild the map tiles

source: local_path (raw input), url (downloaded to local_path), files (extra
inputs; URLs are downloaded next to local_path), build (script producing
local_path), notes (how to get inputs by hand).
"""

import subprocess
import sys
from pathlib import Path

import requests

from broombuster import data_loader
from broombuster.cities import CITIES
from broombuster.config import REPO_ROOT

_ROOT = Path(REPO_ROOT)


def _download(url: str, dest: Path) -> None:
    print(f"    GET {url}")
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)


def rebuild(city_key: str) -> None:
    city = CITIES[city_key]
    src = city["source"]
    local = _ROOT / src["local_path"]
    print(f"{city['name']}")
    if src.get("url"):
        _download(src["url"], local)
    for f in src.get("files", []):
        if f.startswith(("http://", "https://")):
            _download(f, local.parent / f.rsplit("/", 1)[-1])
        elif not (_ROOT / f).exists():
            sys.exit(f"missing {f}:\n{src.get('notes', '')}")
    if src.get("build"):
        subprocess.run([sys.executable, str(_ROOT / src["build"])], cwd=_ROOT, check=True)
    if not local.exists():
        sys.exit(f"missing {src['local_path']}:\n{src.get('notes', '')}")
    gdf = data_loader.build_city_fgb(city_key)
    print(f"    {city['fgb_path']}: {len(gdf):,} rows")


if __name__ == "__main__":
    for key in sys.argv[1:] or CITIES:
        rebuild(key)
