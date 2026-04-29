"""
Central configuration — all credentials are loaded from environment variables.

Copy .env.example to .env and fill in your values, or export them in your shell:

    export EMAIL_SENDER=you@example.com
    export EMAIL_RECEIVER=you@example.com
    export EMAIL_PASSWORD=app_password
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on real environment variables

# Email notification (Gmail App Password recommended)
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER",   "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
