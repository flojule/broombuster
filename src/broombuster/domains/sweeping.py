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

import re
from datetime import datetime
from typing import Any, Optional

from broombuster import analysis, normalize, resolve
from broombuster.cities import CITIES
from broombuster.domains.base import DomainResult


# Mirrors the cleanup maps._clean_desc does on the hover text — keeps the
# card and hover formatting in lock-step. See maps.py for the original.
_DESC_REDUNDANT_RE = re.compile(r"\s*\(every\)", flags=re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _clean_desc(s: str) -> str:
    if not s or s == "N/A":
        return s
    s = _DESC_REDUNDANT_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Output formatter (used by /check legacy `message` field and the CLI)
# ---------------------------------------------------------------------------


def compose_message(schedule_even, schedule_odd, car_side):
    """Return a plain-text schedule summary matching the map info panel layout."""

    def _dedup_parts(entries):
        valid = [e for e in entries if e and len(e) >= 3]
        seen, parts = set(), []
        for entry in valid:
            key = (entry[1], entry[2])
            if key not in seen:
                t = entry[2]
                body = entry[1] if not t else f"{entry[1]} — {t}"
                parts.append(body)
                seen.add(key)
        return parts

    def _fmt_plain(parts, label, highlight):
        prefix = "►" if highlight else " "
        if not parts:
            return f"{prefix} {label}: no sweeping"
        return f"{prefix} {label}: {' / '.join(parts)}"

    even_parts = _dedup_parts(schedule_even)
    odd_parts  = _dedup_parts(schedule_odd)

    if even_parts and even_parts == odd_parts:
        return f"► Street: {' / '.join(even_parts)}"

    even_line = _fmt_plain(even_parts, "Even side", highlight=(car_side == "even"))
    odd_line  = _fmt_plain(odd_parts,  "Odd side",  highlight=(car_side == "odd"))
    return f"{even_line}\n{odd_line}"


# ---------------------------------------------------------------------------
# DomainPlugin implementation
# ---------------------------------------------------------------------------

# The schemas a sweeping plugin understands — i.e. cities whose normalised
# GeoDataFrame carries DAY_EVEN / DAY_ODD columns. Any city configured with
# a different schema should be handled by a different plugin.
_SUPPORTED_SCHEMAS = frozenset({"oakland", "sf", "chicago", "berkeley", "alameda"})


def _schedule_lines(schedule_even, schedule_odd, car_side: Optional[str]) -> list[str]:
    """Bullet lines for the per-domain card body.

    Mirrors the plain-text in compose_message but returned as a list so the
    frontend can render each line as its own DOM node. Highlight rule:
    when both sides have the same schedule, return one line; otherwise two
    (`Even: …` / `Odd: …`), with the car's side first.
    """

    def _dedup(entries):
        seen, out = set(), []
        for e in entries or []:
            if not e or len(e) < 2:
                continue
            desc = _clean_desc((e[1] or "").strip())
            time = normalize.time_display((e[2] if len(e) >= 3 else "").strip())
            if not desc:
                continue
            # Avoid duplicating the time when the desc already mentions it.
            # Compare canonical forms to be robust against en-dash vs hyphen.
            if not time or time == "N/A" or time in desc:
                body = desc
            else:
                body = f"{desc} — {time}"
            if body in seen:
                continue
            seen.add(body)
            out.append(body)
        return out

    even = _dedup(schedule_even)
    odd  = _dedup(schedule_odd)

    if even and even == odd:
        return [" / ".join(even)]

    lines: list[str] = []
    primary = ("even", even) if car_side == "even" else ("odd", odd)
    other   = ("odd",  odd)  if car_side == "even" else ("even", even)
    for label, parts in (primary, other):
        if parts:
            lines.append(f"{label.capitalize()}: {' / '.join(parts)}")
    if not lines:
        lines.append("No sweeping scheduled")
    return lines


class SweepingPlugin:
    """Street-sweeping domain plugin (the first concrete DomainPlugin)."""

    domain_id: str = "sweeping"
    label: str = "Street sweeping"

    def supports_city(self, city_key: str) -> bool:
        city = CITIES.get(city_key) or {}
        return city.get("schema") in _SUPPORTED_SCHEMAS

    def resolve_for(self, gdf_3857, lat: float, lon: float,
                    city_key: str) -> Optional[resolve.ResolvedCar]:
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

        car_side = resolved.side or "odd"
        message = compose_message(schedule_even, schedule_odd, car_side)
        lines = _schedule_lines(schedule_even, schedule_odd, car_side)

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
