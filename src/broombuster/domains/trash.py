"""Trash-collection domain plugin backed by ReCollect (address-based).

A city opts in via a `trash:` block in its manifest:

    trash:
      kind: recollect
      area: OaklandCA      # ReCollect area id
      service_id: 608      # ReCollect service id
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from broombuster import recollect
from broombuster.cities import CITIES
from broombuster.domains.base import DomainResult


def _trash_config(city_key: str, cities: dict | None = None) -> dict:
    src = cities if cities is not None else CITIES
    return (src.get(city_key) or {}).get("trash") or {}


def _fmt_pickup_date(d) -> str:
    """Short pickup date, e.g. "Tue, Jun 9"."""
    return f"{d:%a, %b} {d.day}"


class ReCollectTrashPlugin:
    """Home-located trash plugin backed by the unofficial ReCollect API.

    Used by cities whose collection schedule is address-based (no zone GIS),
    e.g. Oakland (oaklandrecycles.com). Best-effort: any failure yields a
    `safe` "unavailable" card and never blocks the response.
    """

    domain_id: str = "trash"
    label: str = "Trash day"
    subject: str = "home"

    def __init__(self, cities: dict | None = None):
        self._cities = cities

    def supports_city(self, city_key: str) -> bool:
        return _trash_config(city_key, self._cities).get("kind") == "recollect"

    def resolve_for(self, gdf_3857: Any, lat: float, lon: float,
                    city_key: str, address: str | None = None) -> dict | None:
        if not address or not recollect.enabled():
            return None
        cfg = _trash_config(city_key, self._cities)
        area, service_id = cfg.get("area"), cfg.get("service_id")
        if not area or service_id is None:
            return None
        place_id = recollect.suggest_place(area, service_id, address)
        if not place_id:
            return None
        return {"place_id": place_id, "service_id": service_id, "address": address}

    def format(self, resolved: Any, gdf_3857: Any,
               local_now: datetime) -> DomainResult:
        if not resolved:
            return DomainResult(
                domain_id=self.domain_id, label=self.label, urgency="safe",
                schedule_lines=["Trash schedule unavailable"],
                extras={"streams": {}},
            )
        today = local_now.date()
        tomorrow = today + timedelta(days=1)
        pickups = recollect.fetch_pickups(
            resolved["place_id"], resolved["service_id"], today=today,
        )
        if not pickups:
            return DomainResult(
                domain_id=self.domain_id, label=self.label, urgency="safe",
                schedule_lines=["No upcoming collection found"],
                extras={"streams": {}, "address": resolved.get("address")},
            )

        lines: list[str] = []
        soonest = None
        for stream, dates in sorted(pickups.items()):
            if not dates:
                continue
            nxt = dates[0]
            lines.append(f"{stream}: {_fmt_pickup_date(nxt)}")
            soonest = nxt if soonest is None else min(soonest, nxt)

        urgency = ("today" if soonest == today
                   else "tomorrow" if soonest == tomorrow else "safe")
        return DomainResult(
            domain_id=self.domain_id, label=self.label, urgency=urgency,
            schedule_lines=lines or ["No upcoming collection found"],
            extras={
                "streams": {k: [d.isoformat() for d in v] for k, v in pickups.items()},
                "place_id": resolved["place_id"],
                "address": resolved.get("address"),
            },
        )
