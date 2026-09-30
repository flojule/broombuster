"""Request-scoped helpers for /check: region resolve, address, tile/bbox clipping."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import shapely.geometry as _shp_geom
from shapely.ops import unary_union

from broombuster import gps
from broombuster.cities import CITIES, REGIONS, region_for_point


def _clip_with_sindex(gdf, clip_geom):
    """Clip a GeoDataFrame to features intersecting `clip_geom` via the spatial index.

    Indices are sorted to preserve the original row order — the GeoJSON
    builder's segment dedup relies on insertion order.
    """
    return gdf.iloc[np.sort(gdf.sindex.query(clip_geom, predicate="intersects"))]


def _resolve_region(req):
    """Return (region_key, local_now) for a request (explicit region or auto)."""
    region = req.region if req.region in REGIONS else region_for_point(req.lat, req.lon)
    local_now = datetime.now(ZoneInfo(REGIONS[region]["tz"]))
    return region, local_now


def _build_address(resolved, city_key: str, lat: float, lon: float) -> str:
    """Canonical address string from the resolved segment.

    Polygon zones → "Zone: <name>, <city>". Line segments → optional
    Nominatim house number (gated to the resolved street) + display name.
    Falls back to raw lat/lon when nothing resolves.
    """
    coords = f"{lat:.4f}, {lon:.4f}"
    if resolved is None:
        return coords
    display = resolved.street_display or resolved.street_name
    if not display:
        return coords
    city_short = CITIES[city_key]["name"].split(",")[0]
    if resolved.is_polygon:
        return f"Zone: {display}, {city_short}"
    hn = gps.maybe_house_number(lat, lon, resolved.street_name)
    return f"{hn} {display}, {city_short}" if hn else f"{display}, {city_short}"


def _tiles_to_geom(tiles):
    """Union of XYZ tile boxes ('z/x/y' strings) → clip geometry, or None."""
    def _tile_lat(yy: int, zz: int) -> float:
        n2 = 2 ** zz
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n2))))

    boxes = []
    for t in tiles:
        if not isinstance(t, str):
            continue
        parts = t.split('/')
        if len(parts) != 3:
            continue
        try:
            z, x, y = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        n = 2 ** z
        lon_min = x / n * 360.0 - 180.0
        lon_max = (x + 1) / n * 360.0 - 180.0
        boxes.append(_shp_geom.box(lon_min, _tile_lat(y + 1, z), lon_max, _tile_lat(y, z)))

    return unary_union(boxes) if boxes else None


def _clip_region_for_request(req, gdf_4326):
    """Clip the region GDF to the requested view; return (gdf, simplify_tol).

    Priority: tiles → union of tile boxes; full_region → whole region (no
    simplify, so it stays a superset of later bbox requests); bbox → explicit
    box; otherwise → ~1.5 km radius around the car. The simplify tolerance is
    sub-pixel for a ~1000 px viewport (span / 2000).
    """
    bbox_span_deg = 0.03  # default radius mode below: ±0.015°
    if req.tiles and isinstance(req.tiles, list) and not req.full_region:
        clip_geom = _tiles_to_geom(req.tiles)
        if clip_geom is not None:
            gdf_display = _clip_with_sindex(gdf_4326, clip_geom)
            minx, miny, maxx, maxy = clip_geom.bounds
        else:
            gdf_display = gdf_4326
            minx, miny, maxx, maxy = gdf_4326.total_bounds
        bbox_span_deg = max(maxx - minx, maxy - miny)
    elif req.full_region:
        gdf_display = gdf_4326
        bbox_span_deg = 0.0  # skip simplify so full-region stays a superset
    elif req.bbox and isinstance(req.bbox, list) and len(req.bbox) == 4:
        min_lat, min_lon, max_lat, max_lon = req.bbox
        _clip = _shp_geom.box(min_lon, min_lat, max_lon, max_lat)
        gdf_display = _clip_with_sindex(gdf_4326, _clip)
        bbox_span_deg = max(max_lon - min_lon, max_lat - min_lat)
    else:
        _CLIP_DEG = 0.015  # ≈ 1.5 km
        _clip = _shp_geom.box(
            req.lon - _CLIP_DEG, req.lat - _CLIP_DEG,
            req.lon + _CLIP_DEG, req.lat + _CLIP_DEG,
        )
        gdf_display = _clip_with_sindex(gdf_4326, _clip)
        bbox_span_deg = 2 * _CLIP_DEG

    simplify_tolerance = bbox_span_deg / 2000.0 if bbox_span_deg > 0 else None
    return gdf_display, simplify_tolerance
