"""City-data domain plugins.

Each domain (street sweeping, trash days, parking permit zones, …) is a
plugin that knows how to:

  - tell whether it supports a given city,
  - resolve a car coordinate to its relevant data row,
  - format a per-domain answer for the API response.

The active registry is built by `src/domains/registry.py` and consumed by
`api/api.py:/check`. New plugins are added by importing them in
`registry.py`'s `_REGISTRY` list — no other wiring required.

This package will hold the abstraction in Step 3. For now it carries only
the sweeping-specific output formatter (compose_message), since that
function is sweeping-shaped and belongs alongside the future
`SweepingPlugin` rather than in the email-alerts module.
"""
