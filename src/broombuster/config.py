"""Paths and environment settings, in one place.

Settings are read on each call (not cached at import) so tests and the service
environment can change them. Variables:

  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, BB_BASE_URL  Google sign-in (all or none)
  BB_DB                                                SQLite user database path
  BB_RATE_LIMIT                                        "0" turns rate limits off
  BB_CSP                                               "enforce" enforces the CSP (else report-only)
  BB_NREL_API_KEY                                      EV stations (layer build; DEMO_KEY if unset)
  BB_511_API_KEY                                       511 SF Bay work zones (off if unset)
"""

import os

# <repo>/src/broombuster/config.py -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OAUTH_ENV_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "BB_BASE_URL")


def oauth_env() -> tuple[str, str, str]:
    """(client_id, client_secret, base_url) as set, stripped; "" when unset."""
    cid, secret, base = (os.environ.get(k, "").strip() for k in OAUTH_ENV_KEYS)
    return cid, secret, base


def base_url() -> str:
    """Public site URL, no trailing slash ("" if unset); link previews need absolute URLs."""
    return os.environ.get("BB_BASE_URL", "").strip().rstrip("/")


def db_path() -> str:
    return os.environ.get("BB_DB") or os.path.join(REPO_ROOT, "data", "users.sqlite")


def rate_limit_enabled() -> bool:
    return os.environ.get("BB_RATE_LIMIT", "1") != "0"


def csp_enforced() -> bool:
    return os.environ.get("BB_CSP", "report-only") == "enforce"


def nrel_api_key() -> str:
    """developer.nlr.gov key for the EV layer build; DEMO_KEY works for a few calls an hour."""
    return os.environ.get("BB_NREL_API_KEY", "").strip() or "DEMO_KEY"


def api_511_key() -> str:
    """511.org open-data token ("" when unset: the work-zone feed is off)."""
    return os.environ.get("BB_511_API_KEY", "").strip()
