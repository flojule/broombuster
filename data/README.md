# Data

Each city has a manifest in `manifests/<city>.yaml` (`regions.yaml` groups the
cities and sets each region's timezone and map view) and a committed FlatGeobuf
at the manifest's `fgb_path`, which the server loads at startup.

## Rebuild

```bash
python scripts/rebuild_city_data.py [city ...]   # inputs per the manifest's source: block
python scripts/build_pmtiles.py                  # map tiles; needs tippecanoe
```

Commit the `.fgb` and `frontend/tiles/*.pmtiles` files. A new city needs a
manifest, plus a normaliser in `schemas.SCHEMA_PROFILES` if its source format is
new.

## Columns

| Column | |
|---|---|
| `STREET_NAME`, `STREET_KEY`, `STREET_DISPLAY` | Upper-case name, comparison key, display form |
| `DAY_EVEN`, `DAY_ODD` | Sweep code per side (below); empty = no sweeping |
| `DESC_*`, `TIME_*` | Source description, time window (`8AM–11AM`) |
| `L_F_ADD`, `L_T_ADD`, `R_F_ADD`, `R_T_ADD` | Address ranges, used to pick the car's side |
| `SIDE_EVEN`, `SIDE_ODD` | Optional side labels (SF compass sides) |
| `geometry` | Street line, or zone polygon (Chicago) |

## Sweep codes

```
code     := DAYS [ "E" | ORDINALS ] | "E" | "DATES:" yyyy-mm-dd{,yyyy-mm-dd}
DAYS     := TH SU M T W F S (one or more)
ORDINALS := week-of-month digits 1..5
```

`ME` = every Monday, `M13` = 1st and 3rd Monday, `TTH` = every Tuesday and
Thursday, `E` = every day. The codes in `analysis.NO_SWEEP_CODES` (`N`, `NS`,
`MS`, …) mean no sweeping.
