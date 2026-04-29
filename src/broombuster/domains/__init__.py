"""City-data domain plugins.

Each domain (street sweeping, trash days, parking permit zones, …) is a
plugin that knows how to:

  - tell whether it supports a given city          (`supports_city`)
  - resolve a car coordinate to its relevant row   (`resolve_for`)
  - format a per-domain answer                     (`format`)

The active registry is built by `broombuster.domains.registry` and
consumed by `broombuster.api.app:/check`. Adding a new plugin is one
file and one line — a new module here, plus appending to
`registry._REGISTRY`.
"""

from broombuster.domains.base import DomainPlugin, DomainResult, max_urgency
from broombuster.domains.registry import for_city, iter_plugins

__all__ = [
    "DomainPlugin",
    "DomainResult",
    "max_urgency",
    "for_city",
    "iter_plugins",
]
