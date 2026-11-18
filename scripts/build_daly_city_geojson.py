#!/usr/bin/env python3
"""
Build data/daly_city/StreetSweeping.geojson from the city's sweeping schedule page and the
county centerlines (both fetched by scripts/rebuild_city_data.py daly_city).

Usage (from repo root):  python scripts/build_daly_city_geojson.py
Input:  data/daly_city/schedule.html   schedule page: per street, the odd and even
                                                   side's weekday and posted hours
        data/daly_city/centerlines.geojson         San Mateo County centerlines in Daly City
Output: data/daly_city/StreetSweeping.geojson

Every listed side is posted and ticketed weekly. A street listed in parts ("Peoria Street
to Pope Street") gives each block the part whose cross streets bracket it.
"""

import html
import re
from pathlib import Path

import citykit
import geopandas
import pandas as pd
import pdf_city
from shapely.ops import nearest_points

from broombuster import normalize
from broombuster.config import REPO_ROOT

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>|<p[^>]*>(.*?)</p>", re.S | re.I)
_SIDE_RE = re.compile(
    r"-\s*(?:of\s+)?(?P<day>\w+day)\s+between\s+(?P<a>[\w:]+(?:\s*[ap]m)?)\s+and\s+"
    r"(?P<b>[\w:]+(?:\s*[ap]m)?)",
    re.I,
)
_TO_RE = re.compile(r"\s+(?:to|and)\s+", re.I)
_END_RE = re.compile(r"\b(end|limits?)\b", re.I)
_UNPLACED = (citykit.UNKNOWN, citykit.UNKNOWN_DESC, None)  # street in parts, block in none
_UTM = "EPSG:26910"
# Page spellings -> the county's (the street signs')
_SPELLING = {"ACTION": "ACTON", "BAYRIDGE": "BAY RIDGE", "CARLETON": "CARLTON",
             "GUTTENBURG": "GUTTENBERG", "ROSYLN": "ROSLYN", "SOUTHHILL": "SOUTH HILL",
             "THEIRS": "THIERS", "SAINT": "ST", "MICHAEL": "MICHAELS", "TRAIL": "TRL"}  # fmt: skip


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def street(name: str) -> str:
    """County spelling of a page street name: "90 th (Ninetieth) Street" -> "90TH STREET"."""
    s = re.sub(r"\(.*?\)", " ", name.upper())
    s = re.sub(r"(\d)\s+(ST|ND|RD|TH)\b", r"\1\2", s)
    return " ".join(_SPELLING.get(t, t) for t in s.split())


def _items(cell: str) -> list[str]:
    return [_text(a or b) for a, b in _ITEM_RE.findall(cell) if _text(a or b)] or [_text(cell)]


def _hours(a: str, b: str) -> str:
    """ "9 am", "11" -> "9AM–11AM" (the end's am/pm when the start has none, and back)."""
    a, b = (re.sub(r"\bnoon\b", "12 pm", x.rstrip(":"), flags=re.I) for x in (a, b))
    ma, mb = (re.search(r"[ap]m", x, re.I) for x in (a, b))
    if not mb and ma:
        b = f"{b} {ma[0]}"
    if not ma:
        a = f"{a} {mb[0] if mb else 'am'}"
    start, end = (int(re.match(r"\d+", x)[0]) % 12 for x in (a, b))
    if a.lower().endswith("pm") and b.lower().endswith("pm") and start > end:
        a = a[:-2] + "am"  # "11 pm and 1 pm": a typo for 11 am
    return citykit.time_text(f"{a} to {b}")


def side_entry(item: str) -> tuple:
    """(code, desc, time) of one listed side; (None, None, None) when not swept here."""
    m = _SIDE_RE.search(re.sub(r"\b([ap])\s+m\b", r"\1m", item, flags=re.I))  # "1 p m"
    if not m:
        return None, None, None
    return citykit.side_entry(m["day"], _hours(m["a"], m["b"]))


