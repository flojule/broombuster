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
    "parking": {"name": "Permit areas", "map": True, "near_m": 12, "applies_to": "car"},
    "meters":  {"name": "Meters", "map": True, "near_m": 20, "applies_to": "car"},
    "winter":  {"name": "Winter parking bans", "map": True, "near_m": 15, "applies_to": "car"},
    "ev":      {"name": "EV charging", "map": True, "near_m": 0, "applies_to": "map"},
    "fire":    {"name": "Fire hazard zones", "map": True, "near_m": 0, "applies_to": "home"},
    "evac":    {"name": "Evacuation zones", "map": False, "near_m": 0, "applies_to": "home"},
    "markets": {"name": "Farmers markets", "map": True, "near_m": 0, "applies_to": "map"},
}

AREAS = "permit_areas"  # data/<city>/permit_areas.fgb: the parking theme's areas (the map's)
_FRONTEND = Path(REPO_ROOT) / "frontend" / "layers"
LIVE_CLOSURES = ("bay_area",)  # regions with a live closures feed (feeds.py)
_loaded: dict[str, dict[str, geopandas.GeoDataFrame]] = {}  # region -> theme -> EPSG:3857
# region -> the map's permit areas (label, city, color, rule; EPSG:3857): a point inside
# one is in that area even where no permit curb is near (the middle of a block).
_areas: dict[str, geopandas.GeoDataFrame] = {}


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
        _areas[rk] = _permit_areas(rk)


def _permit_areas(region_key: str):
    """The region's permit areas as drawn on the map (frontend/layers/<region>/parking.geojson:
    label, city, color), with their rule and date (data/<city>/permit_areas.fgb)."""
    path = _FRONTEND / region_key / "parking.geojson"
    facts = [geopandas.read_file(p, ignore_geometry=True).assign(city=ck)
             for ck in REGIONS[region_key]["cities"]
             if (p := fgb_path(ck, AREAS)).exists()]
    if not facts or not path.exists():
        return None
    g = geopandas.read_file(path).to_crs(3857)
    facts = pd.concat(facts).set_index(["city", "label"])
    keys = list(zip(g["city"], g["label"], strict=True))
    g["rule"] = [facts["rule"].get(k, "") for k in keys]
    g["asof"] = [facts["asof"].get(k, "") for k in keys]
    g.sindex  # noqa: B018
    return g


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
    first per theme, one per (theme, label). A permit area applies anywhere inside the
    area as drawn on the map, not only by its curbs, and carries its map colour (color)."""
    pt = shapely.Point(*_to_3857(lon, lat))
    out = []
    for theme, gdf in _loaded.get(region_key, {}).items():
        meta = THEMES[theme]
        if meta["applies_to"] != applies_to:
            continue
        idx = gdf.sindex.query(pt.buffer(max(meta["near_m"], 1)), predicate="intersects")
        hits = gdf.iloc[idx].copy()
        hits["_d"] = hits.geometry.distance(pt)
        # A polygon applies inside (a 4 m margin for GPS); lines and points within near_m.
        is_poly = hits.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
        hits = hits[hits["_d"] <= is_poly.map({True: 4, False: meta["near_m"]})].sort_values("_d")
        rows = [r for _, r in hits.iterrows()]
        areas = _areas.get(region_key) if theme == "parking" else None
        if areas is not None:  # after the curbs: a curb's own rule is the more precise
            rows += [areas.iloc[i] for i in areas.sindex.query(pt, predicate="intersects")]
        seen = set()
        for r in rows:
            if r["label"] in seen:
                continue
            seen.add(r["label"])
            out.append({"theme": theme, "label": r["label"], "rule": r["rule"] or "",
                        "asof": r["asof"] or "", "city": r["city"],
                        **_color(_area_color(areas, r["city"], r["label"]))})
    return out


def _area_color(areas, city: str, label: str):
    if areas is None:
        return None
    m = areas[(areas["city"] == city) & (areas["label"] == label)]
    return m["color"].iat[0] if len(m) else None


def _color(c) -> dict:
    return {"color": c} if isinstance(c, str) and c else {}


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
