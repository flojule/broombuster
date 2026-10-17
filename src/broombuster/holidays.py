"""City holidays (data/holidays/<city>.yaml, named by a manifest's `holidays:`) and
the services each suspends: "sweeping" (no sweeping that day) and "trash" (the
city's trash holiday rule applies)."""

import datetime
import functools
import os

import yaml

from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

SERVICES = ("sweeping", "trash")


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


def for_region(region_key: str) -> dict[str, dict[str, dict]]:
    """{city: {iso date: {"name", "suspends"}}} for the cities of a region (the
    client's copy, served by /api/cities)."""
    return {ck: {d.isoformat(): h for d, h in sorted(for_city(ck).items())}
            for ck in REGIONS[region_key]["cities"] if for_city(ck)}