def rows_from(page: str) -> list[dict]:
    """One dict per street part: name, part ("A to B" or ""), odd and even entries."""
    out = []
    for row in _ROW_RE.findall(page):
        cells = _CELL_RE.findall(row)
        if len(cells) != 4:
            continue
        name = street(_text(cells[0]))
        odd, even, where = (_items(c) for c in cells[1:])
        if len(where) != len(odd):
            where = [""] * len(odd)
        for i, part in enumerate(where):
            out.append({
                "name": name,
                "part": "" if part.upper() in ("N/A", "") else part,
                "ODD": side_entry(odd[i]),
                "EVEN": side_entry(even[i] if i < len(even) else ""),
            })  # fmt: skip
    return [r for r in out if r["ODD"][0] or r["EVEN"][0]]


def _cross(lines: geopandas.GeoDataFrame, name: str):
    """Geometry of the cross street called `name` ("South Hill" matches SOUTH HILL BLVD)."""
    key = normalize.street_name(street(name))
    hit = lines[(lines["STREET_KEY"] == key) | lines["STREET_KEY"].str.startswith(key + " ")]
    return hit.geometry.union_all() if not hit.empty else None


def _ends(street, part: str, lines) -> list | None:
    """The two points on `street` where a part ("A to B") starts and ends; "South End" or
    "City Limit" is the street's end farthest from the other cross street."""
    names = _TO_RE.split(part)
    if len(names) != 2:
        return None
    is_end = [bool(_END_RE.search(n)) for n in names]
    pts = [None if e else _cross(lines, n) for n, e in zip(names, is_end, strict=True)]
    pts = [None if g is None else nearest_points(street, g)[0] for g in pts]
    if all(is_end) or any(p is None and not e for p, e in zip(pts, is_end, strict=True)):
        return None
    tips = list(street.boundary.geoms)
    other = next(p for p in pts if p is not None)
    return [p if p is not None else max(tips, key=other.distance) for p in pts]


def _part_of(blocks, parts, lines) -> list:
    """Per block, the index of the part whose two cross streets bracket its midpoint; else
    the part listed without cross streets; else None."""
    street = blocks.geometry.union_all()
    ends = {i: e for i, part in enumerate(parts) if (e := _ends(street, part, lines))}
    rest = [i for i, part in enumerate(parts) if not part]
    out = []
    for g in blocks.geometry:
        mid = g.interpolate(0.5, normalized=True)
        # 1.0 on the straight line between the two cross streets, growing off it
        scores = [((mid.distance(a) + mid.distance(b)) / max(a.distance(b), 1.0), i)
                  for i, (a, b) in ends.items()]  # fmt: skip
        best = min(scores, default=(9.0, None))
        out.append(best[1] if best[0] < 1.2 else rest[0] if len(rest) == 1 else None)
    return out


def join(rows: list[dict], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks of each listed street, its part's schedule."""
    lines = lines.to_crs(_UTM)
    typed = lines["STREET_NAME"].map(normalize.street_name_typed)
    out, missing = [], []
    for name, grp in pd.DataFrame(rows).groupby("name", sort=False):
        blocks = lines[typed == normalize.street_name_typed(name)]
        if blocks.empty:  # the page leaves the type out ("Ardendale")
            blocks = citykit.blocks_by_name(lines, name)
        if blocks.empty:
            missing.append(name)
            continue
        alike = len({(r["ODD"], r["EVEN"]) for _, r in grp.iterrows()}) == 1
        picks = [0] * len(blocks) if alike else _part_of(blocks, list(grp["part"]), lines)
        for (_, b), pick in zip(blocks.iterrows(), picks, strict=True):
            r = {"EVEN": _UNPLACED, "ODD": _UNPLACED} if pick is None else grp.iloc[pick]
            rec = b.drop("STREET_KEY").to_dict()
            for side in ("EVEN", "ODD"):
                rec[f"DAY_{side}"], rec[f"DESC_{side}"], rec[f"TIME_{side}"] = r[side]
            out.append(rec)
    if missing:
        print(f"  ⚠  {len(missing)} listed streets have no county centerline: {missing}")
    gdf = geopandas.GeoDataFrame(out, geometry="geometry", crs=_UTM).to_crs("EPSG:4326")
    gdf["STREET_NAME"] = gdf["STREET_NAME"].map(pdf_city.street_label)
    return gdf


def main():
    data_dir = Path(REPO_ROOT) / "data" / "daly_city"
    inputs = [data_dir / "schedule.html", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py daly_city")
    rows = rows_from(inputs[0].read_text(encoding="utf-8", errors="replace"))
    print(f"  {len(rows)} street parts on the schedule page")
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(join(rows, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
