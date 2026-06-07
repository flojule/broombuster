"""Parity: frontend/js/urgency.js must match broombuster.analysis exactly.

Two levels:
  - expansion: parse_sweeping_code(code) == JS parseSweepingCode(code, today)
  - verdict:   compute_urgency(row, now)  == JS urgencyForSched(sched, now)

The JS runs under node (see _urgency_harness.js). Skipped if node is absent.
"""
import datetime
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from broombuster import analysis, data_loader, normalize

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = Path(__file__).parent / "_urgency_harness.js"

if shutil.which("node") is None:
    pytest.skip("node not available", allow_module_level=True)


def _run_js(cases):
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(cases, tf)
        path = tf.name
    try:
        res = subprocess.run(
            ["node", str(_HARNESS), path],
            capture_output=True, text=True, check=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    return {r["id"]: r for r in json.loads(res.stdout)}


# Curated codes covering every parse_sweeping_code branch.
_CURATED_CODES = [
    "ME", "TE", "WE", "THE", "FE",          # every-weekday
    "S", "SU",                                # bare weekend
    "MWF", "TTH", "TTHS", "MF", "TF", "THF",  # compound
    "TFE", "MFE", "THFE",                      # compound + every
    "M1", "T2", "W3", "TH4", "F13", "S24",    # ordinals
    "T135", "W1357",                          # unknown ordinal tails -> []
    "E",                                       # every day
    "NS", "N",                                # no-sweep
    "ZZZ",                                     # garbage -> []
]


def _today_now():
    t = datetime.date.today()
    return {"y": t.year, "m": t.month, "d": t.day, "min": 12 * 60}


def _oakland_codes():
    """Distinct real DAY_* codes from a fast-loading bundled city."""
    try:
        gdf = data_loader.load_city_data("oakland")
    except Exception:
        return []
    codes = set()
    for col in ("DAY_EVEN", "DAY_ODD"):
        if col in gdf.columns:
            for v in gdf[col].dropna().unique():
                if isinstance(v, str) and v.strip():
                    codes.add(v.strip())
    return sorted(codes)


def test_expansion_parity():
    now = _today_now()
    codes = _CURATED_CODES + _oakland_codes()
    cases = [{"id": f"e{i}", "kind": "expand", "code": c, "now": now}
             for i, c in enumerate(codes)]
    js = _run_js(cases)

    mismatches = []
    for i, code in enumerate(codes):
        py = sorted(d.isoformat() for d in analysis.parse_sweeping_code(code))
        got = js[f"e{i}"]["dates"]
        if py != got:
            mismatches.append((code, py, got))
    assert not mismatches, "expansion parity failures:\n" + "\n".join(
        f"  {c}: py={p} js={g}" for c, p, g in mismatches[:20]
    )


def test_next_dates_desc_parity():
    """JS nextDatesDesc must match analysis.next_dates_desc (Chicago hover/card)."""
    code = ("DATES:2026-06-17,2026-06-18,2026-07-25,2026-07-28,"
            "2026-09-02,2026-09-03")
    nows = [
        datetime.date(2026, 6, 1),
        datetime.date(2026, 6, 18),
        datetime.date(2026, 7, 26),
        datetime.date(2026, 12, 31),  # none remain -> ""
    ]
    cases, expected = [], {}
    for i, d in enumerate(nows):
        cid = f"nd{i}"
        ln = datetime.datetime(d.year, d.month, d.day, 12, 0)
        expected[cid] = analysis.next_dates_desc(code, local_now=ln)
        cases.append({"id": cid, "kind": "nextdates", "code": code,
                      "now": {"y": d.year, "m": d.month, "d": d.day, "min": 720}})
    # Non-DATES code -> None / null.
    expected["nd_non"] = analysis.next_dates_desc("MWF", local_now=None)
    cases.append({"id": "nd_non", "kind": "nextdates", "code": "MWF",
                  "now": _today_now()})

    js = _run_js(cases)
    mismatches = []
    for cid, exp in expected.items():
        got = js[cid]["out"]
        if exp != got:
            mismatches.append((cid, exp, got))
    assert not mismatches, "next_dates_desc parity failures:\n" + "\n".join(
        f"  {c}: py={p!r} js={g!r}" for c, p, g in mismatches
    )


def test_sweep_body_parity():
    """JS sweepBody must match normalize.sweep_body for every shape of desc."""
    cases_in = [
        ("1st and 3rd Wed", "9:00AM-12:00PM"),
        ("2nd and 4th Tues", "9:00AM-12:00PM"),
        ("Mon 1st & 3rd of month, 12PM–2PM", "12PM–2PM"),
        ("Every Wed (every), 6AM–8AM", "6AM–8AM"),
        ("Thurs", "8AM-10AM"),
        ("No Signage", ""),
        ("", ""),
        ("1st Sun", "7:30AM-9:30AM"),
    ]
    cases, expected = [], {}
    for i, (d, t) in enumerate(cases_in):
        cid = f"b{i}"
        expected[cid] = normalize.sweep_body(d, t)
        cases.append({"id": cid, "kind": "body", "desc": d, "time": t,
                      "now": _today_now()})
    js = _run_js(cases)
    mismatches = [(cid, expected[cid], js[cid]["out"])
                  for cid in expected if expected[cid] != js[cid]["out"]]
    assert not mismatches, "sweep_body parity failures:\n" + "\n".join(
        f"  {c}: py={p!r} js={g!r}" for c, p, g in mismatches
    )


def test_format_schedule_side_parity():
    """JS formatScheduleSide must match analysis.format_schedule_side."""
    ln = datetime.datetime(2026, 6, 7, 12, 0)
    now = {"y": 2026, "m": 6, "d": 7, "min": 720}

    sf_even = [
        ("TH13", "Thu 1st & 3rd of month, 12PM–2PM", "12PM–2PM"),
        ("M13", "Mon 1st & 3rd of month, 12PM–2PM", "12PM–2PM"),
        ("WE", "Every Wed (every), 6AM–8AM", "6AM–8AM"),
        ("M13", "Mon 1st & 3rd of month, 6AM–8AM", "6AM–8AM"),
        ("M24", "Mon 2nd & 4th of month, 6AM–8AM", "6AM–8AM"),
        ("F13", "Fri 1st & 3rd of month, 6AM–8AM", "6AM–8AM"),
        ("F24", "Fri 2nd & 4th of month, 6AM–8AM", "6AM–8AM"),
        ("T13", "Tue 1st & 3rd of month, 9AM–11AM", "9AM–11AM"),
    ]
    berkeley_even = [
        ("DATES:2026-04-01,2026-05-06,2026-06-03,2026-07-01", "", ""),
        ("DATES:2026-04-08,2026-05-13,2026-06-10,2026-07-08", "", ""),
        ("DATES:2026-04-14,2026-05-12,2026-06-09,2026-07-14", "", ""),
        ("DATES:2026-04-10,2026-05-08,2026-06-12,2026-07-10", "", ""),
    ]
    oakland_even = [("W13", "1st and 3rd Wed", "9:00AM-12:00PM")]
    sides = {"sf": sf_even, "berkeley": berkeley_even, "oakland": oakland_even, "empty": []}

    cases, expected = [], {}
    for cid, entries in sides.items():
        expected[cid] = analysis.format_schedule_side(entries, local_now=ln)
        cases.append({"id": cid, "kind": "side",
                      "entries": [{"code": c, "desc": d, "time": t} for c, d, t in entries],
                      "now": now})
    js = _run_js(cases)
    mismatches = [(cid, expected[cid], js[cid]["lines"])
                  for cid in expected if expected[cid] != js[cid]["lines"]]
    assert not mismatches, "format_schedule_side parity failures:\n" + "\n".join(
        f"  {c}: py={p!r} js={g!r}" for c, p, g in mismatches
    )


def _row(day_even="", time_even="", day_odd="", time_odd=""):
    return pd.Series({
        "DAY_EVEN": day_even, "DESC_EVEN": "", "TIME_EVEN": time_even,
        "DAY_ODD": day_odd, "DESC_ODD": "", "TIME_ODD": time_odd,
    })


def _now(d: datetime.date, hour: int, minute: int = 0):
    return {"y": d.year, "m": d.month, "d": d.day, "min": hour * 60 + minute}


def _sched(entries):
    return json.dumps(entries, separators=(",", ":"))


def test_verdict_parity_dates_codes():
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    far = today + datetime.timedelta(days=30)
    di = datetime.date.isoformat

    scenarios = [
        # (label, day_even, time_even, day_odd, time_odd, hour, minute)
        ("today_untimed", f"DATES:{di(today)}", "", "", "", 9, 0),
        ("today_window_open", f"DATES:{di(today)}", "8AM-10AM", "", "", 9, 0),
        ("today_window_closed", f"DATES:{di(today)}", "8AM-10AM", "", "", 11, 0),
        ("today_closed_other_untimed",
         f"DATES:{di(today)}", "8AM-10AM", f"DATES:{di(today)}", "", 11, 0),
        ("tomorrow_only", f"DATES:{di(tomorrow)}", "8AM-10AM", "", "", 9, 0),
        ("today_and_tomorrow_closed",
         f"DATES:{di(today)},{di(tomorrow)}", "8AM-10AM", "", "", 11, 0),
        ("none", f"DATES:{di(far)}", "8AM-10AM", "", "", 9, 0),
        ("empty", "", "", "", "", 9, 0),
    ]

    cases = []
    expected = {}
    for label, de, te, do, to, hh, mm in scenarios:
        row = _row(de, te, do, to)
        local_now = datetime.datetime(today.year, today.month, today.day, hh, mm)
        expected[label] = analysis.compute_urgency(row, local_now=local_now)
        entries = []
        if de:
            entries.append({"code": de, "time": te, "side": "even"})
        if do:
            entries.append({"code": do, "time": to, "side": "odd"})
        cases.append({"id": label, "kind": "verdict",
                      "sched": _sched(entries), "now": _now(today, hh, mm)})

    js = _run_js(cases)
    # Python uses False for "no urgency"; JS uses 'clear'.
    norm = {False: "clear", "today": "today", "tomorrow": "tomorrow"}
    mismatches = []
    for label in expected:
        py = norm[expected[label]]
        got = js[label]["urgency"]
        if py != got:
            mismatches.append((label, py, got))
    assert not mismatches, "verdict parity failures:\n" + "\n".join(
        f"  {label}: py={py} js={got}" for label, py, got in mismatches
    )
