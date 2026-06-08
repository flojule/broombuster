"""City + region configs, loaded from data/manifests/*.yaml.

CITIES maps city_key -> config (name, center, manual_default, local_path,
url, schema, bbox, fgb_path, plus optional stale_after_days / schedule_pdf_url).
REGIONS groups cities geographically (name, cities, center, tz, overview frame).

A manifest names a normaliser profile via `schema`; it never remaps columns,
so each city's distinct format stays handled by its own
data_loader.SCHEMA_PROFILES entry. Add a city by dropping a new YAML in
data/manifests/ (and a new SCHEMA_PROFILES entry if its format is novel).
"""

from broombuster.manifest import load_all

CITIES, REGIONS = load_all()
