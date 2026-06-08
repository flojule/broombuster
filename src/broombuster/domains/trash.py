"""Trash-collection domain plugin (zone-based).

`ZoneTrashPlugin` answers garbage/recycling/organics collection for cities
whose curbside schedule is published as collection-day zone polygons (the
Chicago-sweeping data shape). It reuses the sweeping day-code vocabulary and
analysis helpers verbatim: each stream stores an Oakland-style code (e.g.
"ME", "M13", "DATES:...") so `parse_sweeping_code`, `format_schedule_side`,
and `check_day_street_sweeping` apply unchanged.

A city opts in via a `trash:` block in its manifest:

    trash:
      kind: zone
      fgb_path: data/<city>/TrashZones.fgb
      streams:                       # optional; defaults to Garbage/Recycle/Organics
        - {label: Garbage,   column: GARBAGE}
        - {label: Recycling, column: RECYCLE}
        - {label: Organics,  column: ORGANICS}

Each zone row carries one day code per stream column plus an optional
ZONE_NAME. The live ReCollect (address-API) path is a separate plugin.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

from broombuster import analysis, recollect, resolve
from broombuster.cities import CITIES
from broombuster.domains.base import DomainResult

# Repo root — this file is <repo>/src/broombuster/domains/trash.py.
_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

_DEFAULT_STREAMS = (
    ("Garbage", "GARBAGE"),
    ("Recycling", "RECYCLE"),
    ("Organics", "ORGANICS"),
)

# city_key -> (mtime, gdf in EPSG:3857). Zone data is small and static per build.
_ZONE_CACHE: dict[str, tuple[float, Any]] = {}
_ZONE_CACHE_LOCK = threading.Lock()


def _trash_config(city_key: str, cities: Optional[dict] = None) -> dict:
    src = cities if cities is not None else CITIES
    return (src.get(city_key) or {}).get("trash") or {}


_ORD_WORDS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


def _fallback_desc(code: str) -> str:
    """Minimal human desc from a day code when the data carries none.

    Weekly codes (e.g. "ME") have empty ordinals and no desc, so
    format_schedule_side would drop them; this yields "Mon", "Mon 1st & 3rd".
    """
    wk = analysis._code_weekday(code)
    if wk is None:
        return ""
    _, label = wk
    ords = sorted(analysis._code_ordinals(code))
    if not ords or set(ords) >= {1, 2, 3, 4}:
        return label
    return f"{label} {' & '.join(_ORD_WORDS.get(o, str(o)) for o in ords)}"


def _streams(cfg: dict) -> tuple[tuple[str, str, Optional[str]], ...]:
    """Return (label, code_column, desc_column|None) per collection stream."""
    raw = cfg.get("streams")
    if not raw:
        return tuple((lbl, col, None) for lbl, col in _DEFAULT_STREAMS)
    return tuple((s["label"], s["column"], s.get("desc_column")) for s in raw)


def _load_trash_zones(city_key: str) -> Optional[Any]:
    """Load a city's trash zone polygons in EPSG:3857, or None if unconfigured."""
    import geopandas

    cfg = _trash_config(city_key)
    rel = cfg.get("fgb_path")
    if not rel:
        return None
    path = os.path.join(_ROOT, rel)
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    with _ZONE_CACHE_LOCK:
        cached = _ZONE_CACHE.get(city_key)
        if cached and cached[0] == mtime:
            return cached[1]
    gdf = geopandas.read_file(path).to_crs("EPSG:3857")
    # Tag rows so format() can recover the city's stream config statelessly.
    gdf["_city"] = city_key
    with _ZONE_CACHE_LOCK:
        _ZONE_CACHE[city_key] = (mtime, gdf)
    return gdf


class ZoneTrashPlugin:
    """Trash-day plugin for zone-polygon cities."""

    domain_id: str = "trash"
    label: str = "Trash day"
    subject: str = "home"

    def __init__(self, loader=None, cities: Optional[dict] = None):
        # Injectable for tests: loader(city_key) -> gdf_3857 | None.
        self._loader = loader or _load_trash_zones
        self._cities = cities

    def supports_city(self, city_key: str) -> bool:
        return _trash_config(city_key, self._cities).get("kind") == "zone"

    def resolve_for(self, gdf_3857: Any, lat: float, lon: float,
                    city_key: str, address: Optional[str] = None
                    ) -> Optional[resolve.ResolvedCar]:
        # Ignore the sweeping gdf and address; trash zones resolve by coordinate.
        zones = self._loader(city_key)
        if zones is None or len(zones) == 0:
            return None
        try:
            return resolve.resolve_car_segment(
                zones, lat, lon, city_key=None, max_distance_m=0.0,
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
                schedule_lines=["No trash schedule for this location"],
                extras={"zone": None, "streams": {}},
            )

        row = resolved.segment
        cfg = _trash_config(self._get_city(resolved), self._cities)
        streams = _streams(cfg)

        lines: list[str] = []
        entries: list[tuple] = []   # (code, desc, time) for urgency
        stream_codes: dict[str, str] = {}
        for stream_label, col, desc_col in streams:
            code = row.get(col)
            if not isinstance(code, str) or not code.strip():
                continue
            if analysis.is_no_sweep_code(code):
                continue
            desc = row.get(desc_col) if desc_col else ""
            if not isinstance(desc, str) or not desc.strip():
                desc = _fallback_desc(code)
            display = analysis.format_schedule_side([(code, desc, "")], local_now)
            if not display:
                continue
            lines.append(f"{stream_label}: {' / '.join(display)}")
            entries.append((code, desc, ""))
            stream_codes[stream_label] = code

        raw = analysis.check_day_street_sweeping(entries, local_now=local_now)
        urgency = raw if raw in ("today", "tomorrow") else "safe"
        if not lines:
            lines = ["No collection scheduled"]

        zone_name = row.get("ZONE_NAME") or resolved.street_display or None
        return DomainResult(
            domain_id=self.domain_id,
            label=self.label,
            urgency=urgency,
            schedule_lines=lines,
            extras={"zone": zone_name, "streams": stream_codes},
        )

    @staticmethod
    def _get_city(resolved: Any) -> str:
        # Trash zone rows may carry _city; fall back to empty (uses default cfg).
        try:
            return resolved.segment.get("_city") or ""
        except Exception:
            return ""


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

    def __init__(self, cities: Optional[dict] = None):
        self._cities = cities

    def supports_city(self, city_key: str) -> bool:
        return _trash_config(city_key, self._cities).get("kind") == "recollect"

    def resolve_for(self, gdf_3857: Any, lat: float, lon: float,
                    city_key: str, address: Optional[str] = None) -> Optional[dict]:
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
