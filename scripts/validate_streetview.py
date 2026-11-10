#!/usr/bin/env python3
"""Validate sweeping and parking data against street signs in Street View.

    python scripts/validate_streetview.py sample CITY LAYER [--n 400] [--seed 1]
    python scripts/validate_streetview.py capture CITY LAYER [--first 0] [--count 40]
    python scripts/validate_streetview.py zoom IMAGE U0 U1 V0 V1
    python scripts/validate_streetview.py score CITY LAYER

LAYER is `sweeping` or `parking`. `sample` draws block sides (sweeping) or blocks (parking)
at random, probability proportional to length, and records what /api/check and /api/rules
answer there. `capture` saves, per sample, two panoramas along the block and a 180 deg view of
each side (L = left of the block's drawn direction). Readers transcribe the signs into
`readings/<id>.json` without seeing the expected values (docs/validation.md);
`zoom` crops a view at full resolution. `score` compares, writes data/validation/<city>_<layer>.csv
and the sequential test: pass when the one-sided 95 % upper bound on the mismatch rate is <= 5 %.
Work files go to .validation/ (git-ignored). Imagery: Google's keyless Street View endpoints.
"""

import argparse
import csv
import json
import logging
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas
import numpy as np
import requests
import shapely
from PIL import Image

from broombuster import analysis, schedule
from broombuster.cities import CITIES, REGIONS
from broombuster.config import REPO_ROOT

ROOT = Path(REPO_ROOT)
WORK = ROOT / ".validation"
OUT = ROOT / "data" / "validation"
LAYERS = ("sweeping", "parking")
STALE_BEFORE = (2023, 11)  # imagery older than 3 years is not evidence of today's signs
BOUND = 0.05
OFFSET_M = 6.0  # a parked car's distance from the centre line
PANO_MAX_M = 20.0
SUPPLEMENT = 15  # minimum samples per small sweeping stratum
_GROUPS = {"sweeping": ("scheduled", "unscheduled"), "parking": ("parking",)}
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
_TILE = 512
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _work(city: str, layer: str) -> Path:
    return WORK / f"{city}_{layer}"


# ---------- sampling ----------


def _stratum(code, time) -> str:
    """Schedule class of one side: none, nosweep, night, weekly, monthly."""
    if not isinstance(code, str) or not code.strip():
        return "none"
    if analysis.is_no_sweep_code(code):
        return "nosweep"
    w = schedule.time_window(time)
    if w and w[0] < 6 * 60:
        return "night"
    r = schedule._rule(code)
    return "weekly" if r and (r[0] != "weekly" or r[2] is None) else "monthly"


def _longest(geom):
    """A line as one LineString: merged parts, else the longest."""
    geom = shapely.line_merge(geom) if geom.geom_type == "MultiLineString" else geom
    return max(geom.geoms, key=lambda x: x.length) if hasattr(geom, "geoms") else geom


def _pps_order(weights: np.ndarray, rng) -> np.ndarray:
    """Weighted random order without replacement (Efraimidis-Spirakis)."""
    keys = rng.random(len(weights)) ** (1.0 / weights)
    return np.argsort(-keys)


def _frame(city: str, layer: str):
    """Units to draw: (geometry in metres, weight, attributes)."""
    g = geopandas.read_file(ROOT / CITIES[city]["fgb_path"])
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g[g.geometry.geom_type.isin(("LineString", "MultiLineString"))].copy()
    g["geometry"] = [_longest(x) for x in g.geometry]
    g = g[~g["STREET_NAME"].fillna("").str.upper().str.contains("UNNAMED")]
    crs = g.estimate_utm_crs()
    g = g.to_crs(crs)
    g = g[g.length >= 40]
    if layer == "sweeping":
        units = []
        for i, r in g.iterrows():
            for side in ("even", "odd"):
                s = side.upper()
                units.append((i, side, _stratum(r[f"DAY_{s}"], r[f"TIME_{s}"])))
        w = np.array([g.length[i] for i, _, _ in units])
        return g, crs, units, w
    p = geopandas.read_file(ROOT / "data" / city / "parking.fgb").to_crs(crs)
    p = p[~p["label"].str.startswith("No oversized")]
    if not p.geometry.geom_type.str.contains("LineString").any():  # areas, not curbs
        inside = g[g.geometry.within(p.geometry.buffer(1).union_all())]
        units = [(i, None, "area") for i in inside.index]
        return g, crs, units, np.array([g.length[i] for i in inside.index])
    p["geometry"] = [_longest(x) for x in p.geometry]
    p = p[p.length >= 40].reset_index(drop=True)
    units = [(i, None, "curb") for i in p.index]
    return p, crs, units, np.array(p.length)


