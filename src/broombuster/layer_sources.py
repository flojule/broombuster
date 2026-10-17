"""Where each city's extra map layers come from, and how they become the layer schema
(layers.py). Run by scripts/build_layers.py; nothing here runs in the server.

Every source returns a GeoDataFrame (EPSG:4326) with only these columns, so no
source column (owner names, plates, …) can leak into the committed files:

  label   short name shown on the map and the card ("Permit area G")
  rule    what it means for a parked car, in a sentence ("" for none)
  asof    when the source last changed (ISO date, "" when unknown)
"""

import datetime
import json
import os
import re

import geopandas
import pandas as pd
import requests
import shapely
import yaml

from broombuster.cities import CITIES
from broombuster.config import REPO_ROOT

_TIMEOUT_S = 120
_UA = {"User-Agent": "BroomBuster layer build (https://github.com/flojule/broombuster)"}
COLUMNS = ("label", "rule", "asof")

BERKELEY = "https://services1.arcgis.com/IYiCpZoSIq9lAxi8/arcgis/rest/services"
BERKELEY_GIS = "https://gis.cityofberkeley.info/arcgis/rest/services"
OAKLAND = "https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services"
AFDC = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"  # NREL's API, renamed NLR in 2025


# ── Fetching ──────────────────────────────────────────────────────────────────

def _get(url: str, **params) -> requests.Response:
    r = requests.get(url, params=params, headers=_UA, timeout=_TIMEOUT_S)
    r.raise_for_status()
    return r


def _frame(features: list) -> geopandas.GeoDataFrame:
    if not features:
        return geopandas.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    return geopandas.GeoDataFrame.from_features(features, crs="EPSG:4326")


def arcgis(layer_url: str, where: str = "1=1",
           fields: str = "*") -> tuple[geopandas.GeoDataFrame, str]:
    """(every feature of an ArcGIS FeatureServer/MapServer layer, its last edit date)."""
    meta = _get(layer_url, f="json").json()
    edited = (meta.get("editingInfo") or {}).get("lastEditDate")
    asof = datetime.date.fromtimestamp(edited / 1000).isoformat() if edited else ""
    page = min(int(meta.get("maxRecordCount") or 1000), 2000)
    feats, offset = [], 0
    while True:
        data = _get(f"{layer_url}/query", where=where, outFields=fields, outSR=4326, f="geojson",
                    resultOffset=offset, resultRecordCount=page).json()
        batch = data.get("features") or []
        feats += batch
        if len(batch) < page and not data.get("exceededTransferLimit"):
            break
        offset += len(batch)
        if not batch:
            break
    return _frame(feats), asof


def socrata(domain: str, dataset: str, where: str | None = None,
            select: str | None = None) -> tuple[geopandas.GeoDataFrame, str]:
    """(rows of a Socrata dataset with a geometry column, its last data update)."""
    meta = _get(f"https://{domain}/api/views/{dataset}.json").json()
    upd = meta.get("rowsUpdatedAt")
    asof = datetime.date.fromtimestamp(upd).isoformat() if upd else ""
    params = {"$limit": 200000}
    if where:
        params["$where"] = where
    if select:
        params["$select"] = select
    data = _get(f"https://{domain}/resource/{dataset}.geojson", **params).json()
    return _frame(data.get("features") or []), asof


def _out(gdf: geopandas.GeoDataFrame, label, rule, asof: str) -> geopandas.GeoDataFrame:
    """The layer schema from a source frame: label / rule are strings or callables of a row."""
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["label"] = gdf.apply(label, axis=1) if callable(label) else label
    gdf["rule"] = gdf.apply(rule, axis=1) if callable(rule) else rule
    gdf["asof"] = asof
    return gdf[[*COLUMNS, "geometry"]].reset_index(drop=True)


def _clean(geom, min_area: float):
    """Drop parts and holes under min_area (m², EPSG:3857 units near enough here)."""
    parts = getattr(geom, "geoms", [geom])
    keep = [shapely.Polygon(p.exterior,
                            [h for h in p.interiors if shapely.Polygon(h).area >= min_area])
            for p in parts if p.area >= min_area]
    return shapely.MultiPolygon(keep) if keep else shapely.Polygon()


