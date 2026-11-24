"""Trash pickup days for a home, by the city manifest's `trash:` block:

kind: recollect  area + service_id of the city's ReCollect (recollect.net) widget.
                 Unofficial API, best effort: any failure yields no pickups. Answers
                 are cached (failures briefly) so a home re-check never hammers it.
kind: weekly     no lookup exists: the user picks the home's collection weekday and
                 the city's holiday rule moves it (schedule.weekly_pickups).
"""

import datetime

from broombuster import net, schedule
from broombuster.cities import CITIES
from broombuster.feeds import TTLCache

_API = "https://api.recollect.net/api"
_TIMEOUT_S = 3
_PLACE_TTL_S = 30 * 24 * 3600
_PICKUPS_TTL_S = 12 * 3600
DAYS_AHEAD = 56  # the calendar shows about two months of pickups
WEEKLY_LABEL = "Trash, compost and recycling"

cache = TTLCache(max_items=2048, failure_ttl=5 * 60, wait_s=5 * _TIMEOUT_S)  # bounded
_MISS = object()
Busy = net.Busy  # every ReCollect slot taken: local congestion, not cached as an outage


def _get(path: str, **params):
    return net.get_json(f"{_API}/{path}", _TIMEOUT_S, **params, locale="en-US")


def parse_pickups(events, today: datetime.date) -> dict[str, list[str]]:
    """ReCollect events -> {stream label: sorted ISO dates from today on}."""
    out: dict[str, set[str]] = {}
    for e in events or []:
        try:
            day = datetime.date.fromisoformat(e.get("day"))
        except (TypeError, ValueError):
            continue
        if day < today:
            continue
        for flag in e.get("flags") or []:
            label = flag.get("subject") or flag.get("name")
            if flag.get("event_type") == "pickup" and label:
                out.setdefault(str(label), set()).add(day.isoformat())
    return {k: sorted(v) for k, v in out.items()}


def kind(city_key: str) -> str | None:
    """The city's trash lookup kind ("recollect", "weekly"), None without one."""
    cfg = CITIES[city_key].get("trash")
    return cfg.get("kind", "recollect") if cfg else None


def pickups(
    city_key: str,
    address: str,
    today: datetime.date,
    collection_day: int | None = None,
    *,
    network: bool = True,
) -> dict[str, list[str]] | None:
    """{stream: [ISO dates]} for the next DAYS_AHEAD days at a home, or {}.

    collection_day (Mon=0..Sun=6) is the weekday the user picked, for kind weekly.
    network=False: cached ReCollect answers only; None when a lookup is not cached."""
    k = kind(city_key)
    if k == "weekly":
        if collection_day is None:
            return {}
        days = schedule.weekly_pickups(city_key, collection_day, today, DAYS_AHEAD)
        return {WEEKLY_LABEL: [d.isoformat() for d in days]} if days else {}
    cfg = CITIES[city_key].get("trash")
    if k != "recollect" or not address.strip():
        return {}
    area, service = cfg["area"], cfg["service_id"]

    def place():
        hits = _get(f"areas/{area}/services/{service}/address-suggest", q=address)
        return hits[0].get("place_id") if isinstance(hits, list) and hits else None

    place_key = ("place", area, " ".join(address.lower().split()))
    try:
        place_id = (
            cache.get(place_key, _PLACE_TTL_S, place) if network else cache.peek(place_key, _MISS)
        )
    except Busy:
        return {}
    if place_id is _MISS:
        return None
    if not place_id:
        return {}

    def events():
        data = _get(
            f"places/{place_id}/services/{service}/events",
            nomerge=1,
            hide="reminder_only",
            after=today.isoformat(),
            before=(today + datetime.timedelta(days=DAYS_AHEAD)).isoformat(),
        )
        if data is None:
            return None
        return parse_pickups(data.get("events", []) if isinstance(data, dict) else data, today)

    events_key = ("events", place_id, today.isoformat())
    if not network:
        got = cache.peek(events_key, _MISS)
        return None if got is _MISS else got or {}
    try:
        return cache.get(events_key, _PICKUPS_TTL_S, events) or {}
    except Busy:
        return {}
