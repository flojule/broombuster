"""Request-scoped helpers: locate a coordinate, build the canonical address."""

from datetime import datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from broombuster import gps, resolve
from broombuster.cities import CITIES, REGIONS, region_for_point

from .state import region_gdf


class Located(NamedTuple):
    region: str
    local_now: datetime              # region-local clock
    gdf_3857: object                 # the region's combined GeoDataFrame
    resolved: resolve.ResolvedCar | None
    city_key: str


def _locate(lat: float, lon: float, region: str | None = None) -> Located:
    """Region, clock, data and nearest segment for a coordinate; 503 while loading."""
    region = region if region in REGIONS else region_for_point(lat, lon)
    local_now = datetime.now(ZoneInfo(REGIONS[region]["tz"]))
    gdf = region_gdf(lat, lon, region)
    if gdf is None:
        raise HTTPException(503, f"No data for region '{region}' yet — try again shortly.")
    resolved, city_key = resolve.locate(gdf, lat, lon, region)
    return Located(region, local_now, gdf, resolved, city_key)


def _build_address(resolved, city_key: str, lat: float, lon: float,
                   *, network: bool = True) -> tuple[str, bool]:
    """(canonical address, house_number_pending) from the resolved segment.

    Polygon zones → "Zone: <name>, <city>". Line segments → optional
    Nominatim house number (gated to the resolved street) + display name.
    Falls back to raw lat/lon when nothing resolves.

    network=False never calls Nominatim: the house number comes from the
    cache only, and `house_number_pending` is True when a lookup could still
    add one (the client then asks GET /address).
    """
    coords = f"{lat:.4f}, {lon:.4f}"
    if resolved is None or not resolved.label:
        return coords, False
    city_short = CITIES[city_key]["name"].split(",")[0]
    if resolved.is_polygon:
        return f"Zone: {resolved.label}, {city_short}", False
    pending = (not network and bool(resolved.street_name)
               and not gps.house_number_cached(lat, lon))
    hn = gps.maybe_house_number(lat, lon, resolved.street_name, network=network)
    addr = f"{hn} {resolved.label}, {city_short}" if hn else f"{resolved.label}, {city_short}"
    return addr, pending
