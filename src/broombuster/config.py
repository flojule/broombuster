"""Paths and environment settings, in one place.

Settings are read on each call (not cached at import) so tests and the service
environment can change them. Variables:

  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, BB_BASE_URL  Google sign-in (all or none)
  BB_DB                                                SQLite user database path
  BB_RATE_LIMIT                                        "0" turns rate limits off
  BB_CSP                                               "enforce" enforces the CSP (else report-only)
"""

import os

# <repo>/src/broombuster/config.py -> <repo>
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OAUTH_ENV_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "BB_BASE_URL")


def oauth_env() -> tuple[str, str, str]:
    """(client_id, client_secret, base_url) as set, stripped; "" when unset."""
    cid, secret, base = (os.environ.get(k, "").strip() for k in OAUTH_ENV_KEYS)
    return cid, secret, base


def db_path() -> str:
    return os.environ.get("BB_DB") or os.path.join(REPO_ROOT, "data", "users.sqlite")


def rate_limit_enabled() -> bool:
    return os.environ.get("BB_RATE_LIMIT", "1") != "0"


def csp_enforced() -> bool:
    return os.environ.get("BB_CSP", "report-only") == "enforce"
