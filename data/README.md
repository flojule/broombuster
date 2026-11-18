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
| Dublin | city swept-street lines (ArcGIS): 1st & 3rd or 2nd & 4th weekday | same lines, both sides | every line (enforcement unpublished) |
| Livermore | city route polygons (ArcGIS): one weekday a month; "Call 960-8020" routes: check the sign | county centerlines (ArcGIS), both sides | every route (enforcement unpublished) |
| Emeryville | city route lines (ArcGIS): week, weekday, 8 AM–12 PM or 8 PM–5 AM | county centerlines (ArcGIS); curb lines give sides | every line |
| San Leandro | city zones, commercial routes, posted streets (ArcGIS) | county centerlines (ArcGIS), both sides | every block (enforcement unpublished); posted streets: check the sign |
| Hayward | city PDF: week, weekday per address range and parity; window not given (all day) | county centerlines (ArcGIS) | every row (city: cited on sweeping days) |
| Albany | city zone pages, transcribed in `albany/zones.yaml`: odd side 3rd, even side 4th Wed/Thu, window per zone | county centerlines (ArcGIS) | every listed street |

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
