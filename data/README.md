# BroomBuster Data Directory

This directory contains street-sweeping schedule data for each supported city.
Each city has its own subdirectory. The files are consumed by `src/data_loader.py`,
which normalises every source into a shared column schema before handing the data
to `src/analysis.py` and `src/maps.py`.

---

## Directory Layout

Only the **runtime artifacts** (`.fgb`) are committed. Raw shapefiles, source
PDFs, and intermediate GeoJSONs are reproducible via the rebuild script and
are kept out of git.

```
data/
  README.md                          ← this file
  sources.yaml                       ← origin URLs + SHA256s for raw inputs
  alameda/StreetSweeping.fgb         ← runtime: normalised, in EPSG:4326
  berkeley/StreetSweeping.fgb
  chicago/StreetSweepingZones.fgb
  oakland/StreetSweeping.fgb
  san_francisco/StreetSweeping.fgb
```

The runtime cares only about the `.fgb` files. Each was produced from a raw
upstream source documented in [`sources.yaml`](sources.yaml) — official
shapefiles, GeoJSON exports, or PDF schedules.

### Rebuilding from upstream

To refresh a city's `.fgb` from its upstream source, run:

```bash
python scripts/rebuild_city_data.py [<city_key> ...]
python scripts/rebuild_city_data.py --force         # ignore SHA cache
```

The script downloads what it can (SF, Chicago, Berkeley PDFs) and prints a
manual download instruction for cities with no stable URL (Oakland, Alameda).
Once the raw input is in place, it runs the appropriate normaliser and writes
the new `.fgb`.

### Auto-refresh at runtime

SF and Chicago auto-refresh in the background while the server runs: when
the cached file is older than its `stale_after_days` (SF: 30 days, Chicago:
90 days), `data_loader.py` re-downloads from the upstream URL recorded in
`cities.py`. Berkeley, Alameda, and Oakland do not auto-refresh — they
require the explicit `rebuild_city_data.py` invocation.

### Local-only files (never committed)

| File | Purpose |
|---|---|
| `data/app.sqlite` (and `-wal`, `-shm`, `-journal`) | User accounts and prefs database. Created on first server start. |
| Raw inputs (`*.shp`, `*.pdf`, `*.geojson` etc.) | Reproducible from `sources.yaml`. Kept out of git via `.gitignore`. |

---

## Normalised Schema

After loading, **every city's GeoDataFrame shares this column schema** regardless
of the original source format. All downstream code (`analysis.py`, `maps.py`)
relies exclusively on these columns.

| Column | Type | Description |
|---|---|---|
| `STREET_NAME` | `str` | Full street name, upper-case, whitespace-normalised (e.g. `"MOSLEY AVE"`) |
| `DAY_EVEN` | `str \| None` | Sweep-day code for **even**-numbered addresses (see codes below) |
| `DAY_ODD` | `str \| None` | Sweep-day code for **odd**-numbered addresses |
| `DESC_EVEN` | `str \| None` | Human-readable schedule for even side (e.g. `"Every Fri"`) |
| `DESC_ODD` | `str \| None` | Human-readable schedule for odd side |
| `TIME_EVEN` | `str \| None` | Sweep time window for even side (e.g. `"8AM–11AM"`) |
| `TIME_ODD` | `str \| None` | Sweep time window for odd side |
| `L_F_ADD` | `float \| NaN` | Left-side **from** address number |
| `L_T_ADD` | `float \| NaN` | Left-side **to** address number |
| `R_F_ADD` | `float \| NaN` | Right-side **from** address number |
| `R_T_ADD` | `float \| NaN` | Right-side **to** address number |
| `geometry` | Shapely | `LineString` for line-based cities; `MultiPolygon` for Chicago zones |

`None` / `NaN` in schedule columns means no sweeping is scheduled for that side.

---

## Sweep-Day Code Reference

`DAY_EVEN` / `DAY_ODD` use an Oakland-style code system parsed by
`analysis.parse_sweeping_code()`.

### Weekly codes

| Code | Meaning |
|---|---|
| `ME` | Every Monday |
| `TE` | Every Tuesday |
| `WE` | Every Wednesday |
| `THE` | Every Thursday |
| `FE` | Every Friday |
| `SE` | Every Saturday |
| `SUE` | Every Sunday |
| `E` | Every day |

### Nth-of-month codes

