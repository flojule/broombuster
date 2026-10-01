"""Active list of domain plugins.

Order matters: plugins listed first appear first in /check responses, so the
most safety-critical domain (street sweeping) leads. Tests override the
registry by patching `_REGISTRY`.
"""

from __future__ import annotations

from broombuster.domains.base import DomainPlugin
from broombuster.domains.sweeping import SweepingPlugin
from broombuster.domains.trash import ReCollectTrashPlugin

_REGISTRY: list[DomainPlugin] = [
    SweepingPlugin(),
    ReCollectTrashPlugin(),  # inert until a city's manifest has `trash: {kind: recollect}`
]


def for_city(city_key: str) -> list[DomainPlugin]:
    """Plugins that report support for the given city, in registry order."""
    return [p for p in _REGISTRY if p.supports_city(city_key)]


def get(domain_id: str) -> DomainPlugin:
    """The registered plugin with this id."""
    return next(p for p in _REGISTRY if p.domain_id == domain_id)
