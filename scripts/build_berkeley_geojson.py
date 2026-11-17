#!/usr/bin/env python3
"""
Build data/berkeley/StreetSweeping.geojson from Berkeley's PDF schedules and the
city's street centerlines (both fetched by scripts/rebuild_city_data.py berkeley).

Usage (from repo root):  python scripts/build_berkeley_geojson.py
Input:  the manifest's source.files PDFs, data/berkeley/centerlines.geojson
Output: data/berkeley/StreetSweeping.geojson
Requires: uv sync --extra scripts

A PDF row gives a street, a side, an address range and "1st Fri AM": the nth weekday
of every month, encoded as the recurring week-of-month code ("F1"). Each row lands on
the centerline blocks of that street whose address range it overlaps, on the side whose
address parity it has; a row whose side letter contradicts its numbers lands on the side
the letter names. Per the city map's mech_sweep class of the block:

| Class | Side with a PDF row | Side without |
|---|---|---|
| enforced (or blank, with a PDF row) | the PDF schedule | citykit.UNKNOWN |
| exempt (opted out: not swept) | "NS" | "NS" |
| Commercial, industry, excluded | citykit.COURTESY | "NS" |
"""

import math
import re
from pathlib import Path

import citykit
import geopandas
import pdf_city
import rebuild_city_data

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
_OPTED_OUT = "exempt"  # opted out of the program: not swept (council 2025-07-08 item 22)
_NO_SWEEP = "NS"  # analysis.NO_SWEEP_CODES
# Opted-out blocks the council put back since the map's last edit: (name, lo, hi).
_REINSTATED = {("REGENT", 2900, 2949)}  # council 2025-07-08 item 22; H-Z PDF 2025-09
# The PDFs: "even numbered addresses on the south and west sides".
_ODD_LETTERS = {"N", "E"}
_LETTER_DEG = {"E": 0, "N": 90, "W": 180, "S": 270}
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
    """PDF rows: {name, kind, lo, hi, letter, parities, entry, line}; entry = (code, desc, time)."""
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
                "letter": side.upper(),
                # A one-sided row covers the parity of its range; "E/W" covers both.
                "parities": {0, 1} if "/" in side else {lo % 2},
                "entry": (code, f"{nth.lower()} {day}", HOURS[ampm.upper()]),
                "line": line,
            }
        )
    return out


def _side_range(a, b):
    """(lo, hi) of one side's address range, None when the block has none."""
    a, b = int(a or 0), int(b or 0)
    return (min(a, b), max(a, b)) if a or b else None


def _facing(geom) -> tuple[set, set]:
    """Compass letters the (left, right) side of a line faces: those within 67.5 deg of
    the side's normal (two letters on a diagonal street)."""
    line = geom if geom.geom_type == "LineString" else geom.geoms[0]
    (x0, y0), (x1, y1) = line.coords[0][:2], line.coords[-1][:2]
    dx, dy = (x1 - x0) * math.cos(math.radians(y0)), y1 - y0
    left = math.degrees(math.atan2(dx, -dy))  # left normal (-dy, dx), 0 = east
    return tuple(
        {k for k, a in _LETTER_DEG.items() if abs((a - n + 180) % 360 - 180) < 67.5}
        for n in (left, left + 180)
    )


def _on_side(r: dict, parity: int, faces: set, opposite: set) -> bool:
    """Whether row r covers the block side of this address parity, facing `faces`."""
    if len(r["parities"]) == 2 or (r["letter"] in _ODD_LETTERS) == (r["lo"] % 2 == 1):
        return parity in r["parities"]
    # The letter contradicts the numbers: the side it faces, else the PDFs' parity rule.
    if r["letter"] in faces or r["letter"] in opposite:
        return r["letter"] in faces
    return parity == (r["letter"] in _ODD_LETTERS)


def _class(c, name: str) -> str:
    """The block's mech_sweep class; reinstated blocks count as enforced."""
    kind = (c.mech_sweep or "").strip()
    lo = min((int(v) for v in (c.F_ADDL, c.F_ADDR) if v), default=0)
    if kind == _OPTED_OUT and any(n == name and a <= lo <= b for n, a, b in _REINSTATED):
        return _ENFORCED
    return kind


