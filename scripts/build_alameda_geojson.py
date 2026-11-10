#!/usr/bin/env python3
"""
Build data/alameda/StreetSweeping.geojson from the city's sweeping map and the
county's street centerlines (both fetched by scripts/rebuild_city_data.py alameda).

Usage (from repo root):  python scripts/build_alameda_geojson.py
Input:  data/alameda/schedule.geojson     day, posted time, signed or not per block side
        data/alameda/centerlines.geojson  the same blocks, with geometry
Output: data/alameda/StreetSweeping.geojson

The city serves no geometry, so each block takes the county centerline with the same
street name and address ranges (else the one sharing most house numbers). Only sides with No
Parking signs are enforced; an unsigned side gets the no-sweep code "NS".
"""

from pathlib import Path

import geopandas
import pandas as pd
import pdf_city

from broombuster import normalize
from broombuster.config import REPO_ROOT

_DAY_CODE = {
    "MONDAY": ("ME", "Every Mon"),
    "TUESDAY": ("TE", "Every Tue"),
    "WEDNESDAY": ("WE", "Every Wed"),
    "THURSDAY": ("THE", "Every Thu"),
    "FRIDAY": ("FE", "Every Fri"),
    "ALL": ("E", "Every day"),
}
_NO_SIGNS = "NS"  # analysis.NO_SWEEP_CODES
_SIDES = (("EVEN", "SweepEven", "SignEven", "PostedTime_Even"),
          ("ODD", "SweepOdd", "SignOdd", "PostedTime_Odd"))  # fmt: skip


def _int(v) -> int:
    n = pd.to_numeric(v, errors="coerce")
    return 0 if pd.isna(n) else int(n)


def side_entry(day, signed, posted) -> tuple:
    """(code, desc, time) of one block side; (None, None, None) when never swept."""
    hit = _DAY_CODE.get(str(day or "").strip().upper())
    if not hit:
        return None, None, None
    code, desc = hit
    time = normalize.time_display(posted) if normalize.clean_text(posted) else None
    if _int(signed) != 1:
        return _NO_SIGNS, f"{desc}, no signs", None
    return code, desc, time


def _ranges(row, cols) -> tuple[int, int, int, int]:
    return tuple(_int(row[c]) for c in cols)


def _overlap(a, b) -> int:
    """Shared house numbers of two (l_from, l_to, r_from, r_to) blocks."""
    lo_a, hi_a = min(x for x in a if x), max(a)
    lo_b, hi_b = min(x for x in b if x), max(b)
    return min(hi_a, hi_b) - max(lo_a, lo_b)


def join(schedule: pd.DataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per scheduled block that has a county centerline."""
    lines = lines.assign(
        _name=lines["STREET"].fillna("").str.split().str.join(" ").str.upper(),
        _rng=[_ranges(r, ("FRADDL", "TOADDL", "FRADDR", "TOADDR")) for _, r in lines.iterrows()],
    )
    by_name = dict(iter(lines.groupby("_name")))
    by_compact = {name.replace(" ", ""): grp for name, grp in by_name.items()}  # KITTY HAWK
    rows, dropped = [], 0
    for _, s in schedule.iterrows():
        name = " ".join(str(s["FullStreetName"] or "").upper().split())
        rng = _ranges(s, ("FrAddressLeft", "ToAddressLeft", "FrAddressRight", "ToAddressRight"))
        entries = {side: side_entry(s[d], s[sign], s[t]) for side, d, sign, t in _SIDES}
        if not name or not any(e[0] for e in entries.values()):
            continue
        cand = by_name.get(name)
        if cand is None:
            cand = by_compact.get(name.replace(" ", ""))
        geom = None
        if cand is not None and any(rng):
            exact = cand[cand["_rng"] == rng]
            if not exact.empty:
                geom = exact.geometry.union_all()
            else:
                # Left / right follow the line's direction: keep the centerline's own ranges.
                scored = [
                    (_overlap(rng, r), g, r)
                    for r, g in zip(cand["_rng"], cand.geometry, strict=True)
                    if any(r)
                ]
                best = max(scored, key=lambda x: x[0], default=(0, None, None))
                if best[0] > 0:
                    geom, rng = best[1], best[2]
        if geom is None:
            dropped += 1
            continue
        rec = {
            "geometry": geom,
            "STREET_NAME": pdf_city.street_label(name),
            "L_F_ADD": float(rng[0]) or None,
            "L_T_ADD": float(rng[1]) or None,
            "R_F_ADD": float(rng[2]) or None,
            "R_T_ADD": float(rng[3]) or None,
        }
        for side, (code, desc, time) in entries.items():
            rec[f"DAY_{side}"], rec[f"DESC_{side}"], rec[f"TIME_{side}"] = code, desc, time
        rows.append(rec)
    if dropped:
        print(f"  ⚠  {dropped} scheduled blocks had no county centerline and were dropped.")
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def main():
    data_dir = Path(REPO_ROOT) / "data" / "alameda"
    inputs = [data_dir / "schedule.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py alameda")
    schedule, lines = (geopandas.read_file(p) for p in inputs)
    print(f"  {len(schedule)} blocks in the city's schedule")
    pdf_city.write(join(schedule, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
