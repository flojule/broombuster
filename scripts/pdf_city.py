"""Shared steps of the city build scripts (scripts/build_*_geojson.py): PDF text
lines, street-name clean-up, and writing the standard-schema GeoJSON that
data_loader.build_city_fgb normalises."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import geopandas
import pdfplumber

from broombuster import analysis

# "Mon" -> "M", ..., "Sun" -> "SU": the sweep-code token for a weekday name.
WEEKDAY_TOKEN = {disp: tok for tok, (_rank, disp) in analysis.WEEKDAY_CODES.items()}

# City GIS street-type abbreviations -> the usual ones (last word of a name).
_TYPE_FIX = {"AV": "AVE", "WY": "WAY", "PW": "PKWY", "LA": "LN", "BL": "BLVD", "TR": "TER"}
_TYPE_FIX |= {"CR": "CRES", "CS": "CRES", "CI": "CIR"}


def street_label(name: str) -> str:
    """Upper-case name with its type abbreviation normalised: "DWIGHT WY" -> "DWIGHT WAY"."""
    toks = name.upper().split()
    if len(toks) > 1:
        toks[-1] = _TYPE_FIX.get(toks[-1], toks[-1])
    return " ".join(toks)


def pdf_lines(paths: Iterable[Path]) -> Iterator[str]:
    """Every stripped text line of every page of the given PDFs."""
    for path in paths:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    yield line.strip()


def write(gdf: geopandas.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    print(f"  {len(gdf)} segments → {path}")
