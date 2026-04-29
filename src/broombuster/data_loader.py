"""
Loads and normalises street-sweeping GeoDataFrames for any supported city.

After loading, every GeoDataFrame shares this standard column schema so that
analysis.py and maps.py work identically regardless of the data source:

  STREET_NAME    Full street name, upper-case, whitespace-normalised
  DAY_EVEN       Oakland-style sweep-day code for even-numbered addresses
                 (e.g. "M13", "FE", "WE").  None / NaN means no sweep.
  DAY_ODD        Same for odd-numbered addresses.
  DESC_EVEN      Human-readable schedule description – even side
  DESC_ODD       Human-readable schedule description – odd side
  TIME_EVEN      Sweep time window string – even side  (e.g. "8AM–10AM")
  TIME_ODD       Sweep time window string – odd side
  L_F_ADD        Left-side from-address number  (NaN if unavailable)
  L_T_ADD        Left-side to-address number
  R_F_ADD        Right-side from-address number
  R_T_ADD        Right-side to-address number

Oakland-style day codes understood by analysis.parse_sweeping_code():
  ME / TE / WE / THE / FE / SE = every Mon/Tue/Wed/Thu/Fri/Sat
  M13 / T24 / W13 / TH24 / F13 / F24 = 1st+3rd or 2nd+4th of month
  MWF / TTH / TTHS / MF / E = compound / every-day codes
  N / NS / O = no sweeping
"""

import io
import os
import zipfile

# Repo root — used to resolve data file paths regardless of working directory.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import geopandas
import numpy as np
import requests
import normalize
from collections import OrderedDict
import threading

# In-memory LRU cache for already-read FlatGeobuf files: path -> (mtime, gdf)
# Use an OrderedDict and a small max size to bound memory usage.
MAX_GDF_CACHE_ENTRIES = int(os.environ.get("MAX_GDF_CACHE_ENTRIES", "5"))
_GDF_CACHE: "OrderedDict[str, tuple[float, geopandas.GeoDataFrame]]" = OrderedDict()
_GDF_CACHE_LOCK = threading.Lock()

# Prefer pyogrio when available for faster reads
try:
    import pyogrio  # type: ignore
    _HAS_PYOGRIO = True
except Exception:
    _HAS_PYOGRIO = False
from shapely.geometry import box as _shapely_box

from cities import CITIES

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_SCHEMA_COLS = [
    "STREET_NAME",
    "STREET_KEY",
    "STREET_DISPLAY",
    "DAY_EVEN", "DAY_ODD",
    "DESC_EVEN", "DESC_ODD",
    "TIME_EVEN", "TIME_ODD",
    "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD",
]