def _at(line, t: float, crs):
    """Point at fraction t of a line, its bearing (deg from north) and left/right points."""
    d = t * line.length
    a, b = line.interpolate(max(d - 3, 0)), line.interpolate(min(d + 3, line.length))
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy) or 1.0
    p = line.interpolate(d)
    left = shapely.Point(p.x - dy / n * OFFSET_M, p.y + dx / n * OFFSET_M)
    right = shapely.Point(p.x + dy / n * OFFSET_M, p.y - dx / n * OFFSET_M)
    to_ll = geopandas.GeoSeries([p, left, right], crs=crs).to_crs(4326)
    bearing = math.degrees(math.atan2(dx, dy)) % 360  # UTM grid ~ true north here
    return [(q.y, q.x) for q in to_ll], bearing


def _expect(client, region: str, lat: float, lon: float, geocode: bool = False) -> dict:
    """What the app says for a car parked here: sweeping side + schedule, parking rules."""
    c = client.post("/api/check", json={"lat": lat, "lon": lon, "region": region}).json()
    r = client.get(
        "/api/rules",
        params={"lat": lat, "lon": lon, "region": region, "geocode": str(geocode).lower()},
    ).json()
    side = c.get("car_side")
    return {
        "address": c.get("address"),
        "side": side,
        "sweeping": c.get(f"schedule_{side}") if side else None,
        "parking": [
            {"label": x["label"], "rule": x["rule"]}
            for x in r["rules"]
            if x["theme"] == "parking" and x["city"] == c.get("city")
        ],
    }


def cmd_sample(a) -> None:
    from fastapi.testclient import TestClient

    from broombuster.api.app import app

    logging.disable(logging.INFO)

    rng = np.random.default_rng(a.seed)
    region = next(rk for rk, r in REGIONS.items() if a.city in r["cities"])
    out = _work(a.city, a.layer)
    out.mkdir(parents=True, exist_ok=True)
    if a.city == "chicago_all":
        return _sample_chicago(a, rng, region, out)
    g, crs, units, w = _frame(a.city, a.layer)
    groups, strata, n = {}, {}, 0
    with TestClient(app) as client, open(out / "samples.jsonl", "w") as f:
        for draw, k in enumerate(_pps_order(w, rng)):
            full = all(groups.get(x, 0) >= a.n for x in _GROUPS[a.layer])
            if full and (all(c >= SUPPLEMENT for c in strata.values()) or draw > 5000):
                break
            i, side, stratum = units[k]
            line = _longest(g.geometry[i])
            t = float(rng.uniform(0.3, 0.7))
            (pc, pl, pr), bearing = _at(line, t, crs)
            exp = {"L": _expect(client, region, *pl), "R": _expect(client, region, *pr)}
            target = None
            if side:  # the view the app puts on the sampled side, else one at random
                hits = [v for v in "LR" if exp[v]["side"] == side]
                target = hits[0] if len(hits) == 1 else str(rng.choice(["L", "R"]))
                group = "scheduled" if _expected_rules(exp[target]["sweeping"]) else "unscheduled"
                if group == "unscheduled" and exp[target]["side"] is None:
                    continue  # freeways and ramps: no kerb parking
            else:
                group = "parking"
            main = groups.get(group, 0) < a.n
            if not main and (strata.get(stratum, 0) >= SUPPLEMENT or full):
                continue
            groups[group] = groups.get(group, 0) + main
            strata[stratum] = strata.get(stratum, 0) + 1
            rec = {
                "id": f"{a.city[:3]}-{a.layer[:2]}-{n:04d}",
                "draw": draw,
                "group": group,
                "main": main,
                "stratum": stratum,
                "street": exp["L"]["address"] or exp["R"]["address"],
                "lat": pc[0],
                "lon": pc[1],
                "bearing": round(bearing, 1),
                "shots": [_at(line, u, crs)[0][0] for u in (0.3, 0.7)],
                "target": target,
                "expected": exp,
            }
            f.write(json.dumps(rec) + "\n")
            n += 1
    print(f"{n} samples {groups} strata {strata} -> {out / 'samples.jsonl'}")


