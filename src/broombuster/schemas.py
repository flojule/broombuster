"""Per-city source-format normalisers; each returns the standard schema
documented in data_loader.py. SCHEMA_PROFILES maps a manifest `schema` name
to its normaliser."""

import math

import geopandas
import numpy as np
import pandas as pd

from broombuster import normalize

SCHEDULE_COLS = ("DAY_EVEN", "DAY_ODD", "DESC_EVEN", "DESC_ODD", "TIME_EVEN", "TIME_ODD")
ADDR_COLS = ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")
# Standard output schema, in FGB column order.
SCHEMA_COLS = ("STREET_NAME", "STREET_KEY", "STREET_DISPLAY", *SCHEDULE_COLS, *ADDR_COLS,
               "SIDE_EVEN", "SIDE_ODD")

# ---------------------------------------------------------------------------
# Oakland
# ---------------------------------------------------------------------------

def _add_key_and_display(out: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Populate STREET_KEY and STREET_DISPLAY from STREET_NAME (in place)."""
    def _key(v):
        return normalize.street_name(v) if isinstance(v, str) else ""

    def _disp(v):
        return normalize.street_display(v) if isinstance(v, str) else ""

    out["STREET_KEY"] = out["STREET_NAME"].map(_key)
    out["STREET_DISPLAY"] = out["STREET_NAME"].map(_disp)
    return out


def _normalise_oakland(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """
    Oakland shapefile already uses Oakland codes for DAY_EVEN / DAY_ODD.
    We only need to build STREET_NAME and create the DESC_* / TIME_* aliases.
    """
    out = gdf.copy()
    out["STREET_NAME"] = (
        out["NAME"].fillna("").str.strip()
        + " "
        + out["TYPE"].fillna("").str.strip()
    ).str.strip().str.upper()
    # Add canonical key and readable short display form so downstream code
    # doesn't need to re-normalise on every access.
    _add_key_and_display(out)
    out["DESC_EVEN"] = out.get("DescDayEve")
    out["DESC_ODD"]  = out.get("DescDayOdd")
    out["TIME_EVEN"] = out.get("DescTimeEv")
    out["TIME_ODD"]  = out.get("DescTimeOd")
    # DAY_EVEN, DAY_ODD, L_F_ADD, L_T_ADD, R_F_ADD, R_T_ADD already correct.
    return out


# ---------------------------------------------------------------------------
# San Francisco  (DataSF – Street Sweeping Schedule, yhqp-riqs)
# ---------------------------------------------------------------------------
# Key columns (from DataSF metadata):
#   corridor       street name  e.g. "MARKET ST"
#   cnn            centerline id; one geometry (and direction) per cnn
#   cnnrightleft   "L" / "R": side of the centerline's digitized direction
#   blockside      compass side ("North", "SouthEast", ...); "ODD" / "EVEN" /
#                  "BOTH" also accepted
#   week_day       integer 1–7  (1 = Monday … 7 = Sunday)
#   from_hour      integer hour (24-h)
#   to_hour        integer hour (24-h)
#   week_1_of_month … week_5_of_month   integer 1 or 0

_SF_DAY_MAP = {
    "1": "M",  "2": "T",  "3": "W",  "4": "TH", "5": "F", "6": "S", "7": "SU",
    "monday": "M", "tuesday": "T", "wednesday": "W", "thursday": "TH",
    "friday": "F", "saturday": "S", "sunday": "SU",
    # 3-4 letter abbreviations used by DataSF (e.g. "Tues", "Thurs")
    "mon": "M", "tue": "T", "tues": "T", "wed": "W", "weds": "W",
    "thu": "TH", "thur": "TH", "thurs": "TH",
    "fri": "F", "sat": "S", "sun": "SU",
}

_SF_DAY_LABEL = {
    "M": "Mon", "T": "Tue", "W": "Wed", "TH": "Thu",
    "F": "Fri", "S": "Sat", "SU": "Sun",
}


def _sf_desc(code, time) -> str:
    if not isinstance(code, str):
        return "N/A"
    letter = code.rstrip("0123456789E")
    day_label = _SF_DAY_LABEL.get(letter, code)
    suffix = code[len(letter):]
    ordinal = {"E": "every", "1": "1st", "2": "2nd", "3": "3rd", "4": "4th",
               "13": "1st & 3rd", "24": "2nd & 4th"}.get(suffix, suffix)
    return f"Every {day_label} ({ordinal}), {time}" if ordinal == "every" else \
           f"{day_label} {ordinal} of month, {time}"


_OPPOSITE_SIDE = {
    "North": "South", "South": "North", "East": "West", "West": "East",
    "NorthEast": "SouthWest", "SouthWest": "NorthEast",
    "NorthWest": "SouthEast", "SouthEast": "NorthWest",
}
_COMPASS_CANON = {k.upper(): k for k in _OPPOSITE_SIDE}
# 45° sectors counter-clockwise from east.
_COMPASS_SECTORS = ("East", "NorthEast", "North", "NorthWest",
                    "West", "SouthWest", "South", "SouthEast")


def _left_compass(geom):
    """8-way compass side left of a line's digitized direction (EPSG:4326), or None."""
    if geom is None or geom.is_empty:
        return None
    line = geom if geom.geom_type == "LineString" else geom.geoms[0]
    (x0, y0), (x1, y1) = line.coords[0][:2], line.coords[-1][:2]
    dx = (x1 - x0) * math.cos(math.radians(y0))
    dy = y1 - y0
    if dx == 0 and dy == 0:
        return None
    # Left normal of (dx, dy) is (-dy, dx).
    angle = math.degrees(math.atan2(dx, -dy))
    return _COMPASS_SECTORS[round(angle / 45) % 8]


def _side_label(compass):
    """"NorthEast" -> "Northeast"; None stays None."""
    return compass[0] + compass[1:].lower() if isinstance(compass, str) else None


def _normalise_sf(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:

    out = gdf.copy()

    # Case-insensitive column lookup
    c = {col.lower(): col for col in out.columns}

    def col(*alts):
        for n in alts:
            if n.lower() in c:
                return c[n.lower()]
        return None

    name_col  = col("corridor", "cnn", "street_name")
    side_col  = col("blockside", "block_side")
    day_col   = col("week_day", "weekday")
    fh_col    = col("from_hour", "fromhour")
    th_col    = col("to_hour", "tohour")
    week_cols = [(n, col(f"week_{n}_of_month", f"week{n}ofmonth", f"week{n}")) for n in range(1, 6)]

    out["STREET_NAME"] = (
        out[name_col].fillna("").str.strip().str.upper() if name_col else ""
    )
    _add_key_and_display(out)
    out[list(ADDR_COLS)] = np.nan

    # -----------------------------------------------------------------------
    # Vectorised derivation of code / time / desc / side — avoids iterrows.
    # -----------------------------------------------------------------------
    # --- DAY code (letter + ordinal suffix) ---
    raw_day = (
        out[day_col].fillna("").astype(str).str.strip().str.lower()
        if day_col
        else pd.Series("", index=out.index)
    )
    letter_series = raw_day.map(_SF_DAY_MAP).fillna("")  # "" where unmapped

    # Determine ordinal suffix from week_N_of_month flags (vectorised)
    on_flags = pd.DataFrame(index=out.index)
    for n, wc in week_cols:
        if wc:
            on_flags[n] = out[wc].astype(str).str.strip().isin(["1", "1.0", "true", "True"])
        else:
            on_flags[n] = False
    # Build suffix: "E" if no flags or all 4 set; else sorted digit string
    def _suffix(row_flags):
        on = [n for n, v in row_flags.items() if v]
        if not on or set(on) >= {1, 2, 3, 4}:
            return "E"
        return "".join(str(w) for w in sorted(on))
    suffix_series = on_flags.apply(_suffix, axis=1)
    code_series   = (letter_series + suffix_series).where(letter_series != "", other=None)

    # --- TIME string ---
    def _fmt_h(h):
        return f"{h % 12 or 12}{'AM' if h < 12 else 'PM'}"
    if fh_col and th_col:
        fh_num = pd.to_numeric(out[fh_col], errors="coerce")
        th_num = pd.to_numeric(out[th_col], errors="coerce")
        valid  = fh_num.notna() & th_num.notna()
        fh_int = fh_num.fillna(0).astype(int)
        th_int = th_num.fillna(0).astype(int)
        time_series = pd.Series(
            [
                f"{_fmt_h(f)}–{_fmt_h(t)}" if v else "N/A"
                for f, t, v in zip(fh_int, th_int, valid)
            ],
            index=out.index,
            dtype=object,
        )
    else:
        time_series = pd.Series("N/A", index=out.index, dtype=object)

    # --- DESC string ---
    desc_series = pd.Series(
        [_sf_desc(c, t) for c, t in zip(code_series, time_series)],
        index=out.index,
        dtype=object,
    )

    # --- Side classification ---
    raw_side = (out[side_col].fillna("").astype(str).str.strip().str.upper() if side_col
                else pd.Series("", index=out.index))
    is_even = raw_side == "EVEN"
    is_odd  = raw_side == "ODD"

    # Left of the centerline -> even bucket, right -> odd bucket. Synthetic
    # address parity (left 0, right 1) lets resolve._determine_side map the
    # car's geometric side onto the buckets; SIDE_EVEN / SIDE_ODD carry the
    # compass labels shown instead of "Even" / "Odd".
    lr_col = col("cnnrightleft")
    if lr_col:
        lr = out[lr_col].fillna("").astype(str).str.strip().str.upper()
        is_l, is_r = lr == "L", lr == "R"
        is_even, is_odd = is_even | is_l, is_odd | is_r
        known = is_l | is_r
        for cn, v in (("L_F_ADD", 0), ("L_T_ADD", 0), ("R_F_ADD", 1), ("R_T_ADD", 1)):
            out[cn] = np.where(known, v, np.nan)
        cnn_col = col("cnn")
        cnn = out[cnn_col].astype(str) if cnn_col else pd.Series(out.index.astype(str),
                                                                  index=out.index)
        compass = raw_side.map(_COMPASS_CANON)
        left = compass.where(is_l).groupby(cnn).transform("first")
        right = compass.where(is_r).groupby(cnn).transform("first")
        left = left.fillna(right.map(_OPPOSITE_SIDE))
        right = right.fillna(left.map(_OPPOSITE_SIDE))
        geo_left = out.geometry.map(_left_compass)
        left = left.fillna(geo_left)
        right = right.fillna(geo_left.map(_OPPOSITE_SIDE))
        out["SIDE_EVEN"] = left.map(_side_label).where(known)
        out["SIDE_ODD"] = right.map(_side_label).where(known)
    is_both = ~is_even & ~is_odd

    out["DAY_EVEN"]  = code_series.where(is_even | is_both, other=None)
    out["DAY_ODD"]   = code_series.where(is_odd  | is_both, other=None)
    out["DESC_EVEN"] = desc_series.where(is_even | is_both, other=None)
    out["DESC_ODD"]  = desc_series.where(is_odd  | is_both, other=None)
    out["TIME_EVEN"] = time_series.where(is_even | is_both, other=None)
    out["TIME_ODD"]  = time_series.where(is_odd  | is_both, other=None)

    return out


# ---------------------------------------------------------------------------
# Chicago  (zones from geospatial export + schedule from Socrata JSON API)
# ---------------------------------------------------------------------------

# Chicago sweep months present in the dataset (no Jan-Mar / Dec sweeping).
_CHICAGO_MONTHS = {
    "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11,
}
_CHICAGO_MONTH_ABBR = {
    4: "Apr", 5: "May", 6: "Jun", 7: "Jul",
    8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov",
}

# A year whose sweep days exceed this share on weekends is rejected by year
# inference. Chicago sweeps Mon-Fri by ordinance; the correct year sits far
# below this (~0.3% observed), a mislabelled year far above (~20%).
_CHICAGO_MAX_WEEKEND_RATIO = 0.10


def _infer_chicago_year(month_day_pairs, ref_year: int) -> int:
    """Calendar year the sweep day-numbers belong to, by weekday alignment.

    The dataset gives day-of-month numbers but no year. Chicago street sweeping
    runs Mon-Fri, so each number lands on a weekday in exactly one recent year.
    Among ref_year-1 .. ref_year+1, pick the year placing the fewest dates on
    weekends (ties favour ref_year). Raise when no recent year fits — that means
    the dataset lags more than a year and the manifest id needs updating, rather
    than silently emitting sweep dates on the wrong weekdays.

    ``month_day_pairs`` is an iterable of ``(month_int, day_int)``.
    """
    import datetime as _dt

    pairs = list(month_day_pairs)
    if not pairs:
        return ref_year
    scored = []
    for year in (ref_year, ref_year - 1, ref_year + 1):
        total = weekend = 0
        for m, d in pairs:
            try:
                when = _dt.date(year, m, d)
            except ValueError:
                continue  # e.g. day 31 in a 30-day month — invalid every year
            total += 1
            weekend += when.weekday() >= 5
        if total:
            scored.append((weekend / total, abs(year - ref_year), year))
    if not scored:
        return ref_year
    scored.sort()  # lowest weekend share, then closest to ref_year
    best_ratio, _dist, best_year = scored[0]
    if best_ratio > _CHICAGO_MAX_WEEKEND_RATIO:
        raise ValueError(
            f"Chicago sweep dates align with no recent year (best {best_year}: "
            f"{best_ratio:.0%} on weekends). The dataset id in "
            f"data/manifests/chicago_all.yaml is stale — update it."
        )
    return best_year


def _normalise_chicago(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """
    Chicago normaliser for the zones schema (e.g. dataset 2r7q-emq3).

    The zones GeoJSON embeds the sweeping schedule directly as month columns
    (april, may, …, november), each holding comma-separated day-of-month
    numbers. No separate schedule API call is needed.

    The numbers carry no year, so the calendar year is inferred from the data
    by weekday alignment (see _infer_chicago_year) rather than assumed to be the
    current year — a lagging dataset must not be mislabelled. Chicago publishes
    a new dataset each spring; update the id in data/manifests/chicago_all.yaml
    (and data/sources.yaml) when that happens.
    """
    import datetime as _dt

    # Normalise column names to lowercase for consistent access.
    out = gdf.copy()
    out.columns = [c.lower() for c in out.columns]

    def _row_pairs(row):
        """[(month, day), ...] from a row's month columns; skips blanks/junk."""
        pairs = []
        for col, m in _CHICAGO_MONTHS.items():
            val = str(row.get(col, "") or "").strip()
            if not val:
                continue
            for tok in val.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    pairs.append((m, int(tok)))
                except ValueError:
                    pass  # non-numeric day token (data artifact)
        return pairs

    rows = list(out.iterrows())
    per_row = [_row_pairs(row) for _, row in rows]
    schedule_year = _infer_chicago_year(
        (md for pairs in per_row for md in pairs), _dt.date.today().year
    )

    def _schedule(pairs):
        dates = []
        for m, d in sorted(pairs):
            try:
                dates.append(_dt.date(schedule_year, m, d))
            except ValueError:
                pass  # invalid day for this month (e.g. Apr 31)
        if not dates:
            return None, None
        code = "DATES:" + ",".join(d.isoformat() for d in dates)
        # Stable full-season description grouped by month (e.g. "Apr 1, 2;
        # May 13"). The UI recomputes upcoming dates from the code at render
        # time (analysis.format_schedule_side), so this is storage/debug only.
        grouped: dict = {}
        order: list = []
        for d in dates:
            if d.month not in grouped:
                grouped[d.month] = []
                order.append(d.month)
            grouped[d.month].append(str(d.day))
        desc = "; ".join(
            f"{_CHICAGO_MONTH_ABBR[m]} " + ", ".join(grouped[m]) for m in order
        )
        return code, desc

    day_codes, descs, names = [], [], []
    for pairs, (_, row) in zip(per_row, rows):
        code, desc = _schedule(pairs)
        day_codes.append(code)
        descs.append(desc)
        w = str(row.get("ward", "?")).zfill(2)
        s = str(row.get("section", "?")).zfill(2)
        # Keep readable title-case in-memory (e.g. "Ward 05, Section 03")
        names.append(f"Ward {w}, Section {s}")

    # Keep the in-memory STREET_NAME in readable form (Title / mixed-case)
    out["STREET_NAME"] = names
    _add_key_and_display(out)
    out["DAY_EVEN"]    = day_codes
    out["DAY_ODD"]     = day_codes
    out["DESC_EVEN"]   = descs
    out["DESC_ODD"]    = descs
    out["TIME_EVEN"]   = None
    out["TIME_ODD"]    = None
    out[list(ADDR_COLS)] = np.nan
    return out


# ---------------------------------------------------------------------------
# Berkeley / Alameda  (pre-built GeoJSON — all columns already present)
# ---------------------------------------------------------------------------

def _normalise_prebuilt(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """
    Normaliser for cities whose GeoJSON is pre-built by a build script
    (e.g. scripts/build_berkeley_geojson.py, scripts/build_alameda_geojson.py).
    All standard columns are already present; this just ensures nothing is missing.
    """
    out = gdf.copy()
    for col in SCHEDULE_COLS:
        if col not in out.columns:
            out[col] = None
    for col in ADDR_COLS:
        if col not in out.columns:
            out[col] = np.nan
    # Ensure STREET_KEY and STREET_DISPLAY exist and are derived from STREET_NAME
    if "STREET_NAME" in out.columns:
        _add_key_and_display(out)
    else:
        out["STREET_KEY"] = ""
        out["STREET_DISPLAY"] = ""
    return out


# ---------------------------------------------------------------------------
# Schema profiles — named normalisers selected by a city manifest's `schema`.
# Each keeps its source format distinct; manifests never remap columns.
# Add an entry here when a new city introduces a novel source format.
# ---------------------------------------------------------------------------

SCHEMA_PROFILES = {
    "oakland":  _normalise_oakland,
    "sf":       _normalise_sf,
    "chicago":  _normalise_chicago,
    "berkeley": _normalise_prebuilt,
    "alameda":  _normalise_prebuilt,
}

