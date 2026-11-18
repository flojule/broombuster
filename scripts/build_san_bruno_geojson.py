#!/usr/bin/env python3
"""
Build data/san_bruno/StreetSweeping.geojson from the city's sweeping routes and the county
centerlines (both fetched by scripts/rebuild_city_data.py san_bruno).

Usage (from repo root):  python scripts/build_san_bruno_geojson.py
Input:  data/san_bruno/routes.geojson       route lines: weeks and day, posted hours, notes
        data/san_bruno/centerlines.geojson  San Mateo County centerlines in San Bruno
Output: data/san_bruno/StreetSweeping.geojson

Each block takes the curb route running along it (median-island routes are left out). A
route with posted hours is ticketed; one without is swept, nobody ticketed (NT): "most City
streets, not just those posted for removal of cars, are swept twice per month". Both
sides share the schedule. "1st & 3rd Mon / 2nd & 4th Thu" becomes the dates of both.
"""

import datetime
import re
from pathlib import Path

import citykit
import geopandas
import pdf_city

from broombuster import normalize, schedule
from broombuster.config import REPO_ROOT

_BUFFER_M = 15  # route line to centerline distance, metres
_DAYS = ("mon", "tue", "wed", "thu", "fri")
_DAY_RANGE_RE = re.compile(r"\b(mon|tue|wed|thu|fri)\w*\s*-\s*(mon|tue|wed|thu|fri)\w*", re.I)
_WEEK_RANGE_RE = re.compile(r"\b1st\s*-\s*4th\b", re.I)


def _words(text: str) -> str:
    """Ranges spelled out: "Monday - Thursday" -> "mon tue wed thu", "1st - 4th" -> all four."""
    text = _WEEK_RANGE_RE.sub("1st 2nd 3rd 4th", text)
    return _DAY_RANGE_RE.sub(
        lambda m: " ".join(_DAYS[_DAYS.index(m[1].lower()) : _DAYS.index(m[2].lower()) + 1]), text
    )


def _dates(codes, years=(2026, 2027)) -> str:
    """DATES code of every day any of `codes` sweeps in `years`."""
    day, end = datetime.date(years[0], 1, 1), datetime.date(years[-1], 12, 31)
    out = []
    while day <= end:
        if any(schedule.sweeps_on(c, day) for c in codes):
            out.append(day.isoformat())
        day += datetime.timedelta(1)
    return "DATES:" + ",".join(out)


def entry(days, hours, notes) -> tuple:
    """(code, desc, time) of a route; (None, None, None) when it has no schedule."""
    text = normalize.clean_text(days)
    if not text:
        return None, None, None
    note = normalize.clean_text(notes)
    if re.search(r"\bonly\b", note, re.I):  # "Eastside 3rd Friday ONLY": that side's day
        text = f"{text} {note}"
    hours = normalize.clean_text(hours)
    hours = "" if "specified" in hours.lower() else hours.replace(".", "")
    parts = [citykit.side_entry(_words(p), hours, enforced=bool(hours)) for p in text.split("/")]
    if len(parts) == 1:
        return parts[0]
    hits = [citykit.code_from_text(_words(p)) for p in text.split("/")]
    desc = " and ".join(h[1] for h in hits)
    if not hours:
        return citykit.COURTESY, f"{desc}, no tickets", None
    return _dates([h[0] for h in hits]), desc, parts[0][2]


def build(routes: geopandas.GeoDataFrame, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows: county blocks along a curb route."""
    curbs = routes[~routes["Notes"].fillna("").str.contains("island|lot", case=False)]
    curbs = curbs[curbs.geometry.notna()].to_crs("EPSG:26910")
    zones = curbs.set_geometry(curbs.geometry.buffer(_BUFFER_M))
    out = citykit.zone_blocks(
        lines, zones, lambda r: entry(r["DayOfWeek"], r["TimeOfDay"], r["Notes"]), min_share=0.6
    )
    out["STREET_NAME"] = out["STREET_NAME"].map(pdf_city.street_label)
    return out


def main():
    data_dir = Path(REPO_ROOT) / "data" / "san_bruno"
    inputs = [data_dir / "routes.geojson", data_dir / "centerlines.geojson"]
    if not all(p.exists() for p in inputs):
        raise FileNotFoundError(f"inputs missing in {data_dir}; run rebuild_city_data.py san_bruno")
    routes = geopandas.read_file(inputs[0])
    lines = citykit.centerlines(inputs[1], "san_mateo")
    pdf_city.write(build(routes, lines), data_dir / "StreetSweeping.geojson")


if __name__ == "__main__":
    main()