_DIR = {"NORTH": 0.0, "SOUTH": 0.0, "EAST": 90.0, "WEST": 90.0}


def _sample_chicago(a, rng, region: str, out: Path) -> None:
    """Chicago permit zones by address (data/chicago/permit_zones.csv): a random house number
    of a random zoned block side (by its address span), geocoded. Chicago's even numbers are
    on the west and north sides, so with N-S streets drawn northward and E-W eastward the
    even side is L."""
    import pandas as pd
    from fastapi.testclient import TestClient

    from broombuster.api.app import app

    logging.disable(logging.INFO)
    z = pd.read_csv(ROOT / "data" / "chicago" / "permit_zones.csv")
    z = z[z["street"].str.split().str[0].isin(_DIR) & (z["high"] - z["low"] >= 10)]
    n = 0
    with TestClient(app) as client, open(out / "samples.jsonl", "w") as f:
        for draw, k in enumerate(_pps_order(np.array(z["high"] - z["low"], float), rng)):
            if n >= a.n:
                break
            r = z.iloc[k]
            parity = 0 if r["side"] == "E" else 1
            num = int(rng.integers(r["low"], r["high"] + 1))
            num += (num - parity) % 2
            d, name = r["street"].split(" ", 1)
            q = f"{num} {d[0]} {name.title()}, Chicago"
            hit = client.get("/api/geocode", params={"q": q, "region": region}).json()
            if not hit.get("lat"):
                continue
            lat, lon, bearing = hit["lat"], hit["lon"], _DIR[d]
            dy, dx = math.cos(math.radians(bearing)), math.sin(math.radians(bearing))
            k_lat, k_lon = 1 / 111_320, 1 / (111_320 * math.cos(math.radians(lat)))
            shots = [[lat + t * dy * k_lat, lon + t * dx * k_lon] for t in (-30, 30)]
            exp = _expect(client, region, lat, lon, geocode=True)
            target = "L" if r["side"] == "E" else "R"
            other = {"address": None, "side": None, "sweeping": None, "parking": []}
            rec = {
                "id": f"chi-pa-{n:04d}",
                "draw": draw,
                "group": "parking",
                "main": True,
                "stratum": f"zone {r['zone']}",
                "street": q,
                "lat": lat,
                "lon": lon,
                "bearing": bearing,
                "shots": shots,
                "target": None,
                "zone_side": target,
                "expected": {target: exp, "R" if target == "L" else "L": other},
            }
            f.write(json.dumps(rec) + "\n")
            n += 1
    print(f"{n} samples -> {out / 'samples.jsonl'}")


# ---------- imagery ----------


def _get(url: str, params: dict) -> requests.Response:
    for attempt in range(4):
        try:
            return requests.get(url, params=params, headers=_UA, timeout=30)
        except requests.RequestException:
            if attempt == 3:
                raise
    raise AssertionError


def pano(lat: float, lon: float, radius: int = 25) -> dict | None:
    """Nearest panorama: id, position, heading of its centre column, capture [year, month]."""
    pb = (
        f"!1m4!1smaps_sv.tactile!11m2!2m1!1b1!2m4!1m2!3d{lat}!4d{lon}!2d{radius}"
        "!3m10!2m2!1sen!2sUS!9m1!1e2!11m4!1m3!1e2!2b1!3e2!4m10!1e1!1e2!1e3!1e4!1e8!1e6"
        "!5m1!1e2!6m1!1e2"
    )
    r = _get("https://www.google.com/maps/photometa/si/v1", {"pb": pb})
    if r.status_code != 200:
        return None
    try:
        p = json.loads(r.text[4:])[1]
        loc = p[5][0][1]
        date = p[6][7] if len(p) > 6 and p[6] and len(p[6]) > 7 else None
        return {
            "id": p[1][1],
            "lat": loc[0][2],
            "lon": loc[0][3],
            "heading": loc[2][0],
            "date": date,
        }
    except (IndexError, TypeError, ValueError):
        return None


