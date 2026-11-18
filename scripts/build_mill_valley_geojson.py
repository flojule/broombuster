#!/usr/bin/env python3
"""
Build data/mill_valley/StreetSweeping.geojson from Mill Valley Refuse Service's sweeping
lists (scripts/mvrs.py) and the county's road centerlines (fetched by
scripts/rebuild_city_data.py mill_valley).

Usage (from repo root):  python scripts/build_mill_valley_geojson.py
Output: data/mill_valley/StreetSweeping.geojson
"""

import mvrs

# "Downtown area and main arteries of Mill Valley (Miller Ave., Camino Alto and E. Blithedale
# Ave.) are swept every Monday and Friday."
EVERY = dict.fromkeys(("Miller Ave", "Camino Alto", "E Blithedale Ave"), "Every Monday and Friday")

if __name__ == "__main__":
    mvrs.build("mill_valley", "Mill Valley", EVERY)