def _dissolve(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """One feature per (label, rule), closed over 15 m gaps (parcel-level sources), slivers
    under 5,000 m² dropped and simplified ~10 m: big polygon layers stay small."""
    if gdf.empty:
        return gdf
    m = gdf.to_crs(3857)
    m["geometry"] = m.geometry.buffer(0)
    m = m.dissolve(by=["label", "rule", "asof"], as_index=False)
    m["geometry"] = [_clean(g.buffer(15).buffer(-15), 5000).simplify(10) for g in m.geometry]
    return m.to_crs(4326)[[*COLUMNS, "geometry"]]


# ── Parking rules ─────────────────────────────────────────────────────────────

_BERKELEY_RPP = ("Without a permit: 2 hours, generally Mon–Fri 8 AM–7 PM; "
                 "the signs give each block's hours.")
_OAKLAND_RPP = ("Permit holders are exempt from the posted time limit; "
                "everyone else follows the signs.")


def berkeley_parking():
    gdf, asof = arcgis(f"{BERKELEY}/Public_Online_RPP_Map_WFL1/FeatureServer/10", fields="Area")
    return _out(gdf, lambda r: f"Permit area {str(r['Area']).strip()}", _BERKELEY_RPP, asof)


def oakland_parking():
    gdf, asof = arcgis(f"{OAKLAND}/RPPCurbs0819/FeatureServer/0", fields="RPP_ZONE")
    gdf = gdf[gdf["RPP_ZONE"].notna() & (gdf["RPP_ZONE"].astype(str).str.strip() != "")]
    return _out(gdf, lambda r: f"Permit area {str(r['RPP_ZONE']).strip()}", _OAKLAND_RPP, asof)


def _sf_hours(r) -> str:
    days, a, b = (str(r.get(k) or "").strip() for k in ("days", "from_time", "to_time"))
    return " ".join(x for x in (days.replace("-", "–"), f"{a}–{b}" if a and b else "") if x)


def _sf_label(r) -> str:
    area = str(r.get("rpparea1") or "").strip()
    return f"Permit area {area}" if area else str(r.get("regulation") or "Parking rule").strip()


def _sf_rule(r) -> str:
    reg = str(r.get("regulation") or "").strip()
    limit = str(r.get("hrlimit") or "").strip()
    hours = _sf_hours(r)
    area = str(r.get("rpparea1") or "").strip()
    if reg.lower() == "time limited" and limit and limit not in ("0", "0.0"):
        text = f"{limit.rstrip('0').rstrip('.')} h limit" + (f", {hours}" if hours else "")
        return text + (f"; permit area {area} holders exempt." if area else ".")
    if reg.lower().startswith("no overnight"):
        return f"No overnight parking{', ' + hours if hours else ''}."
    return f"{reg}{', ' + hours if hours else ''}." if reg else ""


def sf_parking():
    gdf, asof = socrata("data.sf.gov", "hi6h-neyh")
    return _out(gdf, _sf_label, _sf_rule, asof)


def sf_towaway():
    gdf, asof = socrata("data.sf.gov", "wnhf-gu86")
    if gdf.empty:
        return gdf
    return _out(gdf, "Tow-away zone",
                lambda r: f"Tow-away {_sf_hours(r) or 'at the posted hours'}.", asof)


def sf_parking_all():
    parts = [p for p in (sf_parking(), sf_towaway()) if not p.empty]
    return pd.concat(parts, ignore_index=True)


# ── Meters ────────────────────────────────────────────────────────────────────

def berkeley_meters():
    gdf, asof = arcgis(f"{BERKELEY_GIS}/Public/Portal_CommSvcs/MapServer/3", fields="METERTYPE")
    return _out(gdf, "Meter",
                "Metered: goBerkeley sets each block's rate and limit by demand; see the meter.",
                asof)


def oakland_meters():
    gdf, asof = arcgis(f"{OAKLAND}/Oakland_Parking_Meters/FeatureServer/0",
                       fields="AREA,POLE_STATU")
    return _out(gdf, "Meter",
                "Metered: most meters $3/h ($1–$4), Mon–Sat 8 AM–6 PM; "
                "around Lake Merritt also evenings and Sunday afternoons.", asof)


def sf_meters():
    gdf, asof = socrata("data.sf.gov", "27b3-yjjx")
    def label(r):
        street = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m[1] + m[2].lower(),
                        str(r.get("street_name") or "").title())
        return f"Metered block, {street}" if street else "Metered block"

    return _out(gdf, label, "Metered: pay at the meter or app; hours and rate are on the meter.",
                asof)


# ── Chicago winter bans ───────────────────────────────────────────────────────