| Code | Meaning |
|---|---|
| `M13` | 1st & 3rd Monday |
| `M24` | 2nd & 4th Monday |
| `T13` / `T24` | Tuesday variants |
| `W13` / `W24` | Wednesday variants |
| `TH13` / `TH24` | Thursday variants |
| `F13` / `F24` | Friday variants |
| `S13` / `S24` | Saturday variants |

### Compound codes (Oakland only)

| Code | Meaning |
|---|---|
| `MWF` | Every Mon, Wed, Fri |
| `TTH` | Every Tue, Thu |
| `TTHS` | Every Tue, Thu, Sat |
| `MF` | Every Mon, Fri |

### No-sweep sentinels

| Code | Meaning |
|---|---|
| `N` | No sweeping |
| `NS` | No sweeping |
| `O` | No sweeping (other side only) |

### Explicit date list (Berkeley, Chicago)

```
DATES:2026-04-01,2026-04-15,2026-05-06,...
```

Used when the schedule is not a simple weekly/bi-weekly pattern.
The parser accepts any number of ISO-8601 dates separated by commas.

---

## City-by-City Source Details

### Oakland — Shapefile (`schema: "oakland"`)

- **Runtime:** `data/oakland/StreetSweeping.fgb` (committed)
- **Raw input:** `data/oakland/StreetSweeping.{shp,dbf,shx,prj,cpg}` (not committed; rebuild via `scripts/rebuild_city_data.py oakland` — see `sources.yaml` for the manual portal-download instructions)
- **CRS:** EPSG:2227 (California State Plane III, feet) — reprojected at load time
- **Geometry:** `LineString` (one segment per street block side)
- **Source:** Oakland open data portal (manual export; no auto-download URL)

Key raw columns mapped to the standard schema:

| Raw column | Maps to |
|---|---|
| `NAME` + `TYPE` | `STREET_NAME` |
| `DAY_EVEN` | `DAY_EVEN` (already Oakland codes) |
| `DAY_ODD` | `DAY_ODD` |
| `DescDayEve` | `DESC_EVEN` |
| `DescTimeEv` | `TIME_EVEN` |
| `DescDayOdd` | `DESC_ODD` |
| `DescTimeOd` | `TIME_ODD` |
| `L_F_ADD` / `L_T_ADD` | even-side address range |
| `R_F_ADD` / `R_T_ADD` | odd-side address range |

---

### San Francisco — GeoJSON (`schema: "sf"`)

- **Runtime:** `data/san_francisco/StreetSweeping.fgb` (committed, ~11 MB)
- **Raw input:** `data/san_francisco/StreetSweeping.geojson` (not committed; auto-downloaded ~17 MB)
- **Refresh URL:** `https://data.sfgov.org/resource/yhqp-riqs.geojson?$limit=200000`
- **Auto-refresh:** when file is older than 30 days (background, no restart needed)
- **CRS:** EPSG:4326
- **Geometry:** `LineString`
- **Source:** DataSF — Street Sweeping Schedule (dataset `yhqp-riqs`)

The raw SF data uses a different encoding; the normaliser derives Oakland-style
codes from the source columns:

| Raw column | Role |
|---|---|
| `corridor` | street name |
| `blockside` | `"EVEN"`, `"ODD"`, or `"BOTH"` |
| `weekday` / `week_day` | day of week (integer 1–7 or string) |
| `fromhour` / `from_hour` | sweep start hour (24-h integer) |
| `tohour` / `to_hour` | sweep end hour |
| `week_1_of_month` … `week_5_of_month` | 1/0 flags; all set → `"E"` suffix (every week) |

The derived code is `<DAY_LETTER><ORDINAL>`, e.g. `"ME"` (every Mon),
`"F13"` (1st & 3rd Fri). Address ranges are not available in SF data
(`L_F_ADD` etc. are set to `NaN`).

---

### Berkeley — GeoJSON (`schema: "berkeley"`, prebuilt)

- **Runtime:** `data/berkeley/StreetSweeping.fgb` (committed)
- **Raw input:** `data/berkeley/*.pdf` (not committed; auto-downloaded by `scripts/rebuild_city_data.py berkeley` or manually fetched from
  `https://berkeleyca.gov/city-services/streets-sidewalks-sewers-and-utilities/street-sweeping`)
