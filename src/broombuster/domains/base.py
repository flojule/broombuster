"""Domain plugin contracts.

A `DomainPlugin` is a stateless object that knows how to handle one kind of
city data (street sweeping, trash days, parking permit zones, …). It
answers three questions:

  1. Does it support a given city?       (`supports_city`)
  2. What does the car-shaped resolved row look like for this domain?
                                          (`resolve_for`)
  3. What's the per-domain answer for this car at this moment?
                                          (`format`)

Plugins are pure: stateless objects, no I/O at registration, no
side effects. The runtime calls them per /check request, passing in the
already-loaded GeoDataFrames and the resolver result.

Step 3 keeps the surface deliberately tiny — just enough that the existing
sweeping code path becomes a plugin without losing any behaviour. New
hooks (lazy data loading, per-domain caching, async I/O) can be added
later when a real domain demands them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class DomainResult:
    """Per-domain answer for one /check call.

    `domain_id`        Stable machine identifier (e.g. "sweeping", "trash").
                       Becomes `properties.domain` on every GeoJSON feature
                       this plugin contributes, and the dict key under
                       `/check` response's `domains[]`.
    `label`            Human-readable card header ("Street sweeping").
    `urgency`          One of "today" | "tomorrow" | "safe".
    `schedule_lines`   Plain-text bullet lines for the card body.
    `extras`           Plugin-specific structured fields the frontend may
                       choose to render (e.g. {"car_side": "even",
                       "schedule_even": [...], "schedule_odd": [...]}).
                       Kept open-ended so plugins evolve without breaking
                       the protocol.
    """
    domain_id: str
    label: str
    urgency: str  # "today" | "tomorrow" | "safe"
    schedule_lines: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DomainPlugin(Protocol):
    """Structural type for a domain plugin.

    Implementations attach `domain_id`, `label`, and `subject` as class
    attributes and define the methods below. They are constructed once at
    import time (in `registry.py`) and reused for every request.

    `subject` is what the domain is located against: "car" (parked location,
    e.g. street sweeping) or "home" (residence, e.g. trash collection). The
    runtime resolves car-subject plugins at the car coordinate and
    home-subject plugins at the saved home coordinate.
    """

    domain_id: str
    label: str
    subject: str  # "car" | "home"

    def supports_city(self, city_key: str) -> bool:
        """Whether the plugin has data for this city."""
        ...

    def resolve_for(self, gdf_3857: Any, lat: float, lon: float,
                    city_key: str, address: Optional[str] = None) -> Optional[Any]:
        """Resolve a coordinate (and optional address) to a per-domain row, or None.

        Car-subject plugins delegate to `broombuster.resolve.resolve_car_segment`.
        `address` is supplied for home-subject lookups that need a postal
        address (e.g. the ReCollect trash adapter); other plugins ignore it.
        The return type is intentionally `Any` — each plugin picks the shape
        most natural for its `format` step.
        """
        ...

    def format(self, resolved: Any, gdf_3857: Any,
               local_now: datetime) -> DomainResult:
        """Build the per-domain answer from the resolved row.

        `gdf_3857` is the same GeoDataFrame the resolver consulted. The
        plugin may use it to find sibling rows that describe the same
        physical segment (e.g. SF emits one row per weekday for the same
        street, and the per-card schedule must reflect the union of all
        of them, not just the one row the resolver happened to pick).
        """
        ...


_URGENCY_PRIORITY = {"today": 2, "tomorrow": 1, "safe": 0}


def max_urgency(*urgencies: str) -> str:
    """Combine per-domain urgencies into one panel-level urgency.

    Used by the frontend (and a future banner) to pick the worst case
    across all domains for one car. `today` beats `tomorrow` beats `safe`.
    Unknown values are treated as `safe`.
    """
    best = "safe"
    best_p = 0
    for u in urgencies:
        p = _URGENCY_PRIORITY.get(u, 0)
        if p > best_p:
            best, best_p = u, p
    return best
