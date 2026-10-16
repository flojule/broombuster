"""Approximate position of an IP address via ipapi.co (cached, best effort)."""

import json
import urllib.request
from functools import lru_cache


class _Failed(Exception):
    pass


@lru_cache(maxsize=512)
def _fetch(ip: str) -> tuple[float, float]:
    try:
        req = urllib.request.Request(f"https://ipapi.co/{ip}/json/",
                                     headers={"User-Agent": "broombuster"})
        with urllib.request.urlopen(req, timeout=3.0) as r:
            d = json.load(r)
        return (float(d["latitude"]), float(d["longitude"]))
    except Exception as e:
        raise _Failed from e  # lru_cache does not cache exceptions: retried next time


def lookup(ip: str) -> tuple[float, float] | None:
    """(lat, lon) for a public IP, or None on any failure. `ip` must be validated."""
    try:
        return _fetch(ip)
    except _Failed:
        return None