def _tile(pid: str, z: int, x: int, y: int, cache: Path) -> Image.Image | None:
    f = cache / f"{pid}_{z}_{x}_{y}.jpg"
    if not f.exists():
        r = _get(
            "https://streetviewpixels-pa.googleapis.com/v1/tile",
            {"cb_client": "maps_sv.tactile", "panoid": pid, "x": x, "y": y, "zoom": z, "nbt": 1},
        )
        if r.status_code != 200:
            return None
        f.write_bytes(r.content)
    return Image.open(f).convert("RGB")


def crop(pid: str, z: int, x0: float, x1: float, y0: float, y1: float, cache: Path):
    """Part of a panorama; x as fractions of 360 deg (may wrap), y of 180 deg."""
    cache.mkdir(parents=True, exist_ok=True)
    nx, ny = 2**z, 2 ** (z - 1)
    txs = list(range(math.floor(x0 * nx), math.ceil(x1 * nx)))
    tys = list(range(max(0, math.floor(y0 * ny)), min(ny, math.ceil(y1 * ny))))
    img = Image.new("RGB", (len(txs) * _TILE, len(tys) * _TILE))
    cells = [(i, j) for i in range(len(txs)) for j in range(len(tys))]
    with ThreadPoolExecutor(8) as ex:
        tiles = ex.map(lambda c: _tile(pid, z, txs[c[0]] % nx, tys[c[1]], cache), cells)
        for (i, j), t in zip(cells, tiles, strict=True):
            if t:
                img.paste(t.resize((_TILE, _TILE)), (i * _TILE, j * _TILE))
    px = lambda v, t0, n: int((v * n - t0) * _TILE)  # noqa: E731 (local helper)
    return img.crop(
        (px(x0, txs[0], nx), px(y0, tys[0], ny), px(x1, txs[0], nx), px(y1, tys[0], ny))
    )


def _metres(a, b) -> float:
    k = 111_320.0
    return math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * k * math.cos(math.radians(a[0])))


VIEW_Y = (0.36, 0.6)  # vertical band: kerb to sign height


def cmd_capture(a) -> None:
    work = _work(a.city, a.layer)
    recs = [json.loads(x) for x in (work / "samples.jsonl").read_text().splitlines()]
    cache = WORK / "tiles"
    for rec in recs[a.first : a.first + a.count]:
        d = work / "images" / rec["id"]
        meta = d / "meta.json"
        if meta.exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        panos, seen = [], set()
        for k, shot in enumerate(rec["shots"]):
            p = pano(*shot)
            if not p or p["id"] in seen or _metres(shot, (p["lat"], p["lon"])) > PANO_MAX_M:
                continue
            seen.add(p["id"])
            p["n"] = k
            for view, off in (("L", -90), ("R", 90)):
                c = ((rec["bearing"] + off - p["heading"]) / 360 + 0.5) % 1
                x0, x1 = c - 0.25, c + 0.25
                img = crop(p["id"], 3, x0, x1, *VIEW_Y, cache)
                name = f"p{k}_{view}.jpg"
                img.save(d / name, quality=85)
                p[view] = {"file": name, "x0": x0, "x1": x1, "y0": VIEW_Y[0], "y1": VIEW_Y[1]}
            panos.append(p)
        meta.write_text(json.dumps({"id": rec["id"], "panos": panos}, indent=1))
        print(rec["id"], len(panos), [p["date"] for p in panos])


