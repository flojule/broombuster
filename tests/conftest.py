import os

# PMTILES mode is the app default, but most tests exercise the legacy
# server-built GeoJSON /check path (they read response["geojson"]). Force it off
# here — set before broombuster.api.app is imported, which reads the flag at
# import time. Tests that need PMTILES mode set the env explicitly themselves.
os.environ.setdefault("PMTILES_MODE", "0")
os.environ.setdefault("DEV_MODE", "1")
