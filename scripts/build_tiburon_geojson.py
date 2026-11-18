#!/usr/bin/env python3
"""
Build data/tiburon/StreetSweeping.geojson from Mill Valley Refuse Service's sweeping lists
(scripts/mvrs.py) and the county's road centerlines (fetched by
scripts/rebuild_city_data.py tiburon).

Usage (from repo root):  python scripts/build_tiburon_geojson.py
Output: data/tiburon/StreetSweeping.geojson

The page's "downtown area ... swept every Monday and Friday" names no streets: not mapped.
"""

import mvrs

if __name__ == "__main__":
    mvrs.build("tiburon", "Tiburon", {})
