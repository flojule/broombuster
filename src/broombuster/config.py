"""
Central configuration — all credentials are loaded from environment variables.

Copy .env.example to .env and fill in your values, or export them in your shell:

    export EMAIL_SENDER=you@example.com
    export EMAIL_RECEIVER=you@example.com
    export EMAIL_PASSWORD=app_password
"""

import os

from dotenv import load_dotenv

load_dotenv()


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var ("1", "true", "yes" are truthy); unset -> default."""
    return os.environ.get(name, "1" if default else "").lower() in ("1", "true", "yes")


DEV_MODE           = env_flag("DEV_MODE")            # skip auth; single shared local user
PMTILES_MODE       = env_flag("PMTILES_MODE", True)  # static vector tiles vs legacy GeoJSON
ALLOW_REGISTRATION = env_flag("ALLOW_REGISTRATION", True)

# Email notification (Gmail App Password recommended)
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER",   "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
