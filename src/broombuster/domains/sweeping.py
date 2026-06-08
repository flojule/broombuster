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


def compose_message(schedule_even, schedule_odd, car_side, local_now=None):
    """Return a plain-text schedule summary matching the map info panel layout.

    Both sides go through analysis.format_schedule_side, so the message, the
    card lines, and the map hover all read identically (canonical day-first,
    "Every <Wd>" merge, Mon->Sun order, next-cluster dates).
    """
    even = analysis.format_schedule_side(schedule_even, local_now)
    odd  = analysis.format_schedule_side(schedule_odd, local_now)

    if even and even == odd:
        return f"► Street: {' / '.join(even)}"

    def _fmt_plain(parts, label, highlight):
        prefix = "►" if highlight else " "
        if not parts:
            return f"{prefix} {label}: no sweeping"
        return f"{prefix} {label}: {' / '.join(parts)}"

    even_line = _fmt_plain(even, "Even side", highlight=(car_side == "even"))
    odd_line  = _fmt_plain(odd,  "Odd side",  highlight=(car_side == "odd"))
    return f"{even_line}\n{odd_line}"


# ---------------------------------------------------------------------------
# DomainPlugin implementation
# ---------------------------------------------------------------------------

# The schemas a sweeping plugin understands — i.e. cities whose normalised
# GeoDataFrame carries DAY_EVEN / DAY_ODD columns. Any city configured with
# a different schema should be handled by a different plugin.
_SUPPORTED_SCHEMAS = frozenset({"oakland", "sf", "chicago", "berkeley", "alameda"})


def _schedule_lines(schedule_even, schedule_odd, car_side: Optional[str],
                    local_now=None) -> list[str]:
    """Bullet lines for the per-domain card body — one display line per entry.

    Uses analysis.format_schedule_side (canonical day-first formatting,
    "Every <Wd>" merge, Mon->Sun order, merged next-cluster dates). When both
    sides match, the Even/Odd labels are dropped; otherwise each line is
    prefixed, car's side first.
    """
    even = analysis.format_schedule_side(schedule_even, local_now)
    odd  = analysis.format_schedule_side(schedule_odd, local_now)

    if even and even == odd:
        return even

    lines: list[str] = []
    primary = ("even", even) if car_side == "even" else ("odd", odd)
    other   = ("odd",  odd)  if car_side == "even" else ("even", even)
    for label, parts in (primary, other):
        for p in parts:
            lines.append(f"{label.capitalize()}: {p}")
    if not lines:
        lines.append("No sweeping scheduled")
    return lines


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
        try:
            return resolve.resolve_car_segment(
                gdf_3857, lat, lon,
                city_key=city_key, max_distance_m=50.0,
            )
        except resolve.NoSegmentNearby:
            return None

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

        # Urgency now reflects the union too: if any row has today/tomorrow,
        # the answer is today/tomorrow. We feed the union of both sides into
        # check_day_street_sweeping which already knows how to merge codes.
        all_schedules = list(schedule_even) + list(schedule_odd)
        raw_urgency = analysis.check_day_street_sweeping(
            all_schedules, local_now=local_now
        )
        urgency = raw_urgency if raw_urgency in ("today", "tomorrow") else "safe"

        # schedule_even/odd stay RAW (code, desc, time) tuples in the response;
        # the client formats them via the JS port. Date-dependent collapsing
        # (next sweep cluster for 'DATES:' codes) happens inside
        # format_schedule_side, keyed off local_now.
        car_side = resolved.side or "odd"
        message = compose_message(schedule_even, schedule_odd, car_side, local_now)
        lines = _schedule_lines(schedule_even, schedule_odd, car_side, local_now)

        return DomainResult(
            domain_id=self.domain_id,
            label=self.label,
            urgency=urgency,
            schedule_lines=lines,
            extras={
                "car_side": car_side,
                "schedule_even": schedule_even,
                "schedule_odd": schedule_odd,
                "message": message,
            },
        )