def cmd_zoom(a) -> None:
    """Crop a captured view at zoom 5; U/V are fractions of the view image."""
    img = Path(a.image).resolve()
    meta = json.loads((img.parent / "meta.json").read_text())
    p, view = next(
        (p, p[v]) for p in meta["panos"] for v in "LR" if v in p and p[v]["file"] == img.name
    )
    if not (0 <= a.u0 < a.u1 <= 1 and -1 <= a.v0 < a.v1 <= 2):
        sys.exit("zoom: need 0 <= U0 < U1 <= 1 and -1 <= V0 < V1 <= 2 (V < 0: above the view)")
    x = lambda u: view["x0"] + u * (view["x1"] - view["x0"])  # noqa: E731
    y = lambda v: min(max(view["y0"] + v * (view["y1"] - view["y0"]), 0), 1)  # noqa: E731
    out = img.parent / f"{img.stem}_z_{a.u0:.3f}_{a.v0:.3f}.jpg"
    for z in (5, 4):  # some panoramas stop at zoom 4: their zoom 5 crops come back black
        img = crop(p["id"], z, x(a.u0), x(a.u1), y(a.v0), y(a.v1), WORK / "tiles")
        if img.getbbox():
            break
    img.save(out, quality=90)
    print(out)


def _move(lat: float, lon: float, bearing: float, m: float) -> tuple[float, float]:
    k = 111_320.0
    b = math.radians(bearing)
    return lat + m * math.cos(b) / k, lon + m * math.sin(b) / (k * math.cos(math.radians(lat)))


def _bearing(a, b) -> float:
    k = math.cos(math.radians(a[0]))
    return math.degrees(math.atan2((b[1] - a[1]) * k, b[0] - a[0])) % 360


KERB_M = 5.0  # assumed distance from a panorama (in a traffic lane) to a sign post


def cmd_approach(a) -> None:
    """Look at a far sign from the panorama nearest to it. U, V: the sign in a captured view.
    The sign is taken to stand KERB_M to the side of the camera's path; its distance along
    the street follows from the angle it is seen at."""
    img = Path(a.image).resolve()
    meta = json.loads((img.parent / "meta.json").read_text())
    p, view = next(
        (p, p[v]) for p in meta["panos"] for v in "LR" if v in p and p[v]["file"] == img.name
    )
    samples = img.parent.parent.parent / "samples.jsonl"
    rec = next(
        r for x in samples.read_text().splitlines() if (r := json.loads(x))["id"] == meta["id"]
    )
    x = view["x0"] + a.u * (view["x1"] - view["x0"])
    h = (p["heading"] + (x - 0.5) * 360) % 360  # direction of the sign from the camera
    fwd = (
        rec["bearing"] if math.cos(math.radians(h - rec["bearing"])) >= 0 else rec["bearing"] + 180
    )
    off = (h - fwd + 180) % 360 - 180  # degrees off the street axis, + to the right
    if abs(off) < 3:
        sys.exit("approach: the sign is straight along the street; pick one off to the side")
    along = min(KERB_M / math.tan(math.radians(abs(off))), 80.0)
    sign = _move(*_move(p["lat"], p["lon"], fwd, along), fwd + math.copysign(90, off), KERB_M)
    near = pano(*_move(*sign, fwd, -8.0), radius=12)
    if not near or near["id"] == p["id"]:
        sys.exit(f"approach: no closer panorama (sign ~{along:.0f} m along the street)")
    hb = _bearing((near["lat"], near["lon"]), sign)
    c = (hb - near["heading"]) / 360 + 0.5
    half = a.width / 720
    out = img.parent / f"{img.stem}_a_{a.u:.3f}.jpg"
    for z in (5, 4):
        im = crop(near["id"], z, c - half, c + half, 0.4, 0.6, WORK / "tiles")
        if im.getbbox():
            break
    im.save(out, quality=90)
    print(
        f"{out}  (from a panorama ~{_metres(sign, (near['lat'], near['lon'])):.0f} m from the "
        f"sign, imagery {near['date']}; sign estimated {along:.0f} m along the street)"
    )


# ---------- scoring ----------

_HM = re.compile(r"(\d{1,2}):(\d{2})")


def _minutes(s) -> int | None:
    m = _HM.fullmatch(str(s or "").strip())
    return int(m[1]) * 60 + int(m[2]) if m else None


def _expected_rules(entries) -> set:
    """{(weekday, week|0, start, end)} of a schedule; week 0 = every week."""
    out = set()
    for code, _desc, time in entries or []:
        r = schedule._rule(code)
        if not r or r[0] != "weekly":
            continue
        w = schedule.time_window(time) or (None, None)
        for d in r[1]:
            for k in sorted(r[2]) if r[2] else [0]:
                out.add((d, k, w[0], w[1] % 1440 if w[1] is not None else None))
    return out