- **Build script:** `scripts/build_berkeley_geojson.py` (called by the rebuild orchestrator)
- **CRS:** EPSG:4326
- **Geometry:** `LineString`

Berkeley schedules are nth-weekday patterns (e.g. "2nd Mon of each month"),
so `DAY_EVEN`/`DAY_ODD` use the `DATES:` format with explicit ISO dates.
Address ranges come from the PDF (`L_F_ADD` / `R_T_ADD` present).

The build script:
1. Parses three PDFs (streets A–G, H–Z, numbered streets)
2. Fetches street geometry from OpenStreetMap via Overpass API
3. Joins schedule rows to geometry by normalised street name
4. Writes `data/berkeley/StreetSweeping.geojson`

---

### Alameda — GeoJSON (`schema: "alameda"`, prebuilt)

- **Runtime:** `data/alameda/StreetSweeping.fgb` (committed)
- **Raw input:** `data/alameda/street-sweeping-schedule.pdf` (not committed; manual download from
  `https://www.alamedaca.gov/Residents/Transportation-and-Streets/Street-Sweeping-Schedule`)
- **Build script:** `scripts/build_alameda_geojson.py` (called by the rebuild orchestrator)
- **CRS:** EPSG:4326
- **Geometry:** `LineString`

Alameda uses simple weekly patterns (`ME`, `THE`, etc.).
Address ranges are block-based: `L_F_ADD = block`, `L_T_ADD = block + 98`,
`R_F_ADD = block + 1`, `R_T_ADD = block + 99`.

The build script:
1. Parses the PDF schedule (one row per block/side/day)
2. Fetches Alameda street geometry from OpenStreetMap via Overpass API
3. Joins and writes `data/alameda/StreetSweeping.geojson`

---

### Chicago — GeoJSON (`schema: "chicago"`)

- **Runtime:** `data/chicago/StreetSweepingZones.fgb` (committed, ~2 MB)
- **Raw input:** `data/chicago/StreetSweepingZones.geojson` (not committed; auto-downloaded ~5 MB)
- **Refresh URL:** `https://data.cityofchicago.org/resource/utb4-q645.geojson?$limit=50000`
- **Auto-refresh:** when file is older than 90 days (background, no restart needed)
- **CRS:** EPSG:4326
- **Geometry:** `MultiPolygon` (ward zones, not street lines)
- **Source:** Chicago Data Portal — Street Sweeping Zones (dataset `utb4-q645`)

Chicago publishes a new dataset each year (typically March/April).
When that happens, update the dataset ID in `src/cities.py` (the URL `utb4-q645`
segment) and run `scripts/rebuild_city_data.py chicago_all --force`.

The raw data has month columns (`april` … `november`) containing
comma-separated day-of-month numbers:

```json
{ "ward_id": "1", "section_id": "1", "april": "1,2", "may": "13,14", ... }
```

The normaliser converts these to `DATES:` codes. Because Chicago uses zone
polygons rather than street lines, address ranges are not available and the
car-location check uses a point-in-polygon test instead.

---

## Adding a New City

1. **Create the data file** — either download raw data or write a build script
   that produces a GeoJSON with `LineString` or `Polygon`/`MultiPolygon` geometry.

2. **Write a normaliser** in `src/data_loader.py` — implement
   `_normalise_<city>(gdf)` that maps source columns to the standard schema.
   Add the key to the `dispatch` dict in `_normalise()`.

3. **Register the city** in `src/cities.py` — add an entry to `CITIES` and
   include it in the appropriate `REGIONS` list.

4. **Use `normalize.street_name()`** from `src/normalize.py` for any internal
   name comparisons; do not add new local normalization functions.

---

## Rebuilding from upstream

The recommended entry point is the orchestrator, which downloads what it can,
verifies SHA256 against `sources.yaml`, runs the per-city normaliser, and writes
a fresh `.fgb`:

```bash
python scripts/rebuild_city_data.py                  # all cities
python scripts/rebuild_city_data.py oakland          # one city
python scripts/rebuild_city_data.py san_francisco --force   # ignore SHA cache
```

Per-city build scripts (called internally by the orchestrator) can also be
run directly during development:

```bash
python scripts/build_alameda_geojson.py    # requires data/alameda/street-sweeping-schedule.pdf
python scripts/build_berkeley_geojson.py   # requires data/berkeley/*.pdf
```