def join(rows: list[dict], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per centerline block, schedule per side where a PDF
    row of the same street and side overlaps that side's address range."""
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)
    # "Acton" is Acton St once "Acton Crescent" has rows of its own.
    typed = {(r["name"], r["kind"]) for r in rows if r["kind"]}
    blocks = []
    for c in lines.itertuples():
        cname, _ = name_type(c.STR_NAM or "")
        cname = _ALIASES.get("".join((c.STR_NAM or "").upper().split()), cname)
        ckind = _TYPES.get((c.STR_TYP or "").strip().upper())
        sides = [(s, _side_range(a, b)) for s, a, b in (("L", c.F_ADDL, c.T_ADDL),
                                                        ("R", c.F_ADDR, c.T_ADDR))]  # fmt: skip
        mine = [
            r
            for r in by_name.get(cname, [])
            if (r["kind"] == ckind if r["kind"] else (cname, ckind) not in typed)
        ]
        sides = [(s, rng) for s, rng in sides if rng]
        span = (min(r[0] for _, r in sides), max(r[1] for _, r in sides)) if sides else None
        blocks.append((c, cname, mine, sides, span))

    def meets(r, span):
        """Row r shares house numbers with span, more than the one at a block's end."""
        if span is None:
            return False
        if r["lo"] == r["hi"] or span[0] == span[1]:
            return r["lo"] <= span[1] and span[0] <= r["hi"]
        return r["lo"] < span[1] and span[0] < r["hi"]

    # Rows overlapping no block of their street land on all its blocks when the street has
    # one block, or when none of its rows comes within 100 numbers of any (the PDF numbers
    # it differently; a row next to the blocks is a block the centerlines lack).
    streets: dict[tuple, list] = {}
    for b in blocks:
        if b[2] and b[4]:
            streets.setdefault((b[1], b[0].STR_TYP), []).append(b)
    loose: dict[int, set] = {}
    for bs in streets.values():
        mine = bs[0][2]
        apart = [r for r in mine if not any(meets(r, b[4]) for b in bs)]
        far = [r for r in mine if not any(meets(r, (b[4][0] - 100, b[4][1] + 100)) for b in bs)]
        if len(bs) == 1 or len(far) == len(mine):
            for r in apart:
                loose.setdefault(id(r), set()).update(id(b[0]) for b in bs)
    used: set[int] = set()  # rows on a swept block
    seen: set[int] = set()  # rows on any block
    touched: set[int] = set()  # rows overlapping a block side, kept there or outranked
    out = []
    unknown = 0
    for c, cname, mine, sides, span in blocks:
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
        faces = dict(zip("LR", _facing(c.geometry), strict=True))
        best = {}
        for s, rng in sides:
            hits = [
                r
                for r in mine
                if (id(c) in loose.get(id(r), ()) or meets(r, span))
                and _on_side(r, rng[0] % 2, faces[s], faces["R" if s == "L" else "L"])
            ]
            touched.update(id(r) for r in hits)
            if hits:
                # Most of the side's addresses, then the narrowest row, decide.
                best[s] = max(
                    hits,
                    key=lambda r: (
                        id(r) not in loose,
                        min(r["hi"], rng[1]) - max(r["lo"], rng[0]),
                        r["lo"] - r["hi"],
                    ),
                )
        seen.update(id(r) for r in best.values())
        kind = _class(c, cname)
        if not kind and best:
            kind = _ENFORCED  # blank on the map, listed in the PDF (Bancroft Way 2800)
        for s, rng in sides:
            col = "EVEN" if rng[0] % 2 == 0 else "ODD"
            r = best.get(s)
            if kind == _ENFORCED and r:
                used.add(id(r))
                entry = r["entry"]
            elif kind == _ENFORCED:
                unknown += 1
                entry = (citykit.UNKNOWN, citykit.UNKNOWN_DESC, None)
            elif kind and kind != _OPTED_OUT and r:
                used.add(id(r))
                _, desc, time = r["entry"]
                entry = (citykit.COURTESY, f"{desc}, {time}, no tickets", None)
            elif kind == _OPTED_OUT:
                entry = (_NO_SWEEP, "No sweeping (opted out)", None)
            elif kind:
                entry = (_NO_SWEEP, f"Not enforced ({kind})", None)
            else:
                continue
            rec[f"DAY_{col}"], rec[f"DESC_{col}"], rec[f"TIME_{col}"] = entry
        out.append(rec)
    print(f"  {unknown} enforced sides with no PDF row: {citykit.UNKNOWN} (check the sign)")
    print(f"  {len(seen - used)} PDF rows only on opted-out blocks")
    print(f"  {len(touched - seen)} PDF rows outranked by a narrower or longer row")
    unmatched = [r for r in rows if id(r) not in touched]
    if unmatched:
        print(f"  {len(unmatched)} of {len(rows)} PDF rows on no block:")
        for r in unmatched:
            print(f"     {r['line']}")
    return geopandas.GeoDataFrame(out, crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "berkeley"
    pdfs = rebuild_city_data.input_files("berkeley")  # the manifest's PDFs, not every *.pdf here
    centerlines = data_dir / "centerlines.geojson"
    missing = [p.name for p in [*pdfs, centerlines] if not p.exists()]
    if not pdfs or missing:
        raise FileNotFoundError(
            f"missing {missing or 'source.files'}; run rebuild_city_data.py berkeley"
        )
    rows = rows_from(pdf_city.pdf_lines(pdfs))
    print(f"  {len(rows)} rows parsed from {len(pdfs)} PDF(s)")
    gdf = join(rows, geopandas.read_file(centerlines))
    pdf_city.write(gdf, data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
