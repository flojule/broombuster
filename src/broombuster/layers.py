"""Extra map layers per city (data/<city>/<theme>.fgb, built by scripts/build_layers.py
from layer_sources.py): what applies at a point, for /api/rules, and the catalogue the
map's Layers menu lists (/api/cities)."""

import hashlib
import os
from pathlib import Path

import geopandas
import pandas as pd
import shapely

from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

# theme -> name in the Layers menu; map: drawn on the map (else lookups only);
# near_m: how close a car must be for the feature to apply (0: inside a polygon);
# applies_to: "car" rules, "home" facts, or "map" only.
THEMES = {
    "parking": {"name": "Permit areas and limits", "map": True, "near_m": 12, "applies_to": "car"},
    "meters":  {"name": "Meters", "map": True, "near_m": 20, "applies_to": "car"},
    "winter":  {"name": "Winter parking bans", "map": True, "near_m": 15, "applies_to": "car"},
    "ev":      {"name": "EV charging", "map": True, "near_m": 0, "applies_to": "map"},
    "fire":    {"name": "Fire hazard zones", "map": True, "near_m": 0, "applies_to": "home"},
    "evac":    {"name": "Evacuation zones", "map": False, "near_m": 0, "applies_to": "home"},
    "markets": {"name": "Farmers markets", "map": True, "near_m": 0, "applies_to": "map"},
}

_FRONTEND = Path(REPO_ROOT) / "frontend" / "layers"
LIVE_CLOSURES = ("bay_area",)  # regions with a live closures feed (feeds.py)
_loaded: dict[str, dict[str, geopandas.GeoDataFrame]] = {}  # region -> theme -> EPSG:3857


def city_dir(city_key: str) -> Path:
    """The directory holding a city's data (next to its sweeping FGB)."""
    return Path(REPO_ROOT) / os.path.dirname(CITIES[city_key]["fgb_path"])


def fgb_path(city_key: str, theme: str) -> Path:
    return city_dir(city_key) / f"{theme}.fgb"


def load_all() -> None:
    """Load every region's layers (EPSG:3857, spatial index built)."""
    for rk, region in REGIONS.items():
        themes = {}
        for theme in THEMES:
            parts = []
            for ck in region["cities"]:
                p = fgb_path(ck, theme)
                if theme in (CITIES[ck].get("layers") or []) and p.exists():
                    g = geopandas.read_file(p)
                    g["city"] = ck
                    parts.append(g)
            if parts:
                gdf = geopandas.GeoDataFrame(pd.concat(parts, ignore_index=True),
                                             crs="EPSG:4326").to_crs(3857)
                gdf.sindex  # noqa: B018 — builds the STRtree
                themes[theme] = gdf
        _loaded[rk] = themes


def catalogue(region_key: str) -> list[dict]:
    """[{id, name, url}] of the region's map layers; url carries a content hash (cache)."""
    out = []
    for theme, meta in THEMES.items():
        path = _FRONTEND / region_key / f"{theme}.geojson"
        if meta["map"] and path.exists():
            v = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            out.append({"id": theme, "name": meta["name"],
                        "url": f"/layers/{region_key}/{theme}.geojson?v={v}"})
    if region_key in LIVE_CLOSURES:  # fetched live, not built (feeds.closures)
        out.append({"id": "closures", "name": "Street closures (2 weeks)",
                    "url": f"/api/closures?region={region_key}"})
    return out


def rules_at(region_key: str, lat: float, lon: float, applies_to: str) -> list[dict]:
    """[{theme, label, rule, asof, city}] of the features that apply at a point, nearest
    first per theme, one per (theme, label)."""
    pt = shapely.Point(*_to_3857(lon, lat))
    out = []
    for theme, gdf in _loaded.get(region_key, {}).items():
        meta = THEMES[theme]
        if meta["applies_to"] != applies_to:
            continue
        idx = gdf.sindex.query(pt.buffer(max(meta["near_m"], 1)), predicate="intersects")
        if not len(idx):
            continue
        hits = gdf.iloc[idx].copy()
        hits["_d"] = hits.geometry.distance(pt)
        # A polygon applies inside (a 4 m margin for GPS); lines and points within near_m.
        is_poly = hits.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
        hits = hits[hits["_d"] <= is_poly.map({True: 4, False: meta["near_m"]})].sort_values("_d")
        seen = set()
        for _, r in hits.iterrows():
            if r["label"] in seen:
                continue
            seen.add(r["label"])
            out.append({"theme": theme, "label": r["label"], "rule": r["rule"] or "",
                        "asof": r["asof"] or "", "city": r["city"]})
    return out


_T = None


def _to_3857(lon: float, lat: float) -> tuple[float, float]:
    global _T
    if _T is None:
        from pyproj import Transformer
        _T = Transformer.from_crs(4326, 3857, always_xy=True)
    return _T.transform(lon, lat)


def file_version(path: str) -> str:
    """Short content hash of a file (cache-busting for the layer URLs)."""
    with open(os.path.join(REPO_ROOT, path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]
