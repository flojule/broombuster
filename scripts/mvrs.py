"""Mill Valley Refuse Service's street sweeping page (one tab per town: street lists under
"1st Monday of the Month" headings) laid onto the county's road centerlines. Used by
scripts/build_{mill_valley,corte_madera,tiburon}_geojson.py.

The page asks nothing of parked cars and no sign is posted: every swept block is COURTESY.
A "(from) X to Y" note keeps the street's blocks between those cross streets; any other note
("partial", "upper") stays in the description on the whole street.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import citykit
import geopandas
import pdf_city
import requests

from broombuster.config import REPO_ROOT

PAGE = "https://www.millvalleyrefuse.com/residential-street-sweeping"
_TOKEN_RE = re.compile(
    r"elementor-tab-mobile-title[^>]*>(?P<town>[^<]+)<"
    r"|data-table-header-text\">(?P<head>[^<]+)<"
    r"|<li(?: class=\"li_col\d+\")?>(?P<item>[^<]+)</li>"
)
_BOTH_RE = re.compile(r"^(.*?)\s+(E\.?|East)\s*&\s*(W\.?|West)$")
_LIMITS_RE = re.compile(r"^(?:from\s+)?(.+?)\s+to\s+(.+)$", re.I)
# Page spellings the county writes differently (after street type clean-up).
_TYPES = {"PSG": "PASSAGE", "COVE": "CV", "TERRACE": "TER"}
_ALIASES = {
    "NED'S WAY": "NEDS WAY",
    "MOUNT TIBURON CT": "MT TIBURON CT",
    "MOUNT TIBURON RD": "MT TIBURON RD",
    "ST GABRIELLE CT": "ST GABRIELE CT",
    "STONEY HILL RD": "STONY HILL RD",
    "ROCKHILL DR": "ROCK HILL RD",
    "SOUTHRIDGE RD E": "SOUTHRIDGE EAST",
    "SOUTHRIDGE RD W": "SOUTHRIDGE WEST",
    "VIA PARAISO EAST": "VIA PARAISO E",
    "VIA PARAISO WEST": "VIA PARAISO W",
    "MILLAY PLACE ST": "MILLAY PL",
    "HIGHWAY 101 FRONTAGE ROAD": "REDWOOD HWY FRONTAGE RD",
}


def fetch() -> str:
    resp = requests.get(PAGE, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def lists(page: str) -> dict[str, list[tuple[str, str]]]:
    """{town: [(street as listed, schedule heading)]}."""
    out: dict[str, list[tuple[str, str]]] = {}
    town = head = None
    for m in _TOKEN_RE.finditer(page):
        if m["town"]:
            town, head = html.unescape(m["town"]).strip(), None
        elif m["head"]:
            head = html.unescape(m["head"]).strip()
        elif town and head:
            out.setdefault(town, []).append((html.unescape(m["item"]).strip(), head))
    return out


def street_parts(listed: str) -> tuple[list[str], str]:
    """(street names, note) of a listed entry: "Via Paraiso E. & W." is two streets."""
    note = ""
    if m := re.match(r"(.*?)\s*\((.*)\)\s*$", listed):
        listed, note = m[1], m[2].strip()
    listed = listed.replace("\u2019", "'")
    if m := _BOTH_RE.match(listed):
        names = [f"{m[1]} East", f"{m[1]} West"]
    else:
        names = [listed]
    labels = [pdf_city.street_label(n.replace(".", " ")).split() for n in names]
    labels = [" ".join([*t[:-1], _TYPES.get(t[-1], t[-1])]) for t in labels]
    return [_ALIASES.get(n, n) for n in labels], note


def join(entries, lines: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Standard-schema rows from [(street as listed, schedule words)]."""
    words: dict[int, list[str]] = {}
    for listed, sched in entries:
        names, note = street_parts(listed)
        for name in names:
            hit = citykit.blocks_named(lines, name)
            text = f"{sched} ({note})" if note else sched
            if note and (lim := _LIMITS_RE.match(note)):
                part = citykit.between(lines, name, lim[1], lim[2])
                if len(part):
                    hit, text = lines.loc[part], sched
            if hit.empty:
                print(f"  ⚠  {listed}: no county centerline")
            for i in hit.index:
                words.setdefault(i, []).append(text)
    out = lines.loc[sorted(words)].copy()
    rows = [citykit.courtesy_entry(words[i]) for i in out.index]
    for side in ("EVEN", "ODD"):
        out[f"DAY_{side}"], out[f"DESC_{side}"], out[f"TIME_{side}"] = zip(*rows, strict=True)
    return out.drop(columns=["STREET_KEY"]).reset_index(drop=True)


def build(city_key: str, town: str, every: dict[str, str], extra=()) -> None:
    """Write data/<city_key>/StreetSweeping.geojson: the town's lists, the streets the page
    names as swept every week (`every`), and `extra` (street, schedule) entries."""
    data_dir = Path(REPO_ROOT) / "data" / city_key
    listed = lists(fetch()).get(town, [])
    if len(listed) < 20:
        raise SystemExit(f"{PAGE}: {len(listed)} streets for {town}; has the page changed?")
    lines = citykit.centerlines(data_dir / "centerlines.geojson", "marin")
    entries = [*listed, *every.items(), *extra]
    pdf_city.write(join(entries, lines), data_dir / "StreetSweeping.geojson")
