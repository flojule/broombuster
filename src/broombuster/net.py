"""Outbound HTTP for request-time lookups (feeds, trash, IP location, geocoding): one
pooled session, so repeated calls to a service reuse its TLS connection, and a cap on
calls in flight per host: past it a caller gets Busy at once instead of holding a
worker thread in a queue behind a slow service."""

import threading
import urllib.parse

import requests

USER_AGENT = "BroomBuster (https://github.com/flojule/broombuster)"
MAX_IN_FLIGHT = 4  # per host

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT

_slots: dict[str, threading.BoundedSemaphore] = {}
_lock = threading.Lock()


class Busy(Exception):
    """MAX_IN_FLIGHT calls to that host are running: local congestion, not an outage."""


def get_json(url: str, timeout: float, **params):
    """GET url -> parsed JSON, or None on any failure; raises Busy (nothing sent) when
    MAX_IN_FLIGHT calls to its host are in flight."""
    host = urllib.parse.urlsplit(url).hostname or ""
    with _lock:
        slot = _slots.setdefault(host, threading.BoundedSemaphore(MAX_IN_FLIGHT))
    if not slot.acquire(blocking=False):
        raise Busy(host)
    try:
        r = session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None
    finally:
        slot.release()
