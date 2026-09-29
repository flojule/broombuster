"""Parity: frontend/js/urgency.js must match broombuster.analysis exactly.

Levels:
  - tables:    WEEKDAY_CODES / NO_SWEEP_CODES identical
  - expansion: dates_in_range(code, a, b) == JS datesInRange(code, a, b)
  - verdict:   compute_urgency(row, now)  == JS checkDaySweeping(entries, now)
  - display:   sweep_body / format_schedule_side / side_lines

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


# Curated codes covering every grammar branch.
_CURATED_CODES = [
    "ME", "TE", "WE", "THE", "FE",          # every-weekday
    "S", "SU", "M",                           # bare days
    "MWF", "TTH", "TTHS", "MF", "TF", "THF",  # compound
    "TFE", "MFE", "THFE", "MTHE", "TTHE",     # compound + every
    "M1", "T2", "W3", "TH4", "F13", "S24",    # ordinals
    "T135", "TH245", "W5",                    # 5th-week ordinals
    "W1357",                                  # invalid ordinal -> []
    "E",                                       # every day
    "NS", "N", "MS", "DM", "missing",          # no-sweep
    "ZZZ", "",                                 # garbage -> []
    "DATES:2026-09-29,bad,2026-10-01",         # invalid date skipped
]


def _today_now():
    t = datetime.date.today()
    return {"y": t.year, "m": t.month, "d": t.day, "min": 12 * 60}


def _real_codes():
    """Distinct real weekly DAY_* codes from the line-based cities."""
    codes = set()
    for city in ("oakland", "alameda", "san_francisco"):
        try:
            gdf = data_loader.load_city_data(city)
        except Exception:
            continue
        for col in ("DAY_EVEN", "DAY_ODD"):
            codes |= {v.strip() for v in gdf[col].dropna().unique()
                      if isinstance(v, str) and v.strip()}
    return sorted(codes)


def _ymd(d):
    return {"y": d.year, "m": d.month, "d": d.day}


def test_tables_parity():
    """JS WEEKDAY_CODES / NO_SWEEP_CODES must equal the Python tables."""
    got = _run_js([{"id": "t", "kind": "tables"}])["t"]
    assert got["weekdays"] == {k: list(v) for k, v in analysis.WEEKDAY_CODES.items()}
    assert set(got["noSweep"]) == set(analysis.NO_SWEEP_CODES)


def test_expansion_parity():
    # Two months spanning a month end and a 5th week.
    start = datetime.date(2026, 9, 1)
    end = datetime.date(2026, 10, 31)
    codes = _CURATED_CODES + _real_codes()
    cases = [{"id": f"e{i}", "kind": "expand", "code": c,
              "start": _ymd(start), "end": _ymd(end)}
             for i, c in enumerate(codes)]
    js = _run_js(cases)

    mismatches = []
    for i, code in enumerate(codes):
        py = [d.isoformat() for d in analysis.dates_in_range(code, start, end)]
        got = js[f"e{i}"]["dates"]
        if py != got:
            mismatches.append((code, py, got))
    assert not mismatches, "expansion parity failures:\n" + "\n".join(
        f"  {c}: py={p} js={g}" for c, p, g in mismatches[:20]
    )


def test_both_sides_parity():
    """JS formatBothSides must match analysis.side_lines."""
    ln = datetime.datetime(2026, 6, 7, 12, 0)
    now = {"y": 2026, "m": 6, "d": 7, "min": 720}
    mon = [("ME", "Every Mon", "8AM-10AM")]
    wed = [("WE", "Every Wed", "9AM-11AM")]
    scenarios = {
        "same": (mon, mon, "even", None),
        "diff_even": (mon, wed, "even", None),
        "diff_odd": (mon, wed, "odd", None),
        "unknown_side": (mon, wed, None, None),
        "labels": (mon, wed, "odd", ["North", "South"]),
        "one_side": (mon, [], "odd", None),
        "empty": ([], [], None, None),
    }
    cases, expected = [], {}
    for cid, (ev, od, side, labels) in scenarios.items():
        expected[cid] = analysis.side_lines(
            ev, od, side, tuple(labels) if labels else analysis.DEFAULT_SIDE_LABELS, ln)
        cases.append({"id": cid, "kind": "both", "now": now, "carSide": side, "labels": labels,
                      "even": [list(e) for e in ev], "odd": [list(e) for e in od]})
    js = _run_js(cases)
    mismatches = [(c, expected[c], js[c]["lines"]) for c in expected
                  if expected[c] != js[c]["lines"]]
    assert not mismatches, "side_lines parity failures:\n" + "\n".join(
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
        # Alameda PDF artifacts: stray spaces / bullet / mid-dot separators must
        # normalize identically in Python and JS (else tiles leak raw times).
        ("Every Mon (every)", "8:00 AM -11 :00 AM"),
        ("Every Mon (every)", "8:00 AM • 11: 00 AM"),
        ("Every Mon (every)", "1 0:00 AM -1:00 PM"),
        # "(biweekly)" is display noise that must be dropped (de-dupes lines).
        ("Every Wed (biweekly)", "8AM–11AM"),
        # Bare ordinal runs -> friendly ordinals.
        ("Mon 135", "8AM-10AM"),
        ("Thu 13", "8AM-10AM"),
        ("Tue 24", ""),
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
    # Messy real-world side: duplicate weekday rows with differently-formatted
    # raw times (PDF artifacts) plus a "(biweekly)" twin must collapse to the
    # same clean lines in Python and JS.
    messy_even = [
        ("M", "Every Mon (every)", "8:00 AM -11 :00 AM"),
        ("M", "Every Mon (every)", "8AM–11AM"),
        ("M", "Every Mon (every)", "8:00 AM • 11: 00 AM"),
        ("T", "Every Tue (biweekly)", "6AM–9AM"),
        ("T", "Every Tue (every)", "6AM–9AM"),
    ]
    # All seven weekdays at one time -> "Every day, …".
    everyday_even = [
        (c, f"Every {d} (every)", "7AM–8AM") for c, d in
        [("M", "Mon"), ("T", "Tue"), ("W", "Wed"), ("TH", "Thu"),
         ("F", "Fri"), ("S", "Sat"), ("SU", "Sun")]
    ]
    sides = {"sf": sf_even, "berkeley": berkeley_even, "oakland": oakland_even,
             "messy": messy_even, "everyday": everyday_even, "empty": []}

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


def test_verdict_parity_weekly_codes_month_end():
    """Weekly codes on the last day of a month (tomorrow is next month)."""
    day = datetime.date(2026, 9, 30)  # Wed; Oct 1 is Thu
    scenarios = [
        ("wed_open", "WE", "8AM-10AM", 9),
        ("wed_closed", "WE", "8AM-10AM", 11),
        ("thu_tomorrow", "THE", "8AM-10AM", 9),
        ("fifth_wed", "W135", "", 9),
        ("first_thu_next_month", "TH1", "", 9),
        ("no_sweep_ms", "MS", "", 9),
    ]
    cases, expected = [], {}
    for label, code, t, hh in scenarios:
        row = _row(code, t, "", "")
        expected[label] = analysis.compute_urgency(
            row, local_now=datetime.datetime(day.year, day.month, day.day, hh))
        cases.append({"id": label, "kind": "verdict", "now": _now(day, hh),
                      "sched": _sched([{"code": code, "time": t, "side": "even"}])})
    js = _run_js(cases)
    norm = {False: "clear", "today": "today", "tomorrow": "tomorrow"}
    got = {k: js[k]["urgency"] for k in expected}
    assert {k: norm[v] for k, v in expected.items()} == got
    assert got == {"wed_open": "today", "wed_closed": "clear",
                   "thu_tomorrow": "tomorrow", "fifth_wed": "today",
                   "first_thu_next_month": "tomorrow", "no_sweep_ms": "clear"}
