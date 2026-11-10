"""City holidays (data/holidays/<city>.yaml, named by a manifest's `holidays:`) and
the services each suspends: "sweeping" (no sweeping that day), "sweeping_day" (only
sweeps starting at or after DAYTIME_FROM are off: San Francisco enforces night
cleaning on most holidays) and "trash" (the city's trash holiday rule applies)."""

import datetime
import functools
import os

import yaml

from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

SERVICES = ("sweeping", "sweeping_day", "trash")
DAYTIME_FROM = 6 * 60  # minutes after midnight; urgency.js DAYTIME_FROM


@functools.cache
def for_city(city_key: str) -> dict[datetime.date, dict]:
    """{date: {"name", "suspends"}} for a city ({} without a `holidays:` file)."""
    rel = CITIES[city_key].get("holidays")
    if not rel:
        return {}
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out = {}
    for h in doc.get("holidays") or []:
        day = h["date"]
        if not isinstance(day, datetime.date):
            day = datetime.date.fromisoformat(day)
        suspends = list(h.get("suspends") or [])
        unknown = set(suspends) - set(SERVICES)
        if unknown:
            raise ValueError(f"{rel}: {day} suspends unknown services {sorted(unknown)}")
        out[day] = {"name": str(h["name"]), "suspends": suspends}
    return out


def source(city_key: str) -> dict:
    """{"source", "last_verified"} of a city's holiday file ({} without one)."""
    rel = CITIES[city_key].get("holidays")
    if not rel:
        return {}
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return {"source": doc.get("source"), "last_verified": str(doc.get("last_verified") or "")}


def suspended(city_key: str, service: str, day: datetime.date) -> str | None:
    """The holiday's name when `service` is suspended in the city that day, else None."""
    h = for_city(city_key).get(day)
    return h["name"] if h and service in h["suspends"] else None


def sweep_suspended(city_key: str, day: datetime.date, window) -> str | None:
    """The holiday's name when a sweep with time window `window` ((start, end) minutes,
    None when untimed) is off in the city that day, else None."""
    h = for_city(city_key).get(day)
    if not h:
        return None
    if "sweeping" in h["suspends"]:
        return h["name"]
    daytime = window is not None and DAYTIME_FROM <= window[0] < window[1]
    return h["name"] if daytime and "sweeping_day" in h["suspends"] else None


def for_region(region_key: str) -> dict[str, dict[str, dict]]:
    """{city: {iso date: {"name", "suspends"}}} for the cities of a region (the
    client's copy, served by /api/cities)."""
    return {
        ck: {d.isoformat(): h for d, h in sorted(for_city(ck).items())}
        for ck in REGIONS[region_key]["cities"]
        if for_city(ck)
    }
