"""Alerts at a place (Saved summary): National Weather Service watches and warnings that
change where to park (Red Flag, winter storm, flood, …). Cached; a failing source is
left out."""

import re

from broombuster import feeds, net

_NWS = "https://api.weather.gov/alerts/active"
# NWS events that change where to park: tow-away fire zones, snow routes, flooded streets.
_EVENTS = re.compile(
    r"red flag|fire weather|winter storm|blizzard|snow|ice storm|flood|tsunami|evacuation",
    re.I,
)


def _nws(lat: float, lon: float):
    data = feeds.get_json(_NWS, point=f"{lat:.3f},{lon:.3f}")
    if not isinstance(data, dict):
        return None
    out = []
    for f in data.get("features") or []:
        p = (f.get("properties") if isinstance(f, dict) else None) or {}
        if not isinstance(p, dict):
            continue
        event = p.get("event") or ""
        if not _EVENTS.search(event):
            continue
        out.append(
            {
                "title": event,
                "until": p.get("ends") or p.get("expires") or "",
                "detail": "",  # NWS headline only repeats the event, times and office
                "source": "National Weather Service",
                "severity": p.get("severity") or "",
                "url": "https://alerts.weather.gov/",
            }
        )
    return out


def at(lat: float, lon: float) -> list[dict]:
    """Active alerts at a point (rounded to ~5 km for the cache)."""
    key = (round(lat, 2), round(lon, 2))
    try:
        return list(feeds.cache.get(("nws", key), 10 * 60, lambda: _nws(*key)) or [])
    except net.Busy:
        return []
