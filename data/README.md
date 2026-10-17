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

## Holidays, trash, extra layers

- `holidays/<city>.yaml` (manifest `holidays:`): the city's published holidays and what
  each suspends: `sweeping` (no sweeping that day; only where the city publishes it:
  Oakland, SF) or `trash` (the manifest's `trash.holiday_rule` moves pickups:
  `shift_week` or `next_day`). Add next year's dates before December.
- Manifest `trash:`: `{kind: recollect, area, service_id}` looks pickups up by address
  (Oakland); `{kind: weekly}` has the user pick the home's collection day.
- Manifest `layers:` lists the city's extra map layers, built by
  `python scripts/build_layers.py [city ...] [--theme parking ...]` from the sources in
  `broombuster/layer_sources.py` (Berkeley and Oakland ArcGIS, DataSF and Chicago
  Socrata, the DOE/NLR EV station API, `places/markets.yaml`). It writes
  `<city dir>/<theme>.fgb` (label, rule, asof only, clipped to the city's bbox) for the
  server's `/api/rules`, and `frontend/layers/<region>/<theme>.geojson` (label, city) for
  the map. Chicago's permit zones by address go to `chicago/permit_zones.csv`.
  Commit the outputs. A source that fails keeps its previous files.
- `places/markets.yaml`: weekly farmers markets, kept by hand (no open feed exists).

## Columns

| Column | |
|---|---|
| `STREET_NAME`, `STREET_KEY`, `STREET_DISPLAY` | Upper-case name, comparison key, display form |
| `DAY_EVEN`, `DAY_ODD` | Sweep code per side (below); empty = no sweeping |
| `DESC_*`, `TIME_*` | Source description, time window (`8AM–11AM`; a bare `AM` / `PM` is a morning / afternoon route) |
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
