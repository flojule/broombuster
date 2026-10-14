"""Approximate position of an IP address via ipapi.co (cached, best effort)."""

import json
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=512)
def lookup(ip: str) -> tuple[float, float] | None:
    """(lat, lon) for a public IP, or None on any failure. `ip` must be validated."""
    try:
        req = urllib.request.Request(f"https://ipapi.co/{ip}/json/",
                                     headers={"User-Agent": "broombuster"})
        with urllib.request.urlopen(req, timeout=3.0) as r:
            d = json.load(r)
        return (float(d["latitude"]), float(d["longitude"]))
    except Exception:
        return None
