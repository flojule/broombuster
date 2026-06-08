"""Active list of domain plugins.

The registry is a module-level singleton populated at import time. Adding
a new domain (Step 4 onward: trash, parking permit zones, …) means
appending to `_REGISTRY` and importing the new plugin module.

Tests override the registry by patching `_REGISTRY` directly; production
code consumes it through `iter_plugins()` and `for_city()` so the global
is read but never mutated at request time.
"""

from __future__ import annotations

from typing import Iterable

from broombuster.domains.base import DomainPlugin
from broombuster.domains.sweeping import SweepingPlugin
from broombuster.domains.trash import ReCollectTrashPlugin, ZoneTrashPlugin

# Order matters: plugins listed first appear first in /check responses.
# The frontend renders cards in this order, so put the most safety-critical
# domain (street sweeping) at the top. Trash plugins are inert until a city
# opts in via a `trash:` manifest block; the zone vs recollect kinds are
# mutually exclusive, so at most one trash plugin runs per city.
_REGISTRY: list[DomainPlugin] = [
    SweepingPlugin(),
    ZoneTrashPlugin(),
    ReCollectTrashPlugin(),
]


def iter_plugins() -> Iterable[DomainPlugin]:
    """Every registered plugin, in stable order."""
    return iter(_REGISTRY)


def for_city(city_key: str) -> list[DomainPlugin]:
    """Plugins that report support for the given city, in registry order."""
    return [p for p in _REGISTRY if p.supports_city(city_key)]