def _sign_rules(signs) -> set:
    out = set()
    for s in signs:
        start, end = _minutes(s.get("start")), _minutes(s.get("end"))
        for d in s.get("days") or []:
            for k in s.get("weeks") or [0]:
                out.add((_DAYS.index(d), int(k), start, end % 1440 if end is not None else None))
    return out


def _view_signs(meta: dict, reading: dict, view: str, recent_only: bool = True):
    """Signs of one side, all panoramas: (read signs, readable views, signs not read)."""
    signs, good, unreadable = [], 0, []
    dates = {p["n"]: p.get("date") for p in meta["panos"]}
    for v in reading.get("views", []):
        if v["view"] != view:
            continue
        if recent_only and tuple(dates.get(v["pano"]) or (0, 0)) < STALE_BEFORE:
            continue
        if v.get("coverage") in ("good", "partial"):
            good += 1
        near = [s for s in v.get("signs", []) if s.get("near_kerb", True)]
        signs += [s for s in near if s.get("confidence") == "high"]
        unreadable += [s for s in near if s.get("confidence") != "high"]
    return signs, good, unreadable


def _verdict_sweeping(rec, meta, reading) -> tuple[str, str]:
    view = rec["target"]
    if not any(tuple(p.get("date") or (0, 0)) >= STALE_BEFORE for p in meta["panos"]):
        return ("no_imagery" if not meta["panos"] else "stale"), ""
    signs, good, unread = _view_signs(meta, reading, view)
    sweep = [s for s in signs if s["kind"] == "sweeping"]
    unreadable = any(s["kind"] != "other" for s in unread)
    exp = _expected_rules(rec["expected"][view]["sweeping"])
    if sweep:
        got = _sign_rules(sweep)
        if not exp:
            return "mismatch", f"sign {sorted(got)}; app: no sweeping"
        if got == exp:
            return "match", ""
        same_days = {r[:2] for r in got} == {r[:2] for r in exp}
        what = "time" if same_days else "days"
        return "mismatch", f"{what}: sign {sorted(got)}; app {sorted(exp)}"
    if unreadable:
        return "unreadable", ""
    if not good:
        return "no_view", ""
    return ("match", "no sign, none expected") if not exp else ("absent", "expected a sign")


_AREA = re.compile(r"(?:area|zone)\s+([A-Z]{1,2}|\d+)", re.I)
_SF_RULE = re.compile(r"(\d+) h limit, (\S+) (\d+)(am|pm)–(\d+)(am|pm)", re.I)


def _areas(rules) -> set:
    return {m[1].upper() for r in rules if (m := _AREA.search(r["label"]))}


def _verdict_parking(rec, meta, reading) -> tuple[str, str]:
    if not any(tuple(p.get("date") or (0, 0)) >= STALE_BEFORE for p in meta["panos"]):
        return ("no_imagery" if not meta["panos"] else "stale"), ""
    exp = rec["expected"]["L"]["parking"] + rec["expected"]["R"]["parking"]
    want = _areas(exp)
    found, notes, good, unreadable = set(), [], 0, False
    for view in "LR":
        signs, g, u = _view_signs(meta, reading, view)
        good += g
        unreadable |= any(s["kind"] != "other" for s in u)
        for s in signs:
            if s["kind"] not in ("time_limit", "permit"):
                continue
            if s.get("permit"):
                found.add(str(s["permit"]).upper())
            for r in exp:  # SF curbs give the limit and hours
                m = _SF_RULE.search(r["rule"])
                if m and s.get("limit_h") and int(m[1]) != int(s["limit_h"]):
                    notes.append(f"limit {s['limit_h']} h vs {m[1]} h")
    if found:
        if not want:
            return "mismatch", f"sign area {sorted(found)}; app: none"
        if not found & want:
            return "mismatch", f"sign area {sorted(found)}; app {sorted(want)}"
        return ("mismatch", "; ".join(notes)) if notes else ("match", "")
    if unreadable:
        return "unreadable", ""
    if not good:
        return "no_view", ""
    return ("match", "no sign, none expected") if not want else ("absent", "expected a sign")


