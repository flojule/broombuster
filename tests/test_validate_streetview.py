"""scripts/validate_streetview.py: how a side's signs score against the app's schedule."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import validate_streetview as vs

_META = {"panos": [{"n": 1, "date": (2025, 3)}]}
_NO_SIGN = {"views": [{"view": "L", "pano": 1, "coverage": "good", "signs": []}]}


@pytest.mark.parametrize(
    "entries,verdict",
    [
        ([], "match"),  # no sweeping, no sign
        ([["NT", "1st Tue, no tickets", ""]], "match"),  # unsigned courtesy sweep
        ([["ME", "Every Mon", "8AM-10AM"]], "absent"),
        ([["POSTED", "Day as posted on the signs", "9AM-12PM"]], "absent"),
        ([["POSTED", "Day as posted on the signs", ""]], "absent"),
    ],
)
def test_a_side_without_a_sign(entries, verdict):
    rec = {"target": "L", "expected": {"L": {"sweeping": entries}}}
    assert vs._verdict_sweeping(rec, _META, _NO_SIGN, (2024, 1))[0] == verdict
