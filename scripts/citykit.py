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
    "san_mateo": {
        "url": "https://gis.smcgov.org/maps/rest/services/ISD/SMC_COUNTY_GIS/MapServer/4",
        "city_where": "incmuni_l='{code}' OR incmuni_r='{code}'",
        "name": "fullrdname",
        "ranges": ("fromaddr_l", "toaddr_l", "fromaddr_r", "toaddr_r"),
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


def zone_blocks(lines: geopandas.GeoDataFrame, zones: geopandas.GeoDataFrame, entry, min_share=0.5):
    """Standard-schema rows: lines in a zone (zones_to_blocks) take entry(zone row) ->
    (code, desc, time) on both sides; zones whose entry has no code are left out."""
    entries = [entry(r) for _, r in zones.iterrows()]
    cols = {
        f"{col}_{side}": [e[i] for e in entries]
        for side in ("EVEN", "ODD")
        for i, col in enumerate(("DAY", "DESC", "TIME"))
    }
    zones = zones[["geometry"]].assign(**cols)
    out = zones_to_blocks(lines, zones[zones["DAY_EVEN"].notna()], min_share)
    return out.drop(columns="STREET_KEY").reset_index(drop=True)


def blocks_by_name(lines: geopandas.GeoDataFrame, name) -> geopandas.GeoDataFrame:
    """Lines whose STREET_KEY equals the key of `name` ("Mission Street" matches MISSION ST)."""
    return lines[lines["STREET_KEY"] == normalize.street_name(str(name))]


def in_range(lines: geopandas.GeoDataFrame, lo: int, hi: int) -> pd.Series:
    """Rows whose address range (either side) overlaps [lo, hi]."""
    cols = lines[["L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"]]
    a, b = cols.min(axis=1), cols.max(axis=1)
    return (a <= hi) & (b >= lo)


def address_blocks(
    lines: geopandas.GeoDataFrame, points: pd.DataFrame, entry
) -> geopandas.GeoDataFrame:
    """Standard-schema rows from address points (STREET_KEY, NUM, SCHEDULE): each block side
    takes entry(the most common SCHEDULE of the points on its street, inside its address
    range, of its parity) -> (code, desc, time); blocks with neither side swept are dropped."""
    pts = points.assign(NUM=pd.to_numeric(points["NUM"], errors="coerce"))
    pts = pts.dropna(subset=["NUM", "SCHEDULE"])
    groups = dict(iter(pts.groupby("STREET_KEY")))
    cols = lines[["L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"]]
    out = lines.copy()
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"] = out[f"DESC_{side}"] = out[f"TIME_{side}"] = None
    for i, key, lo, hi in zip(lines.index, lines["STREET_KEY"], cols.min(axis=1),
                              cols.max(axis=1), strict=True):  # fmt: skip
        grp = groups.get(key)
        if grp is None or pd.isna(lo):
            continue
        hit = grp[(grp["NUM"] >= lo) & (grp["NUM"] <= hi)]
        for side, par in (("EVEN", 0), ("ODD", 1)):
            vals = hit.loc[hit["NUM"] % 2 == par, "SCHEDULE"]
            if not vals.empty:
                cells = [f"DAY_{side}", f"DESC_{side}", f"TIME_{side}"]
                out.loc[i, cells] = entry(vals.mode().iloc[0])
    out = out[out["DAY_EVEN"].notna() | out["DAY_ODD"].notna()]
    return out.drop(columns="STREET_KEY").reset_index(drop=True)


def parity_sides(row) -> dict[str, str]:
    """{"L": "EVEN"|"ODD", "R": ...} from a line's address ranges (sides without a
    range are left out)."""
    out = {}
    for side, (f, t) in (("L", ("L_F_ADD", "L_T_ADD")), ("R", ("R_F_ADD", "R_T_ADD"))):
        nums = [int(v) for v in (row.get(f), row.get(t)) if pd.notna(v) and v]
        if nums and len({n % 2 for n in nums}) == 1:
            out[side] = "EVEN" if nums[0] % 2 == 0 else "ODD"
    return out
