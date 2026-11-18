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
_BLANK_RE = re.compile(r"<div>\s*<br\s*/?>\s*</div>|<br\s*/?>\s*<br\s*/?>", re.I)  # blank line
_CLOCK = r"(?:\d{1,2}(?::\d\d)?(?:\s*[ap]m)?|noon)"
_TIME_RE = re.compile(rf"(?P<a>{_CLOCK})\s*(?:\band\b|\bto\b|-|–)\s*(?P<b>{_CLOCK})", re.I)
_WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_THRU_RE = re.compile(rf"\b({'|'.join(_WEEK)})\s+(?:through|thru)\s+({'|'.join(_WEEK)})\b", re.I)
_NONE_RE = re.compile(r"^(?:n/?a|we don.?t sweep|city of .+)?$", re.I)  # side not swept here
_TO_RE = re.compile(r"\s+(?:to|and)\s+", re.I)
_END_RE = re.compile(r"\b(end|limits?|border)\b", re.I)
_UNPLACED = (citykit.UNKNOWN, citykit.UNKNOWN_DESC, None)  # street in parts, block in none
_UTM = "EPSG:26910"
# Page spellings -> the county's (the street signs')
_SPELLING = {"ACTION": "ACTON", "BAYRIDGE": "BAY RIDGE", "CARLETON": "CARLTON",
             "GUTTENBURG": "GUTTENBERG", "ROSYLN": "ROSLYN", "SOUTHHILL": "SOUTH HILL",
             "THEIRS": "THIERS", "SAINT": "ST", "MICHAEL": "MICHAELS", "TRAIL": "TRL",
             "JUNIPERRO": "JUNIPERO", "WILLINGTON": "WELLINGTON",
             "JS": "JUNIPERO SERRA"}  # fmt: skip


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def street(name: str) -> str:
    """County spelling of a page street name: "90 th (Ninetieth) Street" -> "90TH STREET"."""
    s = re.sub(r"\(.*?\)", " ", name.upper())
    s = re.sub(r"(\d)\s+(ST|ND|RD|TH)\b", r"\1\2", s)
    return " ".join(_SPELLING.get(t, t) for t in s.split())


def _items(cell: str) -> list[str]:
    items = [_text(a or b) for a, b in _ITEM_RE.findall(cell) if _text(a or b)]
    return items or [t for x in _BLANK_RE.split(cell) if (t := _text(x))] or [""]


def _clean(item: str) -> str:
    """Page typos normalized: "1 p m", "11 am an 1 pm", "6 AM & 8 AM", "Bet. 6 am"."""
    s = re.sub(r"\b([ap])\s+m\b", r"\1m", item, flags=re.I)
    s = re.sub(r"\ban\b|&", " and ", s, flags=re.I)
    s = re.sub(r"\bbet(?:ween)?\b\.?", " between ", s, flags=re.I)
    return " ".join(s.split())


def _joined(items: list[str]) -> list[str]:
    """A side's entries with a day split from its hours ("North Side - Monday", "Between 7 am
    and 9 am") joined back into one."""
    out = []
    for it in items:
        days, hours = citykit.code_from_text(_clean(it)), _TIME_RE.search(_clean(it))
        if out and hours and not days and not _TIME_RE.search(_clean(out[-1])):
            out[-1] = f"{out[-1]} {it}"
        else:
            out.append(it)
    return out


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
    """(code, desc, time) of one listed side ("South Side - Tue and Thurs Between 5 am and
    6 am"); (None, None, None) when it gives no weekday and hours."""
    s = _clean(item)
    m = _TIME_RE.search(s)
    days = _THRU_RE.sub(
        lambda d: " ".join(_WEEK[_WEEK.index(d[1].lower()) : _WEEK.index(d[2].lower()) + 1]),
        s[: m.start()] if m else "",
    )
    if not m or not citykit.code_from_text(days):
        return None, None, None
    return citykit.side_entry(days, _hours(m["a"], m["b"]))


