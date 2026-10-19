# Data

Each city: `manifests/<city>.yaml` (`regions.yaml` groups cities, sets timezone and map view) and a committed
FlatGeobuf at the manifest's `fgb_path`, loaded by the server at startup. Pipeline: [docs/operations.md](../docs/operations.md#3-data).

## Rebuild

```bash
python scripts/rebuild_city_data.py [city ...]   # inputs per the manifest's source: block
python scripts/build_pmtiles.py                  # map tiles; needs tippecanoe
python scripts/build_layers.py [city ...] [--theme parking ...]   # extra layers
```

Commit the `.fgb` and `frontend/tiles/*.pmtiles` outputs. A new city needs a manifest, and a normaliser in
`schemas.SCHEMA_PROFILES` if its source format is new.

## Manifest keys

- `holidays:` → `holidays/<city>.yaml`: published holidays and what each suspends: `sweeping` (Oakland, SF only)
  or `trash` (moved by `trash.holiday_rule`: `shift_week` or `next_day`).
- `trash:` → `{kind: recollect, area, service_id}` (pickups looked up by address, Oakland) or `{kind: weekly}`
  (the user picks the home's day).
- `layers:` → extra map layers. Permit areas never overlap: within a city the first by label keeps a shared
  edge, across cities the newer source. Chicago's permit zones by address: `chicago/permit_zones.csv`.
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
Codes in `analysis.NO_SWEEP_CODES` (`N`, `NS`, `MS`, …) mean no sweeping.
