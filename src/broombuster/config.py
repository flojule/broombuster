"""Paths and environment settings, in one place.

Settings are read on each call (not cached at import) so tests and the service
environment can change them. Variables:

  BB_BASE_URL                                          public site URL; turns passkey sign-in on
  BB_DB                                                SQLite user database path
  BB_VAPID_KEY                                         Web Push signing key (PEM; made on first
                                                       use; default: next to BB_DB)
  BB_RATE_LIMIT                                        "0" turns rate limits off
  BB_LIVE_FEEDS                                        "0" turns the closures refresh off (tests)
  BB_CSP                                               "report-only" relaxes the enforced CSP
  BB_NREL_API_KEY                                      EV stations (layer build; DEMO_KEY if unset)
  BB_511_API_KEY                                       511 SF Bay work zones (off if unset)
  BB_CACHE_DIR                                         startup cache of the loaded cities
                                                       (default: systemd's STATE_DIRECTORY;
                                                       none when neither is set)
"""

import os

# <repo>/src/broombuster/config.py -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def base_url() -> str:
    """Public site URL, no trailing slash ("" if unset); link previews need absolute URLs."""
    return os.environ.get("BB_BASE_URL", "").strip().rstrip("/")


def db_path() -> str:
    return os.environ.get("BB_DB") or os.path.join(REPO_ROOT, "data", "users.sqlite")


def vapid_key_path() -> str:
    return os.environ.get("BB_VAPID_KEY") or os.path.join(os.path.dirname(db_path()), "vapid.pem")


def rate_limit_enabled() -> bool:
    return os.environ.get("BB_RATE_LIMIT", "1") != "0"


def live_feeds_enabled() -> bool:
    return os.environ.get("BB_LIVE_FEEDS", "1") != "0"


def csp_enforced() -> bool:
    return os.environ.get("BB_CSP", "enforce") != "report-only"


def nrel_api_key() -> str:
    """developer.nlr.gov key for the EV layer build; DEMO_KEY works for a few calls an hour."""
    return os.environ.get("BB_NREL_API_KEY", "").strip() or "DEMO_KEY"


def api_511_key() -> str:
    """511.org open-data token ("" when unset: the work-zone feed is off)."""
    return os.environ.get("BB_511_API_KEY", "").strip()


def cache_dir() -> str | None:
    """Where the server keeps derived files between restarts (None: no cache)."""
    return os.environ.get("BB_CACHE_DIR") or os.environ.get("STATE_DIRECTORY") or None
