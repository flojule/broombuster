"""Street-sweeping domain plugin.

Wraps the pure functions in `broombuster.analysis` and `broombuster.resolve`.
`schedule_lines` is the plain-text summary (CLI, email): both sides, the car's
side first, labelled when they differ. The web card formats the raw
`schedule_even` / `schedule_odd` itself so its urgency follows the live clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broombuster import analysis, maps, resolve
from broombuster.cities import CITIES
from broombuster.domains.base import DomainResult

# The schemas whose normalised GeoDataFrame carries DAY_EVEN / DAY_ODD.
_SUPPORTED_SCHEMAS = frozenset({"oakland", "sf", "chicago", "berkeley", "alameda"})


class SweepingPlugin:
    """Street-sweeping domain plugin."""

    domain_id: str = "sweeping"
    label: str = "Street sweeping"
    subject: str = "car"

    def supports_city(self, city_key: str) -> bool:
        return (CITIES.get(city_key) or {}).get("schema") in _SUPPORTED_SCHEMAS

    def resolve_for(self, gdf_3857, lat: float, lon: float,
                    city_key: str, address: str | None = None
                    ) -> resolve.ResolvedCar | None:
        return resolve.nearest_segment(gdf_3857, lat, lon)

    def format(self, resolved: Any, gdf_3857: Any,
               local_now: datetime) -> DomainResult:
        if resolved is None:
            return DomainResult(
                domain_id=self.domain_id,
                label=self.label,
                urgency="safe",
                schedule_lines=["No sweeping data — car not near a mapped street"],
                extras={
                    "car_side": None,
                    "side_labels": list(analysis.DEFAULT_SIDE_LABELS),
                    "schedule_even": [],
                    "schedule_odd": [],
                    "detail_html": "",
                },
            )

        # Union schedules across every GDF row that describes the same
        # physical segment: SF emits one row per (segment × weekday), and the
        # card must show all of them, as the map does. Falls back to the
        # single row when gdf is None (test fixtures).
        if gdf_3857 is not None:
            even, odd = analysis.schedules_for_all_matching_rows(gdf_3857, resolved)
        else:
            even, odd = analysis.schedules_for_segment(resolved.segment)

        # Urgency is the union of both sides: warn whichever side is swept.
        urgency = analysis.check_day_street_sweeping(even + odd, local_now=local_now)
        car_side = resolved.side  # None when unknown
        labels = analysis.side_labels(resolved.segment)
        detail_html = maps.zone_detail_html(
            resolved.label, even, odd, resolved.segment.get("_city"),
            local_now, car_side, labels,
        ) if even or odd else ""

        return DomainResult(
            domain_id=self.domain_id,
            label=self.label,
            urgency=urgency,
            schedule_lines=(analysis.side_lines(even, odd, car_side, labels, local_now)
                            or ["No sweeping scheduled"]),
            extras={
                "car_side": car_side,
                "side_labels": list(labels),
                # Raw (code, desc, time) tuples; the client formats them.
                "schedule_even": even,
                "schedule_odd": odd,
                "detail_html": detail_html,
            },
        )
