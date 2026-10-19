"""Release version from git history: YYYY.MM.DD, plus .N from the second release of a day.

A release is a first-parent commit on the checked-out branch (a merged PR on main);
the day is its UTC commit date. Without git history (tests, a source export) the
version is "dev".
"""

import os
import subprocess
from functools import lru_cache

from broombuster import config


@lru_cache(maxsize=1)
def app_version() -> str:
    try:
        out = subprocess.run(
            ["git", "log", "--first-parent", "--format=%cd", "--date=format-local:%Y.%m.%d"],  # noqa: S607
            cwd=config.REPO_ROOT,
            env={**os.environ, "TZ": "UTC"},  # noqa: TID251 (passed to git, not a setting)
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return "dev"
    if not out:
        return "dev"
    day, n = out[0], out.count(out[0])
    return day if n == 1 else f"{day}.{n}"