def load_city_data(city_key: str, *, force_refresh: bool = False) -> geopandas.GeoDataFrame:
    """Return a normalised GeoDataFrame for the given city key (EPSG:4326).

    On first call, the raw source is normalised and saved as a FlatGeobuf file
    (``city['fgb_path']``). Subsequent calls read that file directly — no
    normalisation overhead at runtime.  Pass ``force_refresh=True`` to delete
    the FGB (and, for auto-download cities, the raw source) and rebuild.
    """
    city       = CITIES[city_key]
    local_path = os.path.join(_ROOT, city["local_path"])
    fgb_raw    = city.get("fgb_path", "")
    fgb_path   = os.path.join(_ROOT, fgb_raw) if fgb_raw else None

    if force_refresh:
        # Always drop the FGB so it gets rebuilt.
        if fgb_path and os.path.exists(fgb_path):
            os.remove(fgb_path)
            print(f"  Removed FGB cache for {city['name']}.")
        # Only drop the raw source when we can re-download it.
        if city.get("url") and os.path.exists(local_path):
            os.remove(local_path)
            print(f"  Removed raw source for {city['name']}.")

    # Fast path: FGB already built → read from disk or in-memory cache.
    if fgb_path and os.path.exists(fgb_path):
        mtime = os.path.getmtime(fgb_path)
        with _GDF_CACHE_LOCK:
            cached = _GDF_CACHE.get(fgb_path)
            if cached and cached[0] == mtime:
                # Move to end (most-recently-used)
                _GDF_CACHE.move_to_end(fgb_path)
                return cached[1].copy()
        # Read using pyogrio where possible for better perf, fall back to geopandas default
        if _HAS_PYOGRIO:
            try:
                gdf = geopandas.read_file(fgb_path, engine="pyogrio")
            except Exception:
                gdf = geopandas.read_file(fgb_path)
        else:
            gdf = geopandas.read_file(fgb_path)
        # Post-process read GDF for in-memory consumption: for Chicago we
        # prefer a readable `STREET_NAME` (e.g. "Ward 05, Section 03"). The
        # on-disk FGB keeps `STREET_NAME` uppercase for storage consistency.
        if city.get("schema") == "chicago" and "STREET_DISPLAY" in gdf.columns:
            gdf = gdf.copy()
            gdf["STREET_NAME"] = gdf["STREET_DISPLAY"]
        with _GDF_CACHE_LOCK:
            _GDF_CACHE[fgb_path] = (mtime, gdf)
            _GDF_CACHE.move_to_end(fgb_path)
            # Evict oldest if over capacity
            while len(_GDF_CACHE) > MAX_GDF_CACHE_ENTRIES:
                _GDF_CACHE.popitem(last=False)
        return gdf.copy()

    # --- Slow path: build from raw source ---
    if not os.path.exists(local_path):
        url = city.get("url")
        if not url:
            raise FileNotFoundError(
                f"Data file not found: {local_path}\n"
                f"No automatic download is configured for '{city['name']}'.\n"
                f"Download the data manually and save it to: {local_path}\n"
                f"See cities.py for the data-portal URL."
            )
        print(f"Downloading {city['name']} data …")
        _download(url, local_path)
        print("Download complete.")

    gdf = geopandas.read_file(local_path)

    # Optional geographic clip.  Reproject to EPSG:4326 for the intersection
    # test (bbox coords are always degrees), keep original CRS for normalisation.
    if "bbox" in city:
        lat_min, lon_min, lat_max, lon_max = city["bbox"]
        clip = _shapely_box(lon_min, lat_min, lon_max, lat_max)
        gdf_4326 = gdf.to_crs("EPSG:4326") if (gdf.crs and not gdf.crs.equals("EPSG:4326")) else gdf
        gdf = gdf[gdf_4326.geometry.intersects(clip)].copy()

    gdf = _normalise(gdf, city["schema"])

    # Persist as FGB for fast future loads.
    if fgb_path:
        _save_fgb(gdf, fgb_path)

    return gdf


def load_region_data(region_key: str, *, force_refresh: bool = False) -> geopandas.GeoDataFrame:
    """
    Return a normalised GeoDataFrame covering all cities in the given region.

    Cities whose data files are missing (and have no auto-download URL) are
    skipped with a warning, so the rest of the region still loads.  Each row
    gets a ``_city`` column with the source city key.
    """
    import pandas as pd

    from cities import REGIONS

    region = REGIONS[region_key]
    print(f"Loading region '{region['name']}' …")
    gdfs = []
    for city_key in region["cities"]:
        try:
            gdf = load_city_data(city_key, force_refresh=force_refresh).copy()
            gdf["_city"] = city_key
            gdfs.append(gdf)
            print(f"  ✓ {CITIES[city_key]['name']} ({len(gdf)} segments)")
        except FileNotFoundError as exc:
            print(f"  ⚠  Skipping {CITIES[city_key]['name']}: {exc}")

    if not gdfs:
        raise RuntimeError(
            f"No city data could be loaded for region '{region_key}'.\n"
            "Place the required data files and retry (see cities.py for details)."
        )

    combined = geopandas.GeoDataFrame(
        pd.concat(
            [g.to_crs("EPSG:4326") for g in gdfs],
            ignore_index=True,
        ),
        crs="EPSG:4326",
    )
    print(f"Region ready — {len(combined)} total segments.")
    return combined


# ---------------------------------------------------------------------------
# FlatGeobuf helpers
# ---------------------------------------------------------------------------

