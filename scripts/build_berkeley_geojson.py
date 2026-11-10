#!/usr/bin/env python3
"""
Build data/berkeley/StreetSweeping.geojson from Berkeley's PDF schedules and the
city's street centerlines (both fetched by scripts/rebuild_city_data.py berkeley).

Usage (from repo root):  python scripts/build_berkeley_geojson.py
Input:  data/berkeley/*.pdf, data/berkeley/centerlines.geojson
Output: data/berkeley/StreetSweeping.geojson
Requires: uv sync --extra scripts

A PDF row gives a street, a side, an address range and "1st Fri AM": the nth weekday
of every month, encoded as the recurring week-of-month code ("F1"). Each row lands on
the centerline blocks of that street whose address range it overlaps, on the side whose
address parity it has. Blocks the city's map marks as not enforced get the no-sweep code "NS".
"""

import re
from pathlib import Path

import geopandas
import pdf_city

from broombuster.config import REPO_ROOT

# e.g. "61 Acroft Ct S 1400 1498 1st Fri AM Acton Terminus"; "E/W" = both sides.
_ROW_RE = re.compile(
    r"^(\d+)\s+(.+?)\s+([NSEW](?:/[NSEW])?)\s+(\d+)(?:\s+(\d+))?\s+"
    r"(1st|2nd|3rd|4th)\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+(AM|PM)\b",
    re.IGNORECASE,
)
# The signs' hours (the PDFs say only AM / PM); read off street signs, see data/README.md.
HOURS = {"AM": "9AM–12PM", "PM": "12:30PM–3:30PM"}
# mech_sweep of the city's map: only these blocks are signed and ticketed.
_ENFORCED = "enforced"
_NOT_ENFORCED = "NS"  # analysis.NO_SWEEP_CODES
# Street types, PDF and centerline spellings -> one form.
_TYPES = {
    **dict.fromkeys(("AV", "AVE", "AVENUE"), "AVE"),
    **dict.fromkeys(("ST", "STREET"), "ST"),
    **dict.fromkeys(("CT", "COURT"), "CT"),
    **dict.fromkeys(("RD", "ROAD"), "RD"),
    **dict.fromkeys(("WY", "WAY"), "WAY"),
    **dict.fromkeys(("DR", "DRIVE"), "DR"),
    **dict.fromkeys(("PL", "PLACE"), "PL"),
    **dict.fromkeys(("CR", "CS", "CRESC", "CRESCENT"), "CRES"),
    **dict.fromkeys(("CI", "CIR", "CIRCLE"), "CIR"),
    **dict.fromkeys(("BL", "BLVD"), "BLVD"),
    **dict.fromkeys(("LA", "LN", "LANE"), "LN"),
    **dict.fromkeys(("TER", "TERR", "TERRACE"), "TER"),
}
# PDF spellings the centerlines write differently (compared without spaces).
_ALIASES = {
    "MLKING": "MARTINLUTHERKINGJR",
    "MARTINLUTHERKINGJ": "MARTINLUTHERKINGJR",
    "MATTHEWS": "MATHEWS",
    "NORTHVALLEY": "VALLEY",
}


def name_type(raw: str) -> tuple[str, str | None]:
    """("ACTON", "CRES") for "Acton Crescent"; the type is None when not given."""
    toks = raw.upper().replace(".", "").split()
    kind = _TYPES.get(toks[-1]) if len(toks) > 1 else None
    name = "".join(toks[:-1] if kind else toks)
    return _ALIASES.get(name, name), kind


def rows_from(lines) -> list[dict]:
    """PDF rows: {key, lo, hi, parities, entry}, entry = (code, desc, time)."""
    out = []
    for line in lines:
        m = _ROW_RE.match(line)
        if not m:
            continue
        _, street, side, lo, hi, nth, day, ampm = m.groups()
        lo, hi = sorted((int(lo), int(hi or lo)))
        day = day.capitalize()
        code = pdf_city.WEEKDAY_TOKEN[day] + nth[0]
        name, kind = name_type(street)
        out.append(
            {
                "name": name,
                "kind": kind,
                "lo": lo,
                "hi": hi,
                # A one-sided row covers the parity of its range; "E/W" covers both.
                "parities": {0, 1} if "/" in side else {lo % 2},
                "entry": (code, f"{nth.lower()} {day}", HOURS[ampm.upper()]),
            }
        )
    return out


def _side_range(a, b):
    """(lo, hi) of one side's address range, None when the block has none."""
    a, b = int(a or 0), int(b or 0)
    return (min(a, b), max(a, b)) if a or b else None


def join(rows: list[dict], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per centerline block, schedule per side where a PDF
    row of the same street and parity overlaps that side's address range."""
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)
    # "Acton" is Acton St once "Acton Crescent" has rows of its own.
    typed = {(r["name"], r["kind"]) for r in rows if r["kind"]}
    used: set[int] = set()
    out = []
    for c in lines.itertuples():
        name = f"{c.STR_NAM or ''} {c.STR_TYP or ''}".strip().upper()
        if not name:
            continue
        rec = {
            "geometry": c.geometry,
            "STREET_NAME": pdf_city.street_label(name),
            "L_F_ADD": float(c.F_ADDL or 0) or None,
            "L_T_ADD": float(c.T_ADDL or 0) or None,
            "R_F_ADD": float(c.F_ADDR or 0) or None,
            "R_T_ADD": float(c.T_ADDR or 0) or None,
        }
        cname, _ = name_type(c.STR_NAM or "")
        cname = _ALIASES.get("".join((c.STR_NAM or "").upper().split()), cname)
        ckind = _TYPES.get((c.STR_TYP or "").strip().upper())
        kind = (c.mech_sweep or "").strip().lower()
        if kind and kind != _ENFORCED:
            # Swept or not, nobody is ticketed here: the no-sweep code, with the city's class.
            for side in ("EVEN", "ODD"):
                rec[f"DAY_{side}"], rec[f"DESC_{side}"] = _NOT_ENFORCED, f"Not enforced ({kind})"
        if kind == _ENFORCED:
            for rng in (_side_range(c.F_ADDL, c.T_ADDL), _side_range(c.F_ADDR, c.T_ADDR)):
                if not rng:
                    continue
                side = "EVEN" if rng[0] % 2 == 0 else "ODD"
                hits = [
                    r
                    for r in by_name.get(cname, [])
                    if (r["kind"] == ckind if r["kind"] else (cname, ckind) not in typed)
                    and rng[0] % 2 in r["parities"]
                    and r["lo"] <= rng[1]
                    and rng[0] <= r["hi"]
                ]
                if not hits:
                    continue
                # Most of the block's addresses decide between rows meeting inside it.
                best = max(hits, key=lambda r: min(r["hi"], rng[1]) - max(r["lo"], rng[0]))
                used.add(id(best))
                rec[f"DAY_{side}"], rec[f"DESC_{side}"], rec[f"TIME_{side}"] = best["entry"]
        out.append(rec)
    unused = [r for r in rows if id(r) not in used]
    if unused:
        print(f"  ⚠  {len(unused)} of {len(rows)} PDF rows matched no enforced block.")
    return geopandas.GeoDataFrame(out, crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "berkeley"
    pdfs = sorted(data_dir.glob("*.pdf"))
    centerlines = data_dir / "centerlines.geojson"
    if not pdfs or not centerlines.exists():
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py berkeley")
    rows = rows_from(pdf_city.pdf_lines(pdfs))
    print(f"  {len(rows)} rows parsed from {len(pdfs)} PDF(s)")
    gdf = join(rows, geopandas.read_file(centerlines))
    pdf_city.write(gdf, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
