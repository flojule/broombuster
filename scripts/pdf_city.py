"""Shared steps for cities whose sweeping schedule is a PDF table.

Each build script parses its PDF rows into blocks, then this module fetches the
city's named streets from OpenStreetMap (Overpass), joins every block to the
union of the matching ways, and writes the standard-schema GeoJSON that
data_loader.build_city_fgb normalises.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import geopandas
import pdfplumber
import requests
from shapely.geometry import LineString

from broombuster import analysis, normalize

# "Mon" -> "M", ..., "Sun" -> "SU": the sweep-code token for a weekday name.
WEEKDAY_TOKEN = {disp: tok for tok, (_rank, disp) in analysis.WEEKDAY_CODES.items()}

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_HIGHWAYS = "residential|primary|secondary|tertiary|unclassified|living_street"


def pdf_lines(paths: Iterable[Path]) -> Iterator[str]:
    """Every stripped text line of every page of the given PDFs."""
    for path in paths:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    yield line.strip()


def fetch_osm_streets(city: str) -> geopandas.GeoDataFrame:
    """Named drivable streets inside the city's admin boundary, with STREET_KEY."""
    query = f"""
    [out:json][timeout:180];
    area["name"="{city}"]["admin_level"="8"]["boundary"="administrative"]->.city;
    (way["highway"~"^({_HIGHWAYS})$"]["name"](area.city););
    out geom;
    """
    print(f"  Querying Overpass API for {city} …")
    resp = requests.post(_OVERPASS_URL, data={"data": query}, timeout=200)
    resp.raise_for_status()
    rows = [
        {"geometry": LineString([(p["lon"], p["lat"]) for p in el["geometry"]]),
         "key": normalize.street_name(el.get("tags", {}).get("name", ""))}
        for el in resp.json().get("elements", [])
        if len(el.get("geometry", [])) >= 2
    ]
    print(f"  {len(rows)} street ways fetched")
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def join_blocks(blocks: dict, streets: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per block, drawn as the union of its street's ways.

    `blocks` maps any key to {"street": NAME, "even": (code, desc, time) | None,
    "odd": ... | None, "addr": (l_from, l_to, r_from, r_to)}. A street matches
    on its canonical key, else on a key prefix; unmatched blocks are dropped.
    """
    rows, dropped = [], 0
    for b in blocks.values():
        key = normalize.street_name(b["street"])
        hits = streets[streets["key"] == key]
        if hits.empty:
            hits = streets[streets["key"].str.startswith(key)]
        if hits.empty:
            dropped += 1
            continue
        even = b["even"] or (None, None, None)
        odd = b["odd"] or (None, None, None)
        lf, lt, rf, rt = (float(a) for a in b["addr"])
        rows.append({
            "geometry": hits.geometry.union_all(),
            "STREET_NAME": b["street"],
            "DAY_EVEN": even[0], "DESC_EVEN": even[1], "TIME_EVEN": even[2],
            "DAY_ODD": odd[0], "DESC_ODD": odd[1], "TIME_ODD": odd[2],
            "L_F_ADD": lf, "L_T_ADD": lt, "R_F_ADD": rf, "R_T_ADD": rt,
        })
    if dropped:
        print(f"  ⚠  {dropped} blocks had no matching OSM geometry and were dropped.")
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def write(gdf: geopandas.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    print(f"  {len(gdf)} segments → {path}")
