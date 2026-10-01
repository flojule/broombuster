# BroomBuster Data Directory

Only the **runtime artifacts** are committed: one normalised FlatGeobuf per city
(EPSG:4326), read by `src/broombuster/data_loader.py`. Raw shapefiles, PDFs and
intermediate GeoJSONs are reproducible and kept out of git.

```
data/
  manifests/<city>.yaml       ← one per city: schema, bbox, fgb_path, source block
  manifests/regions.yaml      ← city groupings, timezone, overview frame
  oakland/StreetSweeping.fgb
  san_francisco/StreetSweeping.fgb
  berkeley/StreetSweeping.fgb
  alameda/StreetSweeping.fgb
  chicago/StreetSweepingZones.fgb
  app.sqlite                  ← runtime accounts/prefs DB (gitignored, created on boot)
```

## Rebuilding a city

Each manifest's `source:` block says where the raw input comes from:

| Key | Meaning |
|---|---|
| `local_path` | Raw input that `data_loader.build_city_fgb` normalises into `fgb_path` |
| `url` | Downloaded straight to `local_path` (SF, Chicago) |
| `files` | Extra inputs: URLs are downloaded, local paths must be present (manual downloads) |
| `sha256` | Expected hashes of those inputs; a mismatch is reported |
| `build` | Script that turns the inputs into `local_path` (Berkeley, Alameda PDFs) |
| `notes` | Where to get the data by hand |

```bash
python scripts/rebuild_city_data.py <city_key> [--force]   # or no key for all
python scripts/build_pmtiles.py                           # then regenerate the map tiles
```

Commit the new `.fgb` and `frontend/tiles/*.pmtiles`; deployments ship data
through git. `/health` reports the age (from the last commit) of cities with a
`stale_after_days` (SF 30, Chicago 90) and flags them `stale` past it.

## Normalised schema

Every city's frame shares these columns (`schemas.SCHEMA_COLS`); `analysis.py`
and `maps.py` rely on nothing else. `None`/`NaN` in a schedule column means no
sweeping on that side.

| Column | Description |
|---|---|
| `STREET_NAME` | Full street name, upper-case (e.g. `"MOSLEY AVE"`, `"WARD 05, SECTION 03"`) |
| `STREET_KEY` | Comparison key, `normalize.street_name` (e.g. `"MOSLEY"`) |
| `STREET_DISPLAY` | Readable short form, `normalize.street_display` (e.g. `"Mosley Ave"`) |
| `DAY_EVEN` / `DAY_ODD` | Sweep-day code per side (grammar below) |
| `DESC_EVEN` / `DESC_ODD` | Source's human description (display is rebuilt from the codes) |
| `TIME_EVEN` / `TIME_ODD` | Time window, e.g. `"8AM–11AM"` |
| `L_F_ADD`, `L_T_ADD`, `R_F_ADD`, `R_T_ADD` | Left/right address ranges (`NaN` when unknown) |
| `SIDE_EVEN` / `SIDE_ODD` | Optional side labels (SF compass sides); absent = Even/Odd |
| `geometry` | `LineString`/`MultiLineString`, or `MultiPolygon` zones (Chicago) |

## Sweep-day codes

Parsed by `analysis` (and mirrored in `frontend/js/urgency.js`):

```
code     := DAYS [ "E" | ORDINALS ] | "E" | "DATES:" iso-date{,iso-date}
DAYS     := one or more of TH SU M T W F S
ORDINALS := week-of-month digits 1..5
```

| Example | Meaning |
|---|---|
| `ME`, `THE`, `E` | Every Monday, every Thursday, every day |
| `M13`, `F24`, `W1` | 1st & 3rd Monday, 2nd & 4th Friday, 1st Wednesday |
| `MWF`, `TTH` | Every Mon/Wed/Fri, every Tue/Thu |
| `DATES:2026-04-01,…` | Explicit dates (Chicago's yearly calendar) |
| `N`, `NS`, `O`, `MS`, `DM`, `MISSING`, … | No sweeping (`analysis.NO_SWEEP_CODES`) |

## Per-city notes

- **Oakland** (`schema: oakland`): city shapefile (EPSG:2227, reprojected).
  `NAME`+`TYPE` → `STREET_NAME`; `DescDay*`/`DescTime*` → `DESC_*`/`TIME_*`; day
  codes and address ranges are already in this schema. No stable download URL.
- **San Francisco** (`schema: sf`): DataSF `yhqp-riqs`, one row per (block side ×
  weekday). Codes are derived from `week_day` + the `week_N_of_month` flags;
  `cnnrightleft` picks the side bucket and `blockside` the compass label.
  `analysis.schedules_for_all_matching_rows` re-unions a block's rows for the card.
- **Berkeley** (`schema: berkeley`): three PDFs, geocoded against OpenStreetMap
  by `scripts/build_berkeley_geojson.py`. "1st Wed" rows become recurring
  week-of-month codes (`W1`).
- **Alameda** (`schema: alameda`): one PDF of weekly rows, built by
  `scripts/build_alameda_geojson.py`; block-based address ranges
  (`block … block + 98` even, `block + 1 … block + 99` odd).
- **Chicago** (`schema: chicago`): ward-section zone polygons with month columns
  of day numbers, turned into `DATES:` codes. The year is inferred from weekday
  alignment (`schemas._infer_chicago_year`). The city republishes under a new
  dataset id each spring: update `source.url` and `schedule_pdf_url` in
  `manifests/chicago_all.yaml`. Cars resolve by point-in-polygon.

## Adding a city

1. Add `data/manifests/<city>.yaml` (copy a similar city) and list the city in
   `regions.yaml`.
2. If its format is new, add a normaliser to `schemas.SCHEMA_PROFILES`; use
   `normalize` helpers for every name/time comparison.
3. Run `scripts/rebuild_city_data.py <city>` and `scripts/build_pmtiles.py`, then
   commit the `.fgb` and tiles.