def upper_bound(k: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper-Pearson upper bound on a binomial rate."""
    if k >= n:
        return 1.0
    cdf = lambda p: sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))  # noqa: E731
    lo, hi = 0.0, 1.0
    for _ in range(60):  # P(X <= k; n, p) falls with p: bisect for 1 - conf
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > 1 - conf else (lo, mid)
    return hi


def cmd_score(a) -> None:
    work = _work(a.city, a.layer)
    recs = [json.loads(x) for x in (work / "samples.jsonl").read_text().splitlines()]
    verdict = _verdict_sweeping if a.layer == "sweeping" else _verdict_parking
    rows = []
    for rec in recs:
        meta_f = work / "images" / rec["id"] / "meta.json"
        read_f = work / "readings" / f"{rec['id']}.json"
        if not meta_f.exists() or not read_f.exists():
            continue
        meta, reading = json.loads(meta_f.read_text()), json.loads(read_f.read_text())
        v, why = verdict(rec, meta, reading)
        if review := reading.get("review"):  # a person's verdict after looking again
            v, why = review["verdict"], review["note"]
        dates = sorted(f"{d[0]}-{d[1]:02d}" for p in meta["panos"] if (d := p.get("date")))
        tgt = rec["target"]
        exp = rec["expected"][tgt or "L"]
        labels = {r["label"] for v in "LR" for r in rec["expected"][v]["parking"]}
        views = reading.get("views", [])
        rows.append(
            {
                "id": rec["id"],
                "group": rec["group"],
                "main": rec["main"],
                "stratum": rec["stratum"],
                "street": rec["street"],
                "lat": round(rec["lat"], 6),
                "lon": round(rec["lon"], 6),
                "side": f"{tgt}:{exp['side']}" if tgt else "",
                "expected": json.dumps(exp["sweeping"] if tgt else sorted(labels)),
                "read": json.dumps([s.get("text", "") for v in views for s in v.get("signs", [])]),
                "verdict": v,
                "why": why,
                "panos": " ".join(p["id"] for p in meta["panos"]),
                "imagery": dates[-1] if dates else "",
            }
        )
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{a.city}_{a.layer}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    print(f"{a.city} {a.layer}: {len(rows)} samples read -> {out}")
    for group in _GROUPS[a.layer]:
        main = [r for r in rows if r["main"] and r["group"] == group]
        n = sum(r["verdict"] in ("match", "mismatch") for r in main)
        k = sum(r["verdict"] == "mismatch" for r in main)
        ub = upper_bound(k, n)
        state = "PASS" if ub <= BOUND else ("FAIL" if k > 3 else "CONTINUE")
        print(f"  {group}: readable n={n}, mismatches k={k}, 95% upper bound {ub:.3f} -> {state}")
    counts = {}
    for r in rows:
        key = (r["group"], r["stratum"], r["verdict"])
        counts[key] = counts.get(key, 0) + 1
    for (g, s, v), c in sorted(counts.items()):
        print(f"    {g:11s} {s:8s} {v:11s} {c}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("city", choices=sorted(CITIES))
    s.add_argument("layer", choices=LAYERS)
    s.add_argument("--n", type=int, default=200, help="samples per group")
    s.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("capture")
    c.add_argument("city", choices=sorted(CITIES))
    c.add_argument("layer", choices=LAYERS)
    c.add_argument("--first", type=int, default=0)
    c.add_argument("--count", type=int, default=40)
    z = sub.add_parser("zoom")
    z.add_argument("image")
    for k in ("u0", "u1", "v0", "v1"):
        z.add_argument(k, type=float)
    ap_ = sub.add_parser("approach")
    ap_.add_argument("image")
    ap_.add_argument("u", type=float)
    ap_.add_argument("--width", type=float, default=30.0, help="field of view, degrees")
    r = sub.add_parser("score")
    r.add_argument("city", choices=sorted(CITIES))
    r.add_argument("layer", choices=LAYERS)
    a = ap.parse_args()
    {
        "sample": cmd_sample,
        "capture": cmd_capture,
        "zoom": cmd_zoom,
        "approach": cmd_approach,
        "score": cmd_score,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