def rows_from(page: str) -> list[dict]:
    """One dict per street part: name, part ("A to B" or ""), odd and even entries. A listed
    side with no readable schedule stops the build."""
    out, bad, name = [], [], ""
    for row in _ROW_RE.findall(page):
        cells = _CELL_RE.findall(row)
        if len(cells) != 4:
            continue
        name = street(_text(cells[0])) or name  # a blank name cell continues the street above
        odd, even, where = (_items(c) for c in cells[1:])
        odd, even = _joined(odd), _joined(even)
        if len(where) != len(odd):
            where = [""] * len(odd)
        for i, part in enumerate(where):
            sides = {"ODD": odd[i], "EVEN": even[i] if i < len(even) else ""}
            r = {"name": name, "part": "" if part.upper() in ("N/A", "") else part}
            r["both"] = bool(re.search(r"\bboth sides?\b", sides["ODD"], re.I))
            for side, text in sides.items():
                r[side] = side_entry(text)
                if r[side][0] is None and not _NONE_RE.match(text):
                    bad.append(f"{name} {side}: {text!r}")
            out.append(r)
    if bad:
        raise ValueError(f"{len(bad)} listed sides have no readable schedule: {bad}")
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
    """Per block, the index of the part whose two cross streets bracket its midpoint, else
    None; and the part listed without cross streets (the whole street), else None."""
    street = blocks.geometry.union_all()
    ends = {i: e for i, part in enumerate(parts) if (e := _ends(street, part, lines))}
    rest = [i for i, part in enumerate(parts) if not part]
    base = rest[0] if len(rest) == 1 else None
    out = []
    for g in blocks.geometry:
        mid = g.interpolate(0.5, normalized=True)
        # 1.0 on the straight line between the two cross streets, growing off it
        scores = [((mid.distance(a) + mid.distance(b)) / max(a.distance(b), 1.0), i)
                  for i, (a, b) in ends.items()]  # fmt: skip
        best = min(scores, default=(9.0, None))
        out.append((best[1] if best[0] < 1.2 else None, base))
    return out


def _entries(grp: pd.DataFrame, pick, base) -> list[dict]:
    """A block's schedules: its part's, a side the part leaves empty from the whole street,
    plus the whole street's own row when that lists both sides ("Both Sides - Monday")."""
    if pick is None or pick == base:
        return [{"EVEN": _UNPLACED, "ODD": _UNPLACED} if base is None else grp.iloc[base]]
    part = grp.iloc[pick]
    if base is None:
        return [part]
    whole = grp.iloc[base]
    if whole["both"]:
        return [part, whole]
    return [{s: part[s] if part[s][0] else whole[s] for s in ("EVEN", "ODD")}]


def join(rows: list[dict], lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks of each listed street, its part's schedule."""
    lines = lines.to_crs(_UTM)
    typed = lines["STREET_NAME"].map(normalize.street_name_typed)
    page = pd.DataFrame(rows)
    page["key"] = page["name"].map(normalize.street_name_typed)  # "3RD. AVENUE" is 3RD AVE
    out, missing = [], []
    for key, grp in page.groupby("key", sort=False):
        name = grp["name"].iloc[0]
        blocks = lines[typed == key]
        if blocks.empty:  # the page leaves the type out ("Ardendale"), not another listed one's
            blocks = citykit.blocks_by_name(lines, name)
            blocks = blocks[~typed[blocks.index].isin(page["key"])]
        if blocks.empty:
            missing.append(name)
            continue
        alike = len({(r["ODD"], r["EVEN"]) for _, r in grp.iterrows()}) == 1
        picks = [(0, 0)] * len(blocks) if alike else _part_of(blocks, list(grp["part"]), lines)
        for (_, b), (pick, base) in zip(blocks.iterrows(), picks, strict=True):
            for r in _entries(grp, pick, base):
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
