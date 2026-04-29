"""
Single source of truth for "which street is the car on?".

resolve_car_segment(gdf_3857, lat, lon) returns one authoritative
ResolvedCar describing:
  - the nearest street segment (or containing polygon)
  - the side of the street (even / odd / None) derived purely from geometry
    and the segment's address-range parity — never from a Nominatim
    house-number
  - the distance from the car to the centerline
  - the point on the centerline nearest the car (projected, EPSG:3857)

All downstream logic (urgency, schedule, map highlight, UI label) must
consume this single result. Mixing Nominatim output with spatial-join output
is what produced the cross-field inconsistencies this module replaces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pyproj
from shapely.geometry import MultiPolygon, Point, Polygon

_TRANSFORMER_4326_TO_3857 = pyproj.Transformer.from_crs(
    "EPSG:4326", "EPSG:3857", always_xy=True
)


class NoSegmentNearby(Exception):
    """Raised when no street segment is within max_distance_m of the car."""


@dataclass(frozen=True)
class ResolvedCar:
    segment: Any                 # The authoritative GDF row (pandas Series)
    street_name: str             # Canonical upper-case name from the segment
    street_display: str          # Short display form (STREET_DISPLAY or derived)
    side: Optional[str]          # "even" | "odd" | None (None when unknown)
    distance_m: float            # Point-to-line distance in metres
    projected_point: tuple[float, float]  # Nearest point on centerline (EPSG:3857)
    is_polygon: bool             # True when the segment is a polygon (zone-based data)


def resolve_car_segment(
    gdf_3857,
    lat: float,
    lon: float,
    *,
    city_key: Optional[str] = None,
    max_distance_m: float = 40.0,
) -> ResolvedCar:
    """Authoritative nearest-segment resolver.

    Args:
        gdf_3857: GeoDataFrame in EPSG:3857 with a spatial index (`sindex`).
        lat, lon: Car coordinates in EPSG:4326.
        city_key: Optional — when set, filter candidates whose `_city` column
            matches. Skips cross-city matches when regions overlap.
        max_distance_m: If the nearest line segment is farther than this,
            raise NoSegmentNearby. Defaults to 40 m — wide enough for urban
            blocks, narrow enough to reject off-map coordinates.

    Returns:
        A ResolvedCar. `side` is None only when the segment is a polygon
        (e.g. Chicago ward zones) or when the address ranges on both sides
        are ambiguous.

    Raises:
        NoSegmentNearby: No line segment within max_distance_m and no
            containing polygon found.
    """
    car_x, car_y = _TRANSFORMER_4326_TO_3857.transform(lon, lat)
    car_pt = Point(car_x, car_y)

    # Search radius: wider than max_distance_m so polygons that enclose the
    # car but whose bbox center is distant are still found.
    search_buffer = max(max_distance_m * 3.0, 100.0)
    candidate_idxs = gdf_3857.sindex.query(car_pt.buffer(search_buffer))

    best_line_idx: Optional[int] = None
    best_line_dist = float("inf")

    for i in candidate_idxs:
        row = gdf_3857.iloc[i]
        if city_key:
            seg_city = row.get("_city")
            if seg_city and seg_city != city_key:
                continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        # Polygon / MultiPolygon: if the car is inside, that's the answer.
        if isinstance(geom, (Polygon, MultiPolygon)):
            if geom.contains(car_pt):
                return ResolvedCar(
                    segment=row,
                    street_name=_safe_str(row.get("STREET_NAME")),
                    street_display=_safe_str(
                        row.get("STREET_DISPLAY") or row.get("STREET_NAME")
                    ),
                    side=None,
                    distance_m=0.0,
                    projected_point=(car_x, car_y),
                    is_polygon=True,
                )
            continue

        # Line / MultiLineString: track the nearest.
        d = car_pt.distance(geom)
        if d < best_line_dist:
            best_line_dist = d
            best_line_idx = int(i)

    if best_line_idx is None or best_line_dist > max_distance_m:
        raise NoSegmentNearby(
            f"No street segment within {max_distance_m:.0f} m of "
            f"({lat:.5f}, {lon:.5f})"
        )

    row = gdf_3857.iloc[best_line_idx]
    geom = row.geometry

    # Project car onto the centerline.
    proj_dist = geom.project(car_pt)
    proj_pt = geom.interpolate(proj_dist)
    px, py = float(proj_pt.x), float(proj_pt.y)

    side = _determine_side(geom, car_pt, proj_pt, row)

    return ResolvedCar(
        segment=row,
        street_name=_safe_str(row.get("STREET_NAME")),
        street_display=_safe_str(
            row.get("STREET_DISPLAY") or row.get("STREET_NAME")
        ),
        side=side,
        distance_m=float(best_line_dist),
        projected_point=(px, py),
        is_polygon=False,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _determine_side(geom, car_pt, proj_pt, row) -> Optional[str]:
    """Return 'even' | 'odd' | None.

    Combines geometry (which side of the centerline the car is on, via a
    cross product at the nearest line segment) with the row's L_F_ADD /
    L_T_ADD / R_F_ADD / R_T_ADD parity to map left/right → even/odd.
    """
    ax, ay, bx, by = _nearest_vertex_pair(geom, proj_pt)
    if ax is None:
        return None

    # Signed cross product of (B - A) × (car - A):
    # > 0 → car is to the left of A→B  (driver-left when traveling A→B)
    # < 0 → car is to the right
    # = 0 → car is exactly on the line
    cross = (bx - ax) * (car_pt.y - ay) - (by - ay) * (car_pt.x - ax)
    if cross == 0:
        # Exactly on the centerline — tiebreak: treat as right side (odd by
        # common US convention). Callers that care should log a warning.
        left_side = False
    else:
        left_side = cross > 0

    left_parity = _parity(row.get("L_F_ADD"), row.get("L_T_ADD"))
    right_parity = _parity(r_from=row.get("R_F_ADD"), r_to=row.get("R_T_ADD"))

    if left_side:
        # Prefer left-side parity; fall back to the complement of right-side.
        if left_parity is not None:
            return left_parity
        if right_parity == "even":
            return "odd"
        if right_parity == "odd":
            return "even"
        return None  # Both sides ambiguous — unknown.

    # Right side
    if right_parity is not None:
        return right_parity
    if left_parity == "even":
        return "odd"
    if left_parity == "odd":
        return "even"
    return None


def _nearest_vertex_pair(geom, proj_pt):
    """Return (ax, ay, bx, by) of the line segment in `geom` whose midpoint
    is closest to the projected point. Handles LineString and
    MultiLineString. Returns (None, None, None, None) on empty/degenerate.
    """
    coords = _coords_for(geom, proj_pt)
    if not coords or len(coords) < 2:
        return None, None, None, None

    target = (proj_pt.x, proj_pt.y)
    best = None
    best_d = float("inf")
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0], coords[i][1]
        bx, by = coords[i + 1][0], coords[i + 1][1]
        mx = 0.5 * (ax + bx)
        my = 0.5 * (ay + by)
        d = (mx - target[0]) ** 2 + (my - target[1]) ** 2
        if d < best_d:
            best_d = d
            best = (ax, ay, bx, by)
    return best if best else (None, None, None, None)


def _coords_for(geom, proj_pt) -> list[tuple[float, float]]:
    """Return the coordinate list of a LineString, or of the part of a
    MultiLineString closest to `proj_pt`."""
    if geom.geom_type == "LineString":
        return [(x, y) for x, y, *_ in geom.coords]
    if geom.geom_type == "MultiLineString":
        best_part = None
        best_d = float("inf")
        for part in geom.geoms:
            d = proj_pt.distance(part)
            if d < best_d:
                best_d = d
                best_part = part
        if best_part is None:
            return []
        return [(x, y) for x, y, *_ in best_part.coords]
    return []


def _parity(l_from=None, l_to=None, *, r_from=None, r_to=None) -> Optional[str]:
    """Return 'even' | 'odd' | None for one side's address range.

    Called as either _parity(l_f, l_t) or _parity(r_from=..., r_to=...).
    A side is 'even' only when both endpoints are even, 'odd' only when
    both are odd. Mixed or missing → None.
    """
    if r_from is not None or r_to is not None:
        a, b = _safe_int(r_from), _safe_int(r_to)
    else:
        a, b = _safe_int(l_from), _safe_int(l_to)
    if a is None or b is None:
        return None
    if a % 2 == 0 and b % 2 == 0:
        return "even"
    if a % 2 == 1 and b % 2 == 1:
        return "odd"
    return None  # mixed


def _safe_int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _safe_str(v) -> str:
    if isinstance(v, str) and v.strip():
        return v
    return ""
