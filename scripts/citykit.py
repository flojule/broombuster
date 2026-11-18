"""Shared steps of the city build scripts (scripts/build_*_geojson.py): schedule text to
sweep codes, county centerlines in the standard columns, zones laid onto blocks, and the
side entries the standard schema stores.

Codes: data/README.md. A side nobody is ticketed on gets COURTESY ("NT") with the
schedule in words; a side whose schedule the source does not give gets UNKNOWN
("POSTED": the driver reads the sign).
"""

from __future__ import annotations

import re

import geopandas
import pandas as pd

from broombuster import normalize

COURTESY = "NT"  # swept, nobody ticketed: the description is shown, never urgent
UNKNOWN = "POSTED"  # analysis.UNDATED_CODES: the day only the posted sign gives
UNKNOWN_DESC = "Schedule not published: check the posted sign"

_DAY_WORDS = (
    ("SU", r"sun(?:day)?s?"),
    ("TH", r"th(?:u(?:r(?:s)?)?)?(?:sday)?s?"),
    ("M", r"mon(?:day)?s?"),
    ("T", r"tue(?:s(?:day)?)?s?"),
    ("W", r"wed(?:s|nesday)?s?"),
    ("F", r"fri(?:day)?s?"),
    ("S", r"sat(?:urday)?s?"),
)
_DAY_RE = re.compile(r"\b(?:" + "|".join(f"(?P<{t}>{p})" for t, p in _DAY_WORDS) + r")\b", re.I)
_ORDER = ("M", "T", "W", "TH", "F", "S", "SU")
_ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_ORD_RE = re.compile(r"\b([1-5])(?:st|nd|rd|th)\b|\b(first|second|third|fourth|fifth)\b", re.I)
_BARE_RE = re.compile(r"(?<![\d:])([1-5])(?![\d:])")
_EVERY_RE = re.compile(r"\b(every|weekly|each week|all weeks)\b", re.I)
_ALT_RE = re.compile(r"\b(every other|alternat\w*|bi-?weekly|every 2|twice a month)\b", re.I)


def code_from_text(text) -> tuple[str, str] | None:
    """(code, desc) for schedule words: "1st & 3rd Tuesday" -> ("T13", "1st & 3rd Tue"),
    "Weekly (Mondays)" -> ("ME", "Every Mon"), "Monday,1" -> ("M1", "1st Mon"),
    "Weeks 2 & 4 Thursday" -> ("TH24", ...). None when no weekday, or a cadence the
    grammar cannot hold (every other week, bi-weekly)."""
    if not normalize.is_text(text) or _ALT_RE.search(text):
        return None
    days = []
    for m in _DAY_RE.finditer(text):
        tok = next(t for t, v in m.groupdict().items() if v)
        if tok not in days:
            days.append(tok)
    if not days:
        return None
    days.sort(key=_ORDER.index)
    rest = _DAY_RE.sub(" ", text)
    ords = {int(a) if a else _ORD_WORDS[b.lower()] for a, b in _ORD_RE.findall(rest)}
    if not ords and not _EVERY_RE.search(rest):
        ords = {int(n) for n in _BARE_RE.findall(rest)}
    dayword = ", ".join(normalize.WEEKDAYS[_ORDER.index(d)] for d in days)
    if not ords or ords >= {1, 2, 3, 4, 5}:
        return "".join(days) + "E", f"Every {dayword}"
    digits = "".join(str(n) for n in sorted(ords))
    return "".join(days) + digits, f"{normalize.pretty_ordinals(digits)} {dayword}"


_SHARED_AMPM_RE = re.compile(
    r"\b(\d{1,2}(?::\d\d)?)\s*(?:-|–|—|to)\s*(\d{1,2}(?::\d\d)?)\s*([AP])\.?\s*M\b\.?", re.I
)


def time_text(raw) -> str | None:
    """Posted window in the standard form ("9AM–12PM"), None when not given. "9-10:30 AM"
    takes the shared AM/PM ("9AM–10:30AM"); "NOON" is 12PM."""
    if not normalize.clean_text(raw):
        return None
    s = re.sub(r"\bnoon\b", "12PM", str(raw), flags=re.I)
    s = re.sub(r"\bmidnight\b", "12AM", s, flags=re.I)
    s = _SHARED_AMPM_RE.sub(lambda m: f"{m[1]}{m[3]}M–{m[2]}{m[3]}M", s)
    return normalize.time_display(s)


def side_entry(text, time=None, *, enforced=True) -> tuple[str | None, str | None, str | None]:
    """(code, desc, time) of one side from schedule words; (None, None, None) when not
    swept. An unreadable schedule is UNKNOWN; an unenforced one is COURTESY."""
    if not normalize.clean_text(text):
        return None, None, None
    hit = code_from_text(text)
    t = time_text(time)
    if not enforced:
        words = hit[1] if hit else normalize.clean_text(text)
        return COURTESY, f"{words}{f', {t}' if t else ''}, no tickets", None
    if hit is None:
        return UNKNOWN, UNKNOWN_DESC, t
    return hit[0], hit[1], t


# County street centerlines -> standard columns. `url` and `where` go in the city
# manifest's source.arcgis; the build reads the saved GeoJSON with centerlines().
COUNTY_CENTERLINES = {
    "alameda": {
        "url": "https://services5.arcgis.com/ROBnTHSNjoZ2Wm1P/arcgis/rest/services/Street_Centerlines/FeatureServer/0",
        "city_where": "MUNL='{code}' OR MUNR='{code}'",
        "name": "STREET",
        "ranges": ("FRADDL", "TOADDL", "FRADDR", "TOADDR"),
    },
    "santa_clara": {
        "url": "https://maps.santaclaracounty.gov/server/rest/services/opendata/SCCGISHUB/MapServer/21",
        "city_where": "(INCMUNI_L='{code}' OR INCMUNI_R='{code}') AND OWNER='PUB'"
        " AND ROADCLASS NOT IN ('Freeway', 'Ramp', 'Walkway', 'Expressway')",
        "name": "FULLRDNAME",
        "ranges": ("FROMADDR_L", "TOADDR_L", "FROMADDR_R", "TOADDR_R"),
    },
}


