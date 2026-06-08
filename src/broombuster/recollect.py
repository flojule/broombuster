"""ReCollect adapter: address -> collection-day pickups for a city.

ReCollect (recollect.net) powers many municipal "when is my collection day"
widgets, including Oakland's oaklandrecycles.com. This is an UNOFFICIAL,
best-effort integration against the same public widget API the city site
uses; it is gated by RECOLLECT_ENABLED and must never block a response.

Flow per lookup:
  1. address-suggest: free-text address -> place_id   (cached, long TTL)
  2. events:          place_id -> dated pickups        (cached, short TTL)

A city opts in via its manifest:
    trash:
      kind: recollect
      area: OaklandCA          # ReCollect area id
      service_id: 608          # ReCollect service id
"""

from __future__ import annotations

import datetime
import os
import threading
import time
from typing import Optional

import requests

_HOST = os.environ.get("RECOLLECT_HOST", "https://api.recollect.net")
_TIMEOUT_S = float(os.environ.get("RECOLLECT_TIMEOUT_S", "8"))


def enabled() -> bool:
    """Whether the unofficial ReCollect integration is allowed to make calls."""
    return os.environ.get("RECOLLECT_ENABLED", "1").lower() in ("1", "true", "yes")


# place cache: (area, service_id, norm_address) -> (ts, place_id|None)
_PLACE_TTL_S = 30 * 24 * 3600
# pickups cache: (place_id, service_id, today_iso) -> (ts, {stream: [date,...]})
_PICKUPS_TTL_S = 12 * 3600

_place_cache: dict[tuple, tuple[float, Optional[str]]] = {}
_pickups_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def _norm(address: str) -> str:
    return " ".join(address.lower().split())


def suggest_place(area: str, service_id, address: str) -> Optional[str]:
    """Resolve a free-text address to a ReCollect place_id, or None."""
    if not enabled() or not address or not address.strip():
        return None
    key = (area, str(service_id), _norm(address))
    now = time.time()
    with _lock:
        hit = _place_cache.get(key)
        if hit and now - hit[0] < _PLACE_TTL_S:
            return hit[1]
    place_id = None
    try:
        r = requests.get(
            f"{_HOST}/api/areas/{area}/services/{service_id}/address-suggest",
            params={"q": address, "locale": "en-US"},
            timeout=_TIMEOUT_S,
        )
        r.raise_for_status()
        suggestions = r.json()
        if isinstance(suggestions, list) and suggestions:
            place_id = suggestions[0].get("place_id")
    except (requests.RequestException, ValueError):
        place_id = None
    with _lock:
        _place_cache[key] = (now, place_id)
    return place_id


def fetch_pickups(place_id: str, service_id, *,
                  today: Optional[datetime.date] = None,
                  days: int = 21) -> dict[str, list[datetime.date]]:
    """Return {stream_label: [upcoming pickup dates]} for a place, or {}.

    Stream labels are ReCollect's own (e.g. "Trash", "Compost"); the set
    varies by city/service, so callers render whatever is returned.
    """
    if not enabled() or not place_id:
        return {}
    today = today or datetime.date.today()
    key = (place_id, str(service_id), today.isoformat())
    now = time.time()
    with _lock:
        hit = _pickups_cache.get(key)
        if hit and now - hit[0] < _PICKUPS_TTL_S:
            return hit[1]
    pickups: dict[str, list[datetime.date]] = {}
    try:
        r = requests.get(
            f"{_HOST}/api/places/{place_id}/services/{service_id}/events",
            params={
                "nomerge": 1, "hide": "reminder_only",
                "after": today.isoformat(),
                "before": (today + datetime.timedelta(days=days)).isoformat(),
                "locale": "en-US",
            },
            timeout=_TIMEOUT_S,
        )
        r.raise_for_status()
        data = r.json()
        events = data.get("events", data) if isinstance(data, dict) else data
        pickups = parse_pickups(events, today)
    except (requests.RequestException, ValueError):
        pickups = {}
    with _lock:
        _pickups_cache[key] = (now, pickups)
    return pickups


def parse_pickups(events, today: datetime.date) -> dict[str, list[datetime.date]]:
    """Pure parser: ReCollect events list -> {stream_label: sorted [dates]}."""
    out: dict[str, list[datetime.date]] = {}
    for e in events or []:
        day = e.get("day")
        try:
            d = datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        if d < today:
            continue
        for flag in e.get("flags", []) or []:
            if flag.get("event_type") != "pickup":
                continue
            label = flag.get("subject") or flag.get("name")
            if not label:
                continue
            out.setdefault(str(label), []).append(d)
    return {k: sorted(set(v)) for k, v in out.items()}


def clear_caches() -> None:
    """Drop both caches (tests)."""
    with _lock:
        _place_cache.clear()
        _pickups_cache.clear()
