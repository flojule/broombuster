"""frontend/js/urgency.js against recorded cases (tests/urgency_cases.json).

Each case is run through tests/_urgency_harness.js under node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_DIR = Path(__file__).parent

if shutil.which("node") is None:
    pytest.skip("node not available", allow_module_level=True)


def test_urgency_cases():
    cases = _DIR / "urgency_cases.json"
    golden = json.loads(cases.read_text())
    out = subprocess.run(["node", str(_DIR / "_urgency_harness.js"), str(cases)],
                         capture_output=True, text=True, check=True).stdout
    got = {r.pop("id"): r for r in json.loads(out)}
    bad = [(g["case"], g["expect"], got[g["case"]["id"]])
           for g in golden if got[g["case"]["id"]] != g["expect"]]
    assert not bad, "\n".join(f"{c}\n  want {w}\n  got  {r}" for c, w, r in bad[:10])
