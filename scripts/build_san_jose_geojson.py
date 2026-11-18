#!/usr/bin/env python3
"""
Build data/san_jose/StreetSweeping.geojson from the city's sweeping routes and streets
(fetched by scripts/rebuild_city_data.py san_jose).

Usage (from repo root):  python scripts/build_san_jose_geojson.py
Input:  data/san_jose/residential.geojson  daytime routes: day, week, signed or not, posted hours
        data/san_jose/major.geojson        night routes on major roads, one row per side
        data/san_jose/streets.geojson      street centerlines with address ranges
Output: data/san_jose/StreetSweeping.geojson

Each route row takes its street's centerline (CENTERLINEID) for geometry and ranges; a row
for one compass side lands on the even or odd side through that side's address parity.
Only signed routes are ticketed: unsigned rows are NT.
"""

import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city
import shapely

from broombuster import normalize, schedule
from broombuster.config import REPO_ROOT

_WEEKS = {"ODD": "1 3", "EVEN": "2 4", "ALL": ""}  # WEEKSWEPT of the major-road routes
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d\d))?\s*([AP])M\b|\bnoon\b", re.I)
_RANGE_RE = re.compile(r"\d\s*[AP]M\s*(?:-|to)\s*\d", re.I)
_NOT_SWEEP_RE = re.compile(r"anytime|except|permit|hour parking|loading|only", re.I)
_ANY_DAY_RE = re.compile(r"any\s*day", re.I)
_RANGES = ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")


def day_words(day, week) -> str:
    """Schedule words of a route row: ("Monday", "Odd") -> "Monday 1 3", ("Monday", 2) ->
    "Monday 2", ("Monday", "All") -> "Monday"."""
    w = str(week if pd.notna(week) else "").strip()
    return f"{day} {_WEEKS.get(w.upper(), w)}".strip()


def _minutes(m) -> int:
    if m[0].lower() == "noon":
        return 12 * 60
    return int(m[1]) % 12 * 60 + (12 * 60 if m[3].upper() == "P" else 0) + int(m[2] or 0)


def span(posted) -> str | None:
    """One window over a sign's daytime windows: "7AM to NOON  12:30PM to 5PM" -> "7AM–5PM"
    (the lunch gap stays inside: over-warns by up to an hour)."""
    clocks = list(_CLOCK_RE.finditer(str(posted or "")))
    if len(clocks) < 2:
        return None
    first, last = min(clocks, key=_minutes), max(clocks, key=_minutes)
    return citykit.time_text(f"{first[0]} - {last[0]}")


def residential_entry(row) -> tuple:
    words = day_words(row["DAYSWEPT"], row["WEEKSWEPT"])
    signed = row["ROUTETYPE"] == "Signed Route"
    return citykit.side_entry(words, span(row["POSTEDTIME"]) if signed else None, enforced=signed)


def major_entry(row) -> tuple:
    """A night-route side: the posted sign's days and hours when it restricts parking for
    sweeping, else the route's day with NT."""
    words = day_words(row["DAYSWEPT"], row["WEEKSWEPT"])
    posted = normalize.clean_text(row["POSTEDTIME"]) or ""
    if row["ROUTETYPE"] != "Signed Route":
        return citykit.side_entry(words, enforced=False)
    if not _RANGE_RE.search(posted) or _NOT_SWEEP_RE.search(posted):
        code, desc, _ = citykit.side_entry(words, enforced=False)
        return code, f"{desc}; sign: {posted}" if posted else desc, None
    time = citykit.time_text(posted)
    days = _CLOCK_RE.sub(" ", posted)  # "Tuesday 1am-6am": the 1 is no week
    if _ANY_DAY_RE.search(posted):
        code, desc = "E", "Every day"
    else:  # the sign's own days, else the route's
        code, desc, _ = citykit.side_entry(days if citykit.code_from_text(days) else words)
    win = schedule.time_window(time)
    if win and win[0] >= win[1]:  # overnight ("8PM–6AM"): untimed, the whole day warns
        return code, f"{desc}, {time}", None
    return code, desc, time


def compass_side(line, side: str) -> str:
    """ "L" or "R": the side of a line (start to end) facing a compass side."""
    line = shapely.line_merge(line) if line.geom_type == "MultiLineString" else line
    pts = shapely.get_coordinates(line)
    dx, dy = pts[-1] - pts[0]
    left = {"NORTH": dx, "SOUTH": -dx, "EAST": -dy, "WEST": dy}[side.upper()]
    return "L" if left > 0 else "R"


def _parity(row, lr: str) -> str | None:
    """ "EVEN" / "ODD" of a line's L or R side, else the opposite of the other side."""
    sides = citykit.parity_sides(row)
    if lr in sides:
        return sides[lr]
    other = sides.get("R" if lr == "L" else "L")
    return {"EVEN": "ODD", "ODD": "EVEN"}.get(other)


def join(routes: pd.DataFrame, streets: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per route row (medians left out), on its centerline."""
    st = streets.set_index(streets["INTID"].astype("Int64"))
    st = st[~st.index.duplicated()]
    rows, dropped = [], 0
    for _, r in routes.iterrows():
        if r["ROUTETYPE"] == "Median Island" or r["_entry"][0] is None:
            continue
        cid = pd.to_numeric(r["CENTERLINEID"], errors="coerce")
        line = st.loc[int(cid)] if pd.notna(cid) and int(cid) in st.index else None
        rec = {"geometry": r.geometry, "STREET_NAME": pdf_city.street_label(r["ROADNAME"])}
        rec |= dict.fromkeys(_RANGES)
        if line is not None:
            rec["geometry"] = line.geometry
            for col, src in zip(
                _RANGES, ("FROMLEFT", "TOLEFT", "FROMRIGHT", "TORIGHT"), strict=True
            ):
                v = pd.to_numeric(line[src], errors="coerce")
                rec[col] = float(v) if pd.notna(v) and v > 0 else None
        side = str(r["SIDEOFROAD"] or "Both").strip().upper()
        sides = ("EVEN", "ODD")
        if side != "BOTH":
            par = _parity(rec, compass_side(rec["geometry"], side))
            if par is None:
                dropped += 1
                continue
            sides = (par,)
        for s in ("EVEN", "ODD"):
            entry = r["_entry"] if s in sides else (None, None, None)
            rec[f"DAY_{s}"], rec[f"DESC_{s}"], rec[f"TIME_{s}"] = entry
        rows.append(rec)
    if dropped:
        print(f"  ⚠  {dropped} one-side rows had no address parity and were dropped.")
    out = geopandas.GeoDataFrame(rows, crs="EPSG:4326")
    key = out.drop(columns="geometry").astype(str).assign(_g=out.geometry.to_wkb())
    return out[~key.duplicated()].reset_index(drop=True)


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_jose"
    names = ("residential", "major", "streets")
    paths = [data_dir / f"{n}.geojson" for n in names]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py san_jose")
    res, major, streets = (geopandas.read_file(p) for p in paths)
    res["_entry"] = res.apply(residential_entry, axis=1)
    major["_entry"] = major.apply(major_entry, axis=1)
    print(f"  {len(res)} residential and {len(major)} major-road route rows")
    routes = pd.concat([res, major], ignore_index=True)
    pdf_city.write(join(routes, streets), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
