"""Alerts at a place (Saved summary): National Weather Service watches and warnings
(Red Flag, heat, winter storm, …) and, in the Bay Area, a Spare the Air Alert (no wood
burning). Cached; a failing source is left out."""

import re

import requests

from broombuster import feeds

_NWS = "https://api.weather.gov/alerts/active"
_STA = "https://www.baaqmd.gov/alertstatus"
# NWS events worth telling someone who parks on the street.
_EVENTS = re.compile(r"red flag|fire weather|winter storm|blizzard|snow|ice storm|flood|"
                     r"high wind|wind advisory|excessive heat|extreme heat|heat advisory|"
                     r"tsunami|evacuation|air quality", re.I)


def _nws(lat: float, lon: float):
    data = feeds.get_json(_NWS, point=f"{lat:.3f},{lon:.3f}")
    if data is None:
        return None
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        event = p.get("event") or ""
        if not _EVENTS.search(event):
            continue
        out.append({"title": event, "until": p.get("ends") or p.get("expires") or "",
                    "detail": p.get("headline") or "", "source": "National Weather Service",
                    "severity": p.get("severity") or "", "url": "https://alerts.weather.gov/"})
    return out


def parse_spare_the_air(page: str) -> bool | None:
    """True / False for the alert on BAAQMD's status page; None when the page reads
    neither way (it changed: say nothing rather than something wrong)."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    if re.search(r"There is no Spare the Air Alert in effect", text, re.I):
        return False
    if re.search(r"Spare the Air Alert (is )?in effect", text, re.I):
        return True
    return None


def _spare_the_air():
    try:
        r = requests.get(_STA, headers={"User-Agent": "Mozilla/5.0 (BroomBuster)"},
                         timeout=feeds.TIMEOUT_S)
        r.raise_for_status()
    except requests.RequestException:
        return None
    on = parse_spare_the_air(r.text)
    if on is None:
        return None
    return [{"title": "Spare the Air Alert", "until": "", "severity": "Moderate",
             "detail": "No wood burning, indoors or out; drive less.",
             "source": "Bay Area Air District", "url": _STA}] if on else []


def at(region_key: str, lat: float, lon: float) -> list[dict]:
    """Active alerts at a point (rounded to ~5 km for the cache)."""
    key = (round(lat, 2), round(lon, 2))
    out = list(feeds.cache.get(("nws", key), 10 * 60, lambda: _nws(*key)) or [])
    if region_key == "bay_area":
        out += feeds.cache.get(("spare_the_air",), 30 * 60, _spare_the_air) or []
    return out