def centerlines(path, county: str) -> geopandas.GeoDataFrame:
    """A county centerline GeoJSON as STREET_NAME, STREET_KEY, L_F_ADD .. R_T_ADD, geometry
    (EPSG:4326); lines without a name are dropped."""
    spec = COUNTY_CENTERLINES[county]
    raw = geopandas.read_file(path).to_crs("EPSG:4326")
    name = raw[spec["name"]] if isinstance(spec["name"], str) else _join(raw, spec["name"])
    out = geopandas.GeoDataFrame(
        {"STREET_NAME": name.fillna("").astype(str).str.split().str.join(" ").str.upper()},
        geometry=raw.geometry,
        crs="EPSG:4326",
    )
    for col, src in zip(("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"), spec["ranges"], strict=True):
        n = pd.to_numeric(raw[src], errors="coerce")
        out[col] = n.where(n > 0)
    out["STREET_KEY"] = out["STREET_NAME"].map(normalize.street_name)
    return out[(out["STREET_NAME"] != "") & out.geometry.notna()].reset_index(drop=True)


def _join(raw, cols) -> pd.Series:
    """Name parts (prefix, name, type, suffix) joined with single spaces."""
    parts = [raw[c].fillna("").astype(str).str.strip() for c in cols]
    joined = parts[0]
    for p in parts[1:]:
        joined = joined + " " + p
    return joined.str.split().str.join(" ")


def zones_to_blocks(
    lines: geopandas.GeoDataFrame, zones: geopandas.GeoDataFrame, min_share: float = 0.5
) -> geopandas.GeoDataFrame:
    """Each line with the columns of the zone holding most of its length (at least
    min_share of it); lines in no zone are dropped. Both EPSG:4326."""
    utm = "EPSG:26910"
    ln = lines.to_crs(utm).reset_index(drop=True)
    zn = zones.to_crs(utm).reset_index(drop=True)
    zn["geometry"] = zn.geometry.make_valid()
    ln["_line"] = ln.index
    ln["_len"] = ln.geometry.length
    zn["_zone"] = zn.index
    pieces = geopandas.overlay(ln[["_line", "_len", "geometry"]], zn[["_zone", "geometry"]],
                               how="intersection", keep_geom_type=True)  # fmt: skip
    pieces["_share"] = pieces.geometry.length / pieces["_len"].where(pieces["_len"] > 0)
    best = pieces.sort_values("_share").groupby("_line").tail(1)
    best = best[best["_share"] >= min_share]
    zcols = [c for c in zones.columns if c != "geometry"]
    out = ln.drop(columns=["_len"]).merge(best[["_line", "_zone"]], on="_line")
    out = out.merge(zn[["_zone", *zcols]], on="_zone", suffixes=("", "_zone"))
    return out.drop(columns=["_line", "_zone"]).to_crs("EPSG:4326")


def lines_along(
    lines: geopandas.GeoDataFrame,
    segs: geopandas.GeoDataFrame,
    name: str | None = None,
    buffer_m: float = 12.0,
    min_share: float = 0.5,
) -> geopandas.GeoDataFrame:
    """Each line running along a segment of another layer (at least min_share of its length
    within buffer_m of it), with the segment's columns and `_src`, its index in `lines`.
    With `name` (the segments' street-name column) only where both names share their first
    word after a direction ("N PARK VICTORIA DR" and "PARK VICTORIA DR"). Both EPSG:4326."""
    zones = segs.to_crs("EPSG:26910")
    zones["geometry"] = zones.geometry.buffer(buffer_m)
    out = zones_to_blocks(lines.assign(_src=lines.index), zones.to_crs("EPSG:4326"), min_share)
    if name:
        out = out[out[name].map(_first_word) == out["STREET_NAME"].map(_first_word)]
    return out.reset_index(drop=True)


def _first_word(name) -> str:
    words = [w for w in str(name).upper().split() if w not in ("N", "S", "E", "W")]
    return {"MT": "MOUNT", "ST": "SAINT"}.get(words[0], words[0]) if words else ""


def blocks_by_name(lines: geopandas.GeoDataFrame, name) -> geopandas.GeoDataFrame:
    """Lines whose STREET_KEY equals the key of `name` ("Mission Street" matches MISSION ST)."""
    return lines[lines["STREET_KEY"] == normalize.street_name(str(name))]


def in_range(lines: geopandas.GeoDataFrame, lo: int, hi: int) -> pd.Series:
    """Rows whose address range (either side) overlaps [lo, hi]."""
    cols = lines[["L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"]]
    a, b = cols.min(axis=1), cols.max(axis=1)
    return (a <= hi) & (b >= lo)


def parity_sides(row) -> dict[str, str]:
    """{"L": "EVEN"|"ODD", "R": ...} from a line's address ranges (sides without a
    range are left out)."""
    out = {}
    for side, (f, t) in (("L", ("L_F_ADD", "L_T_ADD")), ("R", ("R_F_ADD", "R_T_ADD"))):
        nums = [int(v) for v in (row.get(f), row.get(t)) if pd.notna(v) and v]
        if nums and len({n % 2 for n in nums}) == 1:
            out[side] = "EVEN" if nums[0] % 2 == 0 else "ODD"
    return out
