#!/usr/bin/env python3
"""
Build data/alameda/StreetSweeping.geojson from the city's sweeping map and the
county's street centerlines (both fetched by scripts/rebuild_city_data.py alameda).

Usage (from repo root):  python scripts/build_alameda_geojson.py
Input:  data/alameda/schedule.geojson     day, posted time, signed or not per block side
        data/alameda/centerlines.geojson  the same blocks, with geometry
Output: data/alameda/StreetSweeping.geojson

The city serves no geometry, so each block takes the county centerline with the same
street name (after renames) and address ranges, else the one sharing most house numbers;
a block without ranges takes the street's county segments without ranges. Only sides with
No Parking signs are enforced; an unsigned side is citykit.COURTESY ("NT").
"""

from pathlib import Path

import citykit
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
# City names the county writes differently (the county has the current name).
_RENAMED = {"CONSTITUTION WY": "WILMA CHAN WY"}  # renamed 2023
_UNRANGED_SPREAD_M = 1000  # unranged segments of one street farther apart are not one block
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
        return citykit.COURTESY, f"{desc}{f', {time}' if time else ''}, no tickets", None
    return code, desc, time


def _ranges(row, cols) -> tuple[int, int, int, int]:
    return tuple(_int(row[c]) for c in cols)


def _overlap(a, b) -> int:
    """Shared house numbers of two (l_from, l_to, r_from, r_to) blocks."""
    lo_a, hi_a = min(x for x in a if x), max(a)
    lo_b, hi_b = min(x for x in b if x), max(b)
    return min(hi_a, hi_b) - max(lo_a, lo_b)


def _unranged(cand: geopandas.GeoDataFrame):
    """Union of a street's county segments without address ranges; None when there are
    none or they lie apart (not one block)."""
    parts = cand[[not any(r) for r in cand["_rng"]]]
    if parts.empty:
        return None
    x0, y0, x1, y1 = parts.to_crs("EPSG:26910").total_bounds
    if max(x1 - x0, y1 - y0) > _UNRANGED_SPREAD_M:
        return None
    return parts.geometry.union_all()


def join(schedule: pd.DataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: one per scheduled block that has a county centerline."""
    lines = lines.assign(
        _name=lines["STREET"].fillna("").str.split().str.join(" ").str.upper(),
        _rng=[_ranges(r, ("FRADDL", "TOADDL", "FRADDR", "TOADDR")) for _, r in lines.iterrows()],
    )
    by_name = dict(iter(lines.groupby("_name")))
    by_compact = {name.replace(" ", ""): grp for name, grp in by_name.items()}  # KITTY HAWK
    rows, dropped = [], []
    unranged: dict[str, list] = {}  # street -> side entries of its blocks without ranges
    for _, s in schedule.iterrows():
        name = " ".join(str(s["FullStreetName"] or "").upper().split())
        rng = _ranges(s, ("FrAddressLeft", "ToAddressLeft", "FrAddressRight", "ToAddressRight"))
        entries = {side: side_entry(s[d], s[sign], s[t]) for side, d, sign, t in _SIDES}
        if not name or not any(e[0] for e in entries.values()):
            continue
        county = _RENAMED.get(name, name)
        cand = by_name.get(county)
        if cand is None:
            cand = by_compact.get(county.replace(" ", ""))
        if cand is not None and not any(rng):
            unranged.setdefault(name, []).append((s, entries))
            continue
        geom, why = None, "no county street of this name"
        if cand is not None:
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
                why = "no county segment shares its house numbers"
        if geom is None:
            dropped.append((s, entries, why))
            continue
        rows.append(_record(county, geom, rng, entries))
    for name, blocks in unranged.items():
        cand = by_name.get(_RENAMED.get(name, name))
        if cand is None:
            cand = by_compact.get(_RENAMED.get(name, name).replace(" ", ""))
        geom = _unranged(cand)
        if geom is None:
            why = "no county segment without ranges (or they lie apart)"
            dropped += [(s, e, why) for s, e in blocks]
            continue
        # The blocks cannot be told apart: a side whose blocks disagree is unknown.
        merged = {}
        for side in ("EVEN", "ODD"):
            got = {e[side] for _, e in blocks}
            merged[side] = got.pop() if len(got) == 1 else (
                citykit.UNKNOWN, citykit.UNKNOWN_DESC, None)  # fmt: skip
        rows.append(_record(_RENAMED.get(name, name), geom, (0, 0, 0, 0), merged))
    signed = [
        d for d in dropped if any(e[0] not in (None, citykit.COURTESY) for e in d[1].values())
    ]
    print(f"  {len(dropped)} scheduled blocks dropped, {len(signed)} signed:")
    for s, _, why in signed:
        print(
            f"     {s['FullStreetName']} {_ranges(s, ('FrAddressLeft', 'ToAddressRight'))}: {why}"
        )
    renamable = [s["FullStreetName"] for s, _, why in signed if why.startswith("no county street")]
    if renamable:
        raise SystemExit(f"signed blocks on streets the county names otherwise: {renamable}")
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326")


def _record(name, geom, rng, entries) -> dict:
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
    return rec


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
