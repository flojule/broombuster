"""Street-sweeping domain plugin.

The first concrete `DomainPlugin`. It wraps the existing pure functions
in `broombuster.analysis` and `broombuster.resolve` — no behavior change,
just exposes them through the plugin shape so future domains (trash,
parking permits) can slot in alongside.

The legacy `compose_message` (sweeping-specific plain-text formatter,
returned in the legacy /check `message` field) lives here too because
its rules — highlight the car's side with ►, dedup entries — are
sweeping-shaped and would not transfer to other domains.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from broombuster import analysis, resolve
from broombuster.cities import CITIES
from broombuster.domains.base import DomainResult

# ---------------------------------------------------------------------------
# Output formatter (used by /check legacy `message` field and the CLI)
# ---------------------------------------------------------------------------


def compose_message(schedule_even, schedule_odd, car_side, local_now=None,
                    labels=analysis.DEFAULT_SIDE_LABELS):
    """Plain-text schedule summary (CLI, email): one line per side, ► on the car's.

    Built on analysis.side_groups, so it reads like the card and map hover.
    """
    groups = analysis.side_groups(schedule_even, schedule_odd, car_side, labels, local_now)
    if groups[0][0] is None:
        return f"► Street: {' / '.join(groups[0][2])}"
    return "\n".join(
        f"{'►' if side == car_side else ' '} {label} side: "
        f"{' / '.join(parts) if parts else 'no sweeping'}"
        for side, label, parts in groups
    )


# ---------------------------------------------------------------------------
# DomainPlugin implementation
# ---------------------------------------------------------------------------

# The schemas a sweeping plugin understands — i.e. cities whose normalised
# GeoDataFrame carries DAY_EVEN / DAY_ODD columns. Any city configured with
# a different schema should be handled by a different plugin.
_SUPPORTED_SCHEMAS = frozenset({"oakland", "sf", "chicago", "berkeley", "alameda"})


class SweepingPlugin:
    """Street-sweeping domain plugin (the first concrete DomainPlugin)."""

    domain_id: str = "sweeping"
    label: str = "Street sweeping"
    subject: str = "car"

    def supports_city(self, city_key: str) -> bool:
        city = CITIES.get(city_key) or {}
        return city.get("schema") in _SUPPORTED_SCHEMAS

    def resolve_for(self, gdf_3857, lat: float, lon: float,
                    city_key: str, address: Optional[str] = None
                    ) -> Optional[resolve.ResolvedCar]:
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
                },
            )

        # Union schedules across every GDF row that describes the same
        # physical segment. SF emits one row per (segment × weekday); without
        # this union the card would only show one weekday while the map
        # paints all of them. Falls back to single-row when gdf is None
        # (test fixtures) or for polygon zones (Chicago — one row per zone).
        if gdf_3857 is not None:
            schedule_even, schedule_odd = analysis.schedules_for_all_matching_rows(
                gdf_3857, resolved
            )
        else:
            schedule_even, schedule_odd = analysis.schedules_for_segment(resolved.segment)

        # Urgency is the union of both sides: warn whichever side is swept.
        raw_urgency = analysis.check_day_street_sweeping(
            list(schedule_even) + list(schedule_odd), local_now=local_now
        )
        urgency = raw_urgency if raw_urgency in ("today", "tomorrow") else "safe"

        # schedule_even/odd stay RAW (code, desc, time) tuples in the response;
        # the client formats them via the JS port. side is None when unknown.
        car_side = resolved.side
        labels = analysis.side_labels(resolved.segment)
        message = compose_message(schedule_even, schedule_odd, car_side, local_now, labels)
        lines = (analysis.side_lines(schedule_even, schedule_odd, car_side, labels, local_now)
                 or ["No sweeping scheduled"])

        return DomainResult(
            domain_id=self.domain_id,
            label=self.label,
            urgency=urgency,
            schedule_lines=lines,
            extras={
                "car_side": car_side,
                "side_labels": list(labels),
                "schedule_even": schedule_even,
                "schedule_odd": schedule_odd,
                "message": message,
            },
        )