def _save_fgb(gdf: geopandas.GeoDataFrame, fgb_path: str) -> None:
    """Write a normalised GDF to FlatGeobuf (schema columns + geometry, EPSG:4326)."""
    cols = [c for c in _SCHEMA_COLS if c in gdf.columns]
    out = gdf[cols + ["geometry"]].copy()
    # Reproject to EPSG:4326 so every FGB is in a consistent CRS.
    if out.crs and not out.crs.equals("EPSG:4326"):
        out = out.to_crs("EPSG:4326")
    os.makedirs(os.path.dirname(os.path.abspath(fgb_path)), exist_ok=True)
    # Persist a disk-friendly copy: ensure stored STREET_NAME is uppercase
    disk_out = out.copy()
    if "STREET_NAME" in disk_out.columns:
        try:
            disk_out["STREET_NAME"] = disk_out["STREET_NAME"].astype(str).str.upper()
        except Exception:
            pass
    disk_out.to_file(fgb_path, driver="FlatGeobuf")
    mb = os.path.getsize(fgb_path) / 1_048_576
    print(f"  Saved FGB → {fgb_path}  ({mb:.1f} MB)")
    try:
        mtime = os.path.getmtime(fgb_path)
        with _GDF_CACHE_LOCK:
            # Cache the readable in-memory copy (out) while the on-disk file
            # stores an uppercase STREET_NAME where applicable.
            _GDF_CACHE[fgb_path] = (mtime, out.copy())
            _GDF_CACHE.move_to_end(fgb_path)
            while len(_GDF_CACHE) > MAX_GDF_CACHE_ENTRIES:
                _GDF_CACHE.popitem(last=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(url: str, local_path: str) -> None:
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "zip" in content_type or "Shapefile" in url or local_path.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        z.extractall(os.path.dirname(local_path))
    else:
        with open(local_path, "wb") as fh:
            fh.write(resp.content)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _normalise(gdf: geopandas.GeoDataFrame, schema: str) -> geopandas.GeoDataFrame:
    dispatch = {
        "oakland":  _normalise_oakland,
        "sf":       _normalise_sf,
        "chicago":  _normalise_chicago,
        "berkeley": _normalise_prebuilt,
        "alameda":  _normalise_prebuilt,
    }
    fn = dispatch.get(schema)
    if fn is None:
        raise ValueError(f"Unknown schema '{schema}'")
    return fn(gdf)


# ---------------------------------------------------------------------------
# Oakland
# ---------------------------------------------------------------------------

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
    out["STREET_KEY"] = out["STREET_NAME"].map(lambda v: normalize.street_name(v) if isinstance(v, str) else "")
    out["STREET_DISPLAY"] = out["STREET_NAME"].map(lambda v: normalize.street_display(v) if isinstance(v, str) else "")
    out["DESC_EVEN"] = out.get("DescDayEve", pd_series_none(out))
    out["DESC_ODD"]  = out.get("DescDayOdd", pd_series_none(out))
    out["TIME_EVEN"] = out.get("DescTimeEv", pd_series_none(out))
    out["TIME_ODD"]  = out.get("DescTimeOd", pd_series_none(out))
    # DAY_EVEN, DAY_ODD, L_F_ADD, L_T_ADD, R_F_ADD, R_T_ADD already correct.
    return out


def pd_series_none(ref_gdf):
    """Return a Series of None values with the same index as ref_gdf."""
    import pandas as pd
    return pd.Series([None] * len(ref_gdf), index=ref_gdf.index)


# ---------------------------------------------------------------------------
# San Francisco  (DataSF – Street Sweeping Schedule, yhqp-riqs)
# ---------------------------------------------------------------------------
# Key columns (from DataSF metadata):
#   corridor       street name  e.g. "MARKET ST"
#   blockside      "ODD", "EVEN", or "BOTH"
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


def _normalise_sf(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    import pandas as pd

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
    out["STREET_KEY"] = out["STREET_NAME"].map(lambda v: normalize.street_name(v) if isinstance(v, str) else "")
    out["STREET_DISPLAY"] = out["STREET_NAME"].map(lambda v: normalize.street_display(v) if isinstance(v, str) else "")
    for addr_cn in ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"):
        out[addr_cn] = np.nan

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
    raw_side = (out[side_col].fillna("").astype(str).str.upper() if side_col
                else pd.Series("", index=out.index))
    is_even = raw_side == "EVEN"
    is_odd  = raw_side == "ODD"
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

def _normalise_chicago(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """
    Chicago normaliser for the 2025+ schema (dataset utb4-q645).

    The zones GeoJSON embeds the sweeping schedule directly as month columns
    (april, may, …, november), each containing comma-separated day numbers.
    No separate schedule API call is needed.

    Chicago publishes new datasets each year (typically March/April).
    Update the 'url' ID in cities.py when that happens.
    """
    import datetime as _dt

    MONTHS = {
        "april": 4, "may": 5, "june": 6, "july": 7,
        "august": 8, "september": 9, "october": 10, "november": 11,
    }
    MONTH_ABBR = {
        4: "Apr", 5: "May", 6: "Jun", 7: "Jul",
        8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov",
    }
    today_year = _dt.date.today().year
    today      = _dt.date.today()

    # Normalise column names to lowercase for consistent access.
    out = gdf.copy()
    out.columns = [c.lower() for c in out.columns]

    def _build_schedule(row):
        iso_parts, desc_parts = [], []
        for col, m in MONTHS.items():
            val = str(row.get(col, "") or "").strip()
            if not val:
                continue
            valid_days = []
            for d in val.split(","):
                d = d.strip()
                try:
                    valid_days.append(
                        _dt.date(today_year, m, int(d)).isoformat()
                    )
                except (ValueError, TypeError):
                    pass
            if valid_days:
                iso_parts.extend(valid_days)
                desc_parts.append((m, MONTH_ABBR[m], val))
        if not iso_parts:
            return None, None
        code = f"DATES:{','.join(iso_parts)}"
        # Build DESC: show every date that falls within the next 2 months
        # that have sweeping (typically 4 dates, e.g. "Apr 17, 18; May 15, 16").
        future_months: list = []
        for ds in sorted(iso_parts):
            d = _dt.date.fromisoformat(ds)
            if d >= today and (d.year, d.month) not in future_months:
                future_months.append((d.year, d.month))
        target_months = set(future_months[:2])
        shown: dict = {}
        for ds in sorted(iso_parts):
            d = _dt.date.fromisoformat(ds)
            if (d.year, d.month) in target_months:
                shown.setdefault(d.month, []).append(str(d.day))
        if not shown:
            # Off-season: show first 2 months' dates
            first_months: list = []
            for ds in sorted(iso_parts):
                d = _dt.date.fromisoformat(ds)
                if (d.year, d.month) not in first_months:
                    first_months.append((d.year, d.month))
            for ds in sorted(iso_parts):
                d = _dt.date.fromisoformat(ds)
                if (d.year, d.month) in set(first_months[:2]):
                    shown.setdefault(d.month, []).append(str(d.day))
        desc = "; ".join(
            f"{MONTH_ABBR[m]} {', '.join(days)}"
            for m, days in sorted(shown.items())
        )
        return code, desc

    day_codes, descs, names = [], [], []
    for _, row in out.iterrows():
        code, desc = _build_schedule(row)
        day_codes.append(code)
        descs.append(desc)
        w = str(row.get("ward", "?")).zfill(2)
        s = str(row.get("section", "?")).zfill(2)
        # Keep readable title-case in-memory (e.g. "Ward 05, Section 03")
        names.append(f"Ward {w}, Section {s}")

    # Keep the in-memory STREET_NAME in readable form (Title / mixed-case)
    out["STREET_NAME"] = names
    out["STREET_KEY"] = out["STREET_NAME"].map(lambda v: normalize.street_name(v) if isinstance(v, str) else "")
    out["STREET_DISPLAY"] = out["STREET_NAME"].map(lambda v: normalize.street_display(v) if isinstance(v, str) else "")
    out["DAY_EVEN"]    = day_codes
    out["DAY_ODD"]     = day_codes
    out["DESC_EVEN"]   = descs
    out["DESC_ODD"]    = descs
    out["TIME_EVEN"]   = None
    out["TIME_ODD"]    = None
    out["L_F_ADD"]     = np.nan
    out["L_T_ADD"]     = np.nan
    out["R_F_ADD"]     = np.nan
    out["R_T_ADD"]     = np.nan
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
    for col in ("DAY_EVEN", "DAY_ODD", "DESC_EVEN", "DESC_ODD", "TIME_EVEN", "TIME_ODD"):
        if col not in out.columns:
            out[col] = None
    for col in ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD"):
        if col not in out.columns:
            out[col] = np.nan
    # Ensure STREET_KEY and STREET_DISPLAY exist and are derived from STREET_NAME
    if "STREET_NAME" in out.columns:
        out["STREET_KEY"] = out["STREET_NAME"].map(lambda v: normalize.street_name(v) if isinstance(v, str) else "")
        out["STREET_DISPLAY"] = out["STREET_NAME"].map(lambda v: normalize.street_display(v) if isinstance(v, str) else "")
    else:
        out["STREET_KEY"] = ""
        out["STREET_DISPLAY"] = ""
    return out