def chicago_winter():
    night, a1 = socrata("data.cityofchicago.org", "mcad-r2g5")
    snow, a2 = socrata("data.cityofchicago.org", "i6k4-giaj")
    parts = []
    if not night.empty:
        parts.append(_out(night, "Winter overnight parking ban",
                          "No parking 3–7 AM from Dec 1 to Apr 1, snow or not (towed).", a1))
    if not snow.empty:
        parts.append(_out(snow, "2-inch snow route",
                          "No parking once 2 inches of snow have fallen, until plowed.",
                          a2))
    return pd.concat(parts, ignore_index=True)


# ── Fire hazard and evacuation zones ──────────────────────────────────────────

_FIRE_RULE = ("Hill fire zone: keep narrow streets clear for fire engines; "
              "on Red Flag days don't park on them.")


def _city_box(city_key):
    lat0, lon0, lat1, lon1 = CITIES[city_key]["bbox"]
    return shapely.box(lon0, lat0, lon1, lat1)


def berkeley_fire():
    # The hills' Fire Zones 2 and 3 (the city's own designation, which its narrow-street
    # parking rules and Red Flag measures use).
    gdf, asof = arcgis(f"{BERKELEY}/FireZonesBerkeley_WFL1/FeatureServer/0", fields="PLN_HILL_Z")
    gdf = gdf.clip(_city_box("berkeley"))
    return _dissolve(_out(gdf, lambda r: _zone_label(r["PLN_HILL_Z"]), _FIRE_RULE, asof))


def _zone_label(v) -> str:
    v = str(v or "").strip()
    if v and v[-1].isdigit() and "zone" not in v.lower():
        return f"Fire zone {v}"
    return v or "Fire zone"


def oakland_fire():
    gdf, asof = arcgis(f"{OAKLAND}/Fire_Severity_Zone_LRA/FeatureServer/0", fields="HAZ_CLASS")
    gdf = gdf.clip(_city_box("oakland"))
    gdf = gdf[gdf["HAZ_CLASS"].astype(str).str.contains("Very High", case=False)]
    return _dissolve(_out(gdf, "Very high fire hazard zone", _FIRE_RULE, asof))


def _evac(gdf, asof, prefix):
    gdf = gdf[gdf["zone_id"].astype(str).str.upper().str.startswith(prefix)]
    return _out(gdf, lambda r: f"Evacuation zone {r['zone_id']}",
                "Genasys Protect zone: alerts and evacuation orders name it.", asof)


def berkeley_evac():
    gdf, asof = arcgis(f"{BERKELEY}/cob_evacuation_zones/FeatureServer/109",
                       where="city_abbr = 'BRK'", fields="zone_id,city_abbr")
    return _evac(gdf, asof, "")


def oakland_evac():
    gdf, asof = arcgis(f"{OAKLAND}/Zonehaven_OKL/FeatureServer/0", fields="zone_id")
    return _evac(gdf, asof, "")


# ── EV charging (NREL / NLR Alternative Fuels Data Center) ────────────────────

# ZIP codes covering each city (the AFDC query takes a ZIP list; results are then
# clipped to the city's bbox). One request per city keeps DEMO_KEY's hourly limit.
_ZIPS = {
    "berkeley": [*range(94702, 94711), 94720],
    "oakland": [*range(94601, 94614), 94618, 94619, 94621],
    "alameda": [94501, 94502],
    "san_francisco": [*range(94102, 94135), 94158],
    "chicago_all": [*range(60601, 60662), 60707, 60827],
}


def ev(city_key: str, bbox) -> geopandas.GeoDataFrame:
    """Public EV charging stations of a city, inside bbox [lat_min, lon_min, lat_max, lon_max]."""
    from broombuster import config

    lat0, lon0, lat1, lon1 = bbox
    data = _get(AFDC, api_key=config.nrel_api_key(), fuel_type="ELEC", access="public",
                status="E", limit="all", zip=",".join(str(z) for z in _ZIPS[city_key])).json()
    stations = data.get("fuel_stations") or []
    rows = []
    for s in stations:
        lat, lon = s.get("latitude"), s.get("longitude")
        if lat is None or lon is None or not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
            continue
        ports = [f"{n} DC fast" for n in [s.get("ev_dc_fast_num")] if n] + \
                [f"{n} Level 2" for n in [s.get("ev_level2_evse_num")] if n]
        network = (s.get("ev_network") or "").replace("Non-Networked", "").strip()
        rows.append({"label": s.get("station_name") or "EV charging",
                     "rule": ", ".join(x for x in (", ".join(ports), network) if x),
                     "asof": str(s.get("updated_at") or "")[:10],
                     "geometry": shapely.Point(lon, lat)})
    return geopandas.GeoDataFrame(rows, columns=[*COLUMNS, "geometry"], geometry="geometry",
                                  crs="EPSG:4326")


