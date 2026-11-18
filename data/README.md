# Data

Each city: `manifests/<city>.yaml` (`regions.yaml` groups cities, sets timezone and map view) and a committed
FlatGeobuf at the manifest's `fgb_path`, loaded by the server at startup. Pipeline: [docs/architecture.md](../docs/architecture.md#3-data).

## Rebuild

```bash
uv run --extra scripts python scripts/rebuild_city_data.py [city ...]   # inputs per the manifest's source: block
uv run python scripts/build_pmtiles.py           # map tiles; needs tippecanoe at TIPPECANOE_VERSION
uv run python scripts/build_layers.py [city ...] [--theme parking ...]   # extra layers
uv run --extra scripts python scripts/build_icons.py   # app icons; needs the system cairo library
```

| Tool | macOS | Ubuntu |
|---|---|---|
| tippecanoe | `brew install tippecanoe` | build the pinned release from github.com/felt/tippecanoe |
| cairo | `brew install cairo` | `sudo apt install libcairo2` |

Commit the `.fgb` and `frontend/tiles/*.pmtiles` outputs. A new city needs a manifest, and a normalizer in
`schemas.SCHEMA_PROFILES` if its source format is new. A rebuild with no `git diff` means the committed data
equals the source.

## Sources

| City | Schedule | Blocks and sides | Enforced where |
|---|---|---|---|
| Oakland | city sweeping layer (ArcGIS; behind the city's map) | same layer | codes other than no-sweep |
| San Francisco | DataSF `yhqp-riqs` | same dataset | every row |
| Berkeley | city PDFs: day, AM/PM per address range | city centerlines (ArcGIS) | `mech_sweep = enforced` |
| Alameda | city sweeping map (ArcGIS, no geometry) | county centerlines (ArcGIS) | signed sides only |
| Chicago | Data Portal zones, one dataset per year | same dataset | every zone |
| Vacaville | Recology layer (ArcGIS): weekly, or every other week (green / yellow calendar) | same layer (Solano County centerlines) | every row: no source says unticketed; no signs on 3 Street View blocks |
| Sonoma County (unincorporated) | Recology layer (ArcGIS): 4-week rotation per 2026 calendar, or weekly | same layer, no ranges | every row: Recology asks to move cars; no signs on 3 Street View blocks |
| Fairfield | city residential areas (monthly, twice Nov–Jan) and commercial routes (ArcGIS) | Solano County centerlines (ArcGIS), city-owned | every row: no source says unticketed; no signs on 3 Street View blocks |
| Benicia | city areas (ArcGIS, 2023) + Republic Services 2025 brochure (industrial, downtown, 100 blocks off First St) | Solano County centerlines (ArcGIS), city-owned | every row: brochure asks to move cars; no signs on 3 Street View blocks |
| Napa | city route areas (ArcGIS): monthly, twice Oct–Jan | city sweeping-contract segments (ArcGIS) | every row: city asks to move cars; no signs on 3 Street View blocks |
| San Ramon | city zones (ArcGIS), named by schedule | Contra Costa streets: Census TIGER/Line EDGES 2026 (public domain) | every zone; tickets not published (assumed); "Commercial" zone `POSTED` |
| Danville | town zones (ArcGIS): monthly day | Contra Costa TIGER/Line EDGES | every zone; tickets not published (assumed); corridors (twice a month) and downtown (weekly) not in the source |
| Oakley | city street list (PDF, 2025-03) | city route lines (ArcGIS) on Contra Costa TIGER/Line EDGES | every listed street; tickets not published (assumed) |
| Pittsburg | city zones and commercial routes (ArcGIS) | Contra Costa TIGER/Line EDGES | zones with `Code_Enforcement = Yes`, others `NT`; commercial routes assumed enforced |
| San Rafael | city sweeper zones 2025 (ArcGIS), Apr–Oct 2026 | city swept streets (ArcGIS) | Canal residential zone only (No Parking Mon 8–10 AM signs read in Street View); elsewhere NT: page says "Move cars", no ordinance in SRMC 5.40/5.60, no signs seen |
| Mill Valley | Mill Valley Refuse street lists | Marin County roads (MarinMap, as is) | none (NT): no ordinance or request found, no signs seen |
| Corte Madera | Mill Valley Refuse street lists + Town public works PDF | Marin County roads | none (NT): Town page asks nothing of parked cars, no signs seen |
| Tiburon | Mill Valley Refuse street lists | Marin County roads | none (NT): no ordinance or request found, no signs seen |
| Sausalito | city sweeping table | Marin County roads | none (NT): "please try to minimize on-street parking", no signs seen |
| Belvedere | city route lists (route N = week N, Thu or Fri) | Marin County roads | none (NT): page asks nothing of parked cars, no signs seen |
| South San Francisco | city sweeping lookup (ArcGIS, 2021): day, weeks, posted No Parking | same layer | posted No Parking only; sweeping itself NT; "Mon-Tue" signs POSTED (side not on file) |
| Daly City | city schedule page: odd / even side weekday and hours | county centerlines (ArcGIS) by name, parts by cross streets | every listed side; block of a multi-part street not placed: POSTED |
| San Mateo | city sweeping parcels (ArcGIS): day, weeks, hours | county centerlines, side by address parity | every side (city names no unticketed sides); bi-weekly Saturday: POSTED |
| Millbrae | city sweeping parcels (ArcGIS): day, hours, car removal | county centerlines, side by address parity | "posted mandatory car removal"; "requested" NT; no word kept enforced |
| San Bruno | city sweeping routes (ArcGIS): weeks, day, hours | county centerlines within 15 m of a curb route | routes with posted hours; others NT |
| San Mateo County (unincorporated) | county Public Works routes (ArcGIS) | same layer | routes with hours (ordinance schedules); others NT |
| Burlingame | city sweeping sections (ArcGIS, 2018): day, hours, track | county centerlines in the section | weekly and downtown sections; tracks A / B NT |
| Menlo Park | city sweeping districts (ArcGIS): day, seasonal frequency | county centerlines in the district | none: NT (no hours or signs published) |

San Mateo County centerlines: County of San Mateo ISD GIS, which cannot vouch for their accuracy.
| Dublin | city swept-street lines (ArcGIS): 1st & 3rd or 2nd & 4th weekday | same lines, both sides | every line (enforcement unpublished) |
| Livermore | city route polygons (ArcGIS): one weekday a month; "Call 960-8020" routes: check the sign | county centerlines (ArcGIS), both sides | every route (enforcement unpublished) |
| Emeryville | city route lines (ArcGIS): week, weekday, 8 AM–12 PM or 8 PM–5 AM | county centerlines (ArcGIS); curb lines give sides | every line |
| San Leandro | city zones, commercial routes, posted streets (ArcGIS) | county centerlines (ArcGIS), both sides | every block (enforcement unpublished); posted streets: check the sign |
| Hayward | city PDF: week, weekday per address range and parity; window not given (all day) | county centerlines (ArcGIS) | every row (city: cited on sweeping days) |
| Albany | city zone pages, transcribed in `albany/zones.yaml`: odd side 3rd, even side 4th Wed/Thu, window per zone | county centerlines (ArcGIS) | every listed street |
| San Jose | city routes (ArcGIS, CC BY City of San Jose): residential day/week/posted hours; major roads one row per side | city centerlines by `CENTERLINEID` | `Signed Route` rows; residential two-window signs (one window per side, side not published) get one span on both sides |
| Cupertino | city signed segments + 8 zones (ArcGIS) | county centerlines (ArcGIS) | signed segments; zones NT |
| Milpitas | Milpitas Sanitation routes (ArcGIS), no hours | county centerlines | enforcement not published per street: every block (pilot areas sign a 2-hour window, not published) |
| Los Gatos | town route polygons (ArcGIS) | county centerlines | none: no parking restrictions (NT) |

Berkeley's signs read 9 AM–12 noon (AM routes) and 12:30–3:30 PM (PM routes). `tests/test_api.py` pins signs
read on the street.

## Manifest keys

- `source:` → raw inputs: `url` (download), `arcgis` (`{file: layer URL | {url, where}}`), `files` (the build
  reads exactly these), `build` (script producing `local_path`).
- `holidays:` → `holidays/<city>.yaml`: published holidays and what each suspends: `sweeping` (Oakland, SF),
  `sweeping_day` (SF: only sweeps starting at 6 AM or later; night cleaning stays enforced) or `trash` (moved by
  `trash.holiday_rule`: `shift_week` or `next_day`).
- `corrections:` → `<city>/corrections.yaml`: raw-row edits where posted signs (Street View) contradict the
  source; each entry's `set` applies to rows equal to its `match`; a match on no row stops the build.
- `trash:` → `{kind: recollect, area, service_id}` (pickups looked up by address, Oakland) or `{kind: weekly}`
  (the user picks the home's day).
- `layers:` → extra map layers. Permit areas never overlap: within a city the first by label keeps a shared
  edge, across cities the newer source. Chicago's permit zones by address: `chicago/permit_zones.csv`.
- `streetview_since:` → `YYYY-MM`: oldest Street View imagery `scripts/validate_streetview.py` counts as
  evidence of today's signs.
- `places/markets.yaml`: farmers markets, kept by hand.

## Columns

| Column | |
|---|---|
| `STREET_NAME`, `STREET_KEY`, `STREET_DISPLAY` | Upper-case name, comparison key, display form |
| `DAY_EVEN`, `DAY_ODD` | Sweep code per side; empty = none |
| `DESC_*`, `TIME_*` | Source description; time window (`8AM–11AM`; bare `AM`/`PM` = morning/afternoon route) |
| `L_F_ADD`, `L_T_ADD`, `R_F_ADD`, `R_T_ADD` | Address ranges, to pick the car's side |
| `SIDE_EVEN`, `SIDE_ODD` | Optional side labels (SF compass sides) |
| `geometry` | Street line, or zone polygon (Chicago) |

## Sweep codes

```
code     := DAYS [ "E" | ORDINALS ] | "E" | "DATES:" yyyy-mm-dd{,yyyy-mm-dd}
DAYS     := TH SU M T W F S (one or more)
ORDINALS := week-of-month digits 1..5
```

`ME` = every Monday, `M13` = 1st and 3rd Monday, `TTH` = Tuesdays and Thursdays, `E` = every day.
| Code | Meaning | Shown |
|---|---|---|
| `POSTED` (`analysis.UNDATED_CODES`) | swept on a day no source gives (Oakland's `S` / `SU`; a side the source leaves blank) | "check the sign", hours if known |
| `NT` | swept, nobody ticketed (unsigned sides, courtesy programs) | the description ("1st Tue, no tickets"), never urgent |
| `analysis.NO_SWEEP_CODES` (`N`, `NS`, `MS`, …) | no sweeping | "No sweeping" |

`scripts/citykit.py` turns schedule words ("1st & 3rd Tuesday") into codes for the city build scripts.