# ── Farmers markets (hand list) ───────────────────────────────────────────────

def markets(city_key: str) -> geopandas.GeoDataFrame:
    """data/places/markets.yaml rows of a city: weekly markets, kept by hand."""
    with open(os.path.join(REPO_ROOT, "data", "places", "markets.yaml"), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    rows = [{"label": m["name"], "rule": f"{m['day']} {m['hours']}",
             "asof": str(doc.get("last_verified") or ""),
             "geometry": shapely.Point(m["lon"], m["lat"])}
            for m in doc["markets"] if m["city"] == city_key]
    return geopandas.GeoDataFrame(rows, columns=[*COLUMNS, "geometry"], geometry="geometry",
                                  crs="EPSG:4326")


# ── Tables (no geometry) ──────────────────────────────────────────────────────

def chicago_permit_zones() -> pd.DataFrame:
    """Chicago's residential permit zones by address range (active rows only):
    street (normalize.street_name key), low, high, side (O / E / B), zone."""
    from broombuster import normalize

    rows = _get("https://data.cityofchicago.org/resource/u9xt-hiju.json",
                **{"$limit": 50000, "$where": "status = 'ACTIVE'"}).json()
    out = []
    for r in rows:
        street = " ".join(str(r.get(k) or "").strip()
                          for k in ("street_direction", "street_name", "street_type")).strip()
        try:
            low, high = int(float(r["address_range_low"])), int(float(r["address_range_high"]))
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"street": normalize.street_name(street), "low": low, "high": high,
                    "side": str(r.get("odd_even") or "B").strip().upper()[:1] or "B",
                    "zone": str(r.get("zone") or "").strip()})
    return pd.DataFrame(out, columns=["street", "low", "high", "side", "zone"])


# city -> {table name -> source}; written as data/<city dir>/<name>.csv.
TABLES = {"chicago_all": {"permit_zones": chicago_permit_zones}}


# ── Registry ──────────────────────────────────────────────────────────────────
# theme -> {city -> source}; a source is a callable of the city's manifest.
SOURCES = {
    "parking": {
        "berkeley": lambda c: berkeley_parking(),
        "oakland": lambda c: oakland_parking(),
        "san_francisco": lambda c: sf_parking_all(),
    },
    "meters": {
        "berkeley": lambda c: berkeley_meters(),
        "oakland": lambda c: oakland_meters(),
        "san_francisco": lambda c: sf_meters(),
    },
    "winter": {"chicago_all": lambda c: chicago_winter()},
    "ev": {ck: (lambda c, ck=ck: ev(ck, c["bbox"])) for ck in
           ("berkeley", "oakland", "alameda", "san_francisco", "chicago_all")},
    "fire": {"berkeley": lambda c: berkeley_fire(), "oakland": lambda c: oakland_fire()},
    "evac": {"berkeley": lambda c: berkeley_evac(), "oakland": lambda c: oakland_evac()},
    "markets": {ck: (lambda c, ck=ck: markets(ck)) for ck in
                ("berkeley", "oakland", "alameda", "san_francisco", "chicago_all")},
}

# Who to credit for each theme's data (shown in the Me tab).
ATTRIBUTION = {
    "parking": "Cities of Berkeley and Oakland (ArcGIS), DataSF (PDDL)",
    "meters": "Cities of Berkeley and Oakland (ArcGIS), DataSF (PDDL)",
    "winter": "City of Chicago Data Portal",
    "ev": "U.S. DOE Alternative Fuels Data Center",
    "fire": "CAL FIRE, Cities of Berkeley and Oakland",
    "evac": "Genasys Protect zones via the Cities of Berkeley and Oakland",
    "markets": "Market operators (kept by hand, data/places/markets.yaml)",
}


def dump_json(gdf: geopandas.GeoDataFrame) -> str:
    """Compact GeoJSON for the client's copy: label and city only (the rule text comes
    from /api/rules), coordinates to 1e-6° (~10 cm)."""
    gdf = gdf[["label", "city", "geometry"]].copy()
    gdf["geometry"] = shapely.set_precision(gdf.geometry.values, 1e-6, mode="pointwise")
    return json.dumps(json.loads(gdf.to_json(drop_id=True)), separators=(",", ":"))
