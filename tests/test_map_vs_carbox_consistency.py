"""
tests/test_map_vs_carbox_consistency.py

Catches the specific bug: "map street highlight says one thing, car box says another."

There are two independent urgency-computation code paths for the same GDF row:

    maps._sweeping_color(row, local_now)     → "tomato" | "orange" | "cornflowerblue"
    analysis.compute_urgency(row, local_now) → "today"  | "tomorrow" | False

For the same row these MUST agree:

    "tomato"         ↔  "today"
    "orange"         ↔  "tomorrow"
    "cornflowerblue" ↔  False

If they diverge for a production segment the map paints the street one colour while
the car box shows a different urgency — exactly the inconsistency the user is seeing.

Tests are organised as:

  A. Unit — construct synthetic rows covering every branch in both functions and
     assert that _sweeping_color and compute_urgency agree.

  B. Exhaustive — run both functions over every segment in the Bay Area GDF and
     collect ALL mismatches in one assertion (so one test run reveals all bugs).

  C. API round-trip — call /check, identify the GeoJSON feature that corresponds
     to the resolved segment (the one driving the car box), and verify the
     feature's urgency colour matches the car-box urgency field.
"""

import os
os.environ.setdefault("DEV_MODE", "1")

import datetime
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from broombuster import analysis
from broombuster import maps
from broombuster import normalize

_TZ = ZoneInfo("America/Los_Angeles")

_NOW_MORNING = datetime.datetime(2026, 4, 18,  9, 0, tzinfo=_TZ)  # 09:00
_NOW_AFTER   = datetime.datetime(2026, 4, 18, 11, 0, tzinfo=_TZ)  # 11:00 (past 8–10 window)
_TODAY       = _NOW_MORNING.date()
_TOMORROW    = _TODAY + datetime.timedelta(days=1)
_FUTURE      = _TODAY + datetime.timedelta(days=2)


def _dates(d: datetime.date) -> str:
    return f"DATES:{d.isoformat()}"


def _seg(**kw) -> pd.Series:
    defaults = {
        "STREET_NAME":    "TEST ST",
        "STREET_DISPLAY": "Test St",
        "DAY_EVEN":  None, "TIME_EVEN": "", "DESC_EVEN": "",
        "DAY_ODD":   None, "TIME_ODD":  "", "DESC_ODD":  "",
        "L_F_ADD": None, "L_T_ADD": None,
        "R_F_ADD": None, "R_T_ADD": None,
    }
    defaults.update(kw)
    return pd.Series(defaults)


# Canonical mapping
_COLOR_TO_URGENCY = {
    "tomato":         "today",
    "orange":         "tomorrow",
    "cornflowerblue": False,
}


def _check_agree(row, local_now, *, label=""):
    """Assert that _sweeping_color and compute_urgency agree for this row/time."""
    color   = maps._sweeping_color(row, local_now=local_now)
    urgency = analysis.compute_urgency(row, local_now=local_now)
    expected = _COLOR_TO_URGENCY[color]
    assert urgency == expected, (
        f"{label}Map color={color!r} (→ {expected!r}) but "
        f"compute_urgency={urgency!r}\n"
        f"  DAY_EVEN={row.get('DAY_EVEN')!r} TIME_EVEN={row.get('TIME_EVEN')!r}\n"
        f"  DAY_ODD={row.get('DAY_ODD')!r}  TIME_ODD={row.get('TIME_ODD')!r}\n"
        f"  local_now={local_now}"
    )


# ---------------------------------------------------------------------------
# A. Unit — synthetic rows, every branch
# ---------------------------------------------------------------------------

class TestSyntheticRowConsistency:
    """
    Construct minimal rows that exercise every branch of both code paths and
    verify they always agree.
    """

    # ── No schedule ──────────────────────────────────────────────────────────

    def test_no_schedule_both_blue(self):
        _check_agree(_seg(), _NOW_MORNING, label="no schedule: ")

    def test_null_day_even_no_schedule(self):
        _check_agree(_seg(DAY_EVEN=None, DAY_ODD=None), _NOW_MORNING)

    def test_empty_string_day_even(self):
        _check_agree(_seg(DAY_EVEN="", DAY_ODD=""), _NOW_MORNING)

    # ── N / NS / O no-sweep markers ──────────────────────────────────────────

    def test_n_code_even(self):
        # "N" is the explicit "no sweeping" marker
        _check_agree(_seg(DAY_EVEN="N"), _NOW_MORNING, label="N marker: ")

    def test_ns_code_odd(self):
        _check_agree(_seg(DAY_ODD="NS"), _NOW_MORNING, label="NS marker: ")

    # ── Today on even side only ───────────────────────────────────────────────

    def test_even_today_no_time(self):
        _check_agree(_seg(DAY_EVEN=_dates(_TODAY)), _NOW_MORNING, label="even today no time: ")

    def test_even_today_within_window(self):
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM"),
            _NOW_MORNING, label="even today within window: "
        )

    def test_even_today_past_window(self):
        # 11:00 > 10:00 → window closed → should both say "not today"
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM"),
            _NOW_AFTER, label="even today past window: "
        )

    def test_even_today_at_exact_end_of_window(self):
        # local_now == end_t (10:00 exactly) → still active per analysis
        now_exact = datetime.datetime(2026, 4, 18, 10, 0, tzinfo=_TZ)
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM"),
            now_exact, label="even today at exact end: "
        )

    # ── Today on odd side only ────────────────────────────────────────────────

    def test_odd_today_within_window(self):
        _check_agree(
            _seg(DAY_ODD=_dates(_TODAY), TIME_ODD="8AM-10AM"),
            _NOW_MORNING, label="odd today within window: "
        )

    def test_odd_today_past_window(self):
        _check_agree(
            _seg(DAY_ODD=_dates(_TODAY), TIME_ODD="8AM-10AM"),
            _NOW_AFTER, label="odd today past window: "
        )

    # ── Even done, odd still active ───────────────────────────────────────────

    def test_even_done_odd_active_is_today(self):
        # Even window 8–10 closed at 11:00; odd window 10–12 still open.
        # Both functions must see "today" (odd still active).
        _check_agree(
            _seg(
                DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                DAY_ODD=_dates(_TODAY),  TIME_ODD="10AM-12PM",
            ),
            _NOW_AFTER, label="even done odd active: "
        )

    def test_both_sides_done_falls_through(self):
        # Both windows 8–10 closed at 11:00 → no today → check tomorrow.
        # Odd sweeps tomorrow: should be "tomorrow".
        _check_agree(
            _seg(
                DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                DAY_ODD=_dates(_TOMORROW),
            ),
            _NOW_AFTER, label="even done odd tomorrow: "
        )

    # ── Tomorrow only ─────────────────────────────────────────────────────────

    def test_tomorrow_even(self):
        _check_agree(_seg(DAY_EVEN=_dates(_TOMORROW)), _NOW_MORNING, label="even tomorrow: ")

    def test_tomorrow_odd(self):
        _check_agree(_seg(DAY_ODD=_dates(_TOMORROW)), _NOW_MORNING, label="odd tomorrow: ")

    def test_tomorrow_both_sides(self):
        _check_agree(
            _seg(DAY_EVEN=_dates(_TOMORROW), DAY_ODD=_dates(_TOMORROW)),
            _NOW_MORNING, label="both tomorrow: "
        )

    # ── Future only (neither today nor tomorrow) ──────────────────────────────

    def test_future_only_even(self):
        _check_agree(_seg(DAY_EVEN=_dates(_FUTURE)), _NOW_MORNING, label="future even: ")

    def test_future_only_odd(self):
        _check_agree(_seg(DAY_ODD=_dates(_FUTURE)), _NOW_MORNING, label="future odd: ")

    # ── Mixed: today even, future odd ─────────────────────────────────────────

    def test_today_even_future_odd(self):
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                 DAY_ODD=_dates(_FUTURE)),
            _NOW_MORNING, label="today even future odd: "
        )

    # ── Missing TIME on one side ──────────────────────────────────────────────

    def test_today_even_no_time_odd_has_time_within_window(self):
        # Even: today, no time → assume still active
        # Odd: today, 8AM-10AM, still within window
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="",
                 DAY_ODD=_dates(_TODAY),  TIME_ODD="8AM-10AM"),
            _NOW_MORNING, label="even no-time odd within: "
        )

    def test_today_even_has_time_done_odd_no_time(self):
        # Even: today 8–10 done. Odd: today, no time → assume still active.
        # Both should say "today".
        _check_agree(
            _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                 DAY_ODD=_dates(_TODAY),  TIME_ODD=""),
            _NOW_AFTER, label="even done odd no-time: "
        )

    # ── NaN / float in day column ─────────────────────────────────────────────

    def test_nan_day_even(self):
        import math
        _check_agree(_seg(DAY_EVEN=float("nan")), _NOW_MORNING, label="nan day even: ")

    def test_float_day_even(self):
        # A float that is not a string — both should treat as no-sweep.
        _check_agree(_seg(DAY_EVEN=123.0), _NOW_MORNING, label="float day even: ")

    # ── Unknown sweep code ────────────────────────────────────────────────────

    def test_unknown_code_both_agree_on_no_sweep(self):
        _check_agree(_seg(DAY_EVEN="XYZZY"), _NOW_MORNING, label="unknown code: ")

    # ── Compound codes ────────────────────────────────────────────────────────

    def test_me_code_every_monday(self):
        # ME = every Monday; today is Friday → no sweep today or tomorrow.
        _check_agree(_seg(DAY_EVEN="ME"), _NOW_MORNING, label="ME code on Friday: ")

    def test_tth_code(self):
        _check_agree(_seg(DAY_EVEN="TTH"), _NOW_MORNING, label="TTH code: ")

    def test_mwf_code(self):
        _check_agree(_seg(DAY_EVEN="MWF"), _NOW_MORNING, label="MWF code: ")


# ---------------------------------------------------------------------------
# B. Exhaustive — every segment in the Bay Area GDF
# ---------------------------------------------------------------------------

class TestExhaustiveGdfConsistency:
    """
    Run both urgency functions over every segment in the production dataset
    and collect ALL mismatches.  One failing assertion names every divergent
    segment so bugs can be found and fixed without re-running tests.
    """

    @pytest.fixture(scope="class")
    def bay_area_gdf(self):
        from broombuster import data_loader
        return data_loader.load_region_data("bay_area")

    def _collect_mismatches(self, gdf, local_now):
        mismatches = []
        color_map = _COLOR_TO_URGENCY
        for idx, row in gdf.iterrows():
            color   = maps._sweeping_color(row, local_now=local_now)
            urgency = analysis.compute_urgency(row, local_now=local_now)
            expected = color_map[color]
            if urgency != expected:
                mismatches.append({
                    "idx":        idx,
                    "street":     row.get("STREET_DISPLAY") or row.get("STREET_NAME"),
                    "DAY_EVEN":   row.get("DAY_EVEN"),
                    "TIME_EVEN":  row.get("TIME_EVEN"),
                    "DAY_ODD":    row.get("DAY_ODD"),
                    "TIME_ODD":   row.get("TIME_ODD"),
                    "map_color":  color,
                    "map_urgency": expected,
                    "box_urgency": urgency,
                })
        return mismatches

    def test_morning_no_mismatches(self, bay_area_gdf):
        mismatches = self._collect_mismatches(bay_area_gdf, _NOW_MORNING)
        if mismatches:
            lines = [
                f"  [{m['idx']}] {m['street']!r}: "
                f"DAY_EVEN={m['DAY_EVEN']!r} TIME_EVEN={m['TIME_EVEN']!r} | "
                f"DAY_ODD={m['DAY_ODD']!r} TIME_ODD={m['TIME_ODD']!r} → "
                f"map={m['map_urgency']!r} box={m['box_urgency']!r}"
                for m in mismatches[:20]  # cap to avoid wall of text
            ]
            suffix = f"  … and {len(mismatches) - 20} more" if len(mismatches) > 20 else ""
            pytest.fail(
                f"{len(mismatches)} segment(s) have map/car-box mismatch at 09:00:\n"
                + "\n".join(lines) + suffix
            )

    def test_midday_no_mismatches(self, bay_area_gdf):
        """Past-end-time check: 11:00 local — many morning windows will have closed."""
        mismatches = self._collect_mismatches(bay_area_gdf, _NOW_AFTER)
        if mismatches:
            lines = [
                f"  [{m['idx']}] {m['street']!r}: "
                f"map={m['map_urgency']!r} box={m['box_urgency']!r} | "
                f"DAY_EVEN={m['DAY_EVEN']!r} TIME_EVEN={m['TIME_EVEN']!r} | "
                f"DAY_ODD={m['DAY_ODD']!r} TIME_ODD={m['TIME_ODD']!r}"
                for m in mismatches[:20]
            ]
            suffix = f"  … and {len(mismatches) - 20} more" if len(mismatches) > 20 else ""
            pytest.fail(
                f"{len(mismatches)} segment(s) have map/car-box mismatch at 11:00:\n"
                + "\n".join(lines) + suffix
            )

    @pytest.mark.skipif(
        not os.path.exists(
            os.path.join(os.path.dirname(__file__), "..", "src", "data", "chicago.fgb")
        ),
        reason="Chicago data not built locally",
    )
    def test_chicago_no_mismatches(self):
        from broombuster import data_loader
        gdf = data_loader.load_region_data("chicago")
        mismatches = self._collect_mismatches(gdf, _NOW_MORNING)
        if mismatches:
            lines = [
                f"  [{m['idx']}] {m['street']!r}: map={m['map_urgency']!r} box={m['box_urgency']!r}"
                for m in mismatches[:20]
            ]
            pytest.fail(f"{len(mismatches)} Chicago segment(s) mismatch:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# C. API round-trip — resolved segment must match its GeoJSON feature colour
# ---------------------------------------------------------------------------

class TestApiRoundTripConsistency:
    """
    Call /check, find the GeoJSON feature that corresponds to the resolved
    segment (identified via snap.street_name), and verify the feature's
    urgency colour is consistent with the car-box urgency field.

    This is the end-to-end version of the bug:
        map colours segment X as "tomato" (today)
        car box shows urgency False (all clear)
    """

    # Known-good Oakland coords used across tests
    COORDS = [
        (37.821326, -122.280705),   # Chestnut St, Oakland
        (37.804000, -122.270000),   # E 12th St area, Oakland
        (37.774929, -122.419416),   # Market St, SF
        (37.754370, -122.417480),   # Valencia St, SF
    ]

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from broombuster.api import app as api_mod
        with TestClient(api_mod.app) as c:
            yield c

    def _urgency_to_color(self, urgency):
        if urgency == "today":
            return "tomato"
        if urgency == "tomorrow":
            return "orange"
        return "cornflowerblue"

    def _find_feature_for_snap(self, features, snap_street_name):
        """
        Return the GeoJSON feature whose hover_html most closely matches
        snap_street_name (normalised comparison).
        """
        snap_key = normalize.street_name(snap_street_name)
        best = None
        for f in features:
            props = f.get("properties", {})
            hover = props.get("hover_html", "")
            # hover_html starts with <b>DISPLAY NAME</b>
            import re
            m = re.search(r"<b>(.*?)</b>", hover)
            if m:
                feat_key = normalize.street_name(m.group(1))
                if feat_key == snap_key:
                    best = f
                    break
        return best

    @pytest.mark.parametrize("lat,lon", COORDS)
    def test_resolved_segment_color_matches_carbox_urgency(self, client, lat, lon):
        resp = client.post("/check", json={"lat": lat, "lon": lon, "region": "bay_area"})
        assert resp.status_code == 200, resp.text
        data = resp.json()

        snap    = data.get("snap")
        urgency = data.get("urgency")
        geojson = data.get("geojson") or {}
        features = geojson.get("features", [])

        if snap is None:
            pytest.skip(f"No snap for ({lat}, {lon}) — resolver found no nearby segment")

        snap_name = snap.get("street_name", "")

        # The historical SF "per-weekday rows" inconsistency is now handled
        # by analysis.schedules_for_all_matching_rows — the resolver still
        # picks one row, but the API unions schedules across all rows that
        # describe the same physical segment, so the urgency a card sees
        # matches the color the map paints.

        feature = self._find_feature_for_snap(features, snap_name)

        if feature is None:
            # The resolved segment might not appear in the clipped GeoJSON when
            # it is right at the clip boundary — record as a warning rather than
            # a hard failure, but document the gap.
            pytest.xfail(
                f"Resolved segment '{snap_name}' not found in GeoJSON features for "
                f"({lat}, {lon}).  The clip may have excluded it — but if this "
                f"xfail disappears the resolver and map clip are out of sync."
            )

        feat_color   = feature["properties"].get("urgency", "cornflowerblue")
        expected_box = _COLOR_TO_URGENCY[feat_color]

        assert urgency == expected_box, (
            f"Map/car-box mismatch at ({lat}, {lon}) street='{snap_name}':\n"
            f"  GeoJSON feature urgency colour = {feat_color!r}  → expects car-box={expected_box!r}\n"
            f"  Actual car-box urgency         = {urgency!r}\n"
            f"  snap: {snap}"
        )

    def test_address_matches_snap_street_name(self, client):
        """
        The address field (shown in car box) must be derived from the same
        segment as snap.street_name.  If they differ the car box shows the
        wrong street name.
        """
        lat, lon = 37.821326, -122.280705
        resp = client.post("/check", json={"lat": lat, "lon": lon, "region": "bay_area"})
        assert resp.status_code == 200, resp.text
        data = resp.json()

        snap    = data.get("snap") or {}
        address = data.get("address", "")
        snap_name = snap.get("street_name", "")

        if not snap_name or not address:
            pytest.skip("snap or address not present")

        snap_key    = normalize.street_name(snap_name)
        address_key = normalize.street_name(address)

        assert snap_key in address_key or address_key in snap_key, (
            f"Car-box address '{address}' does not contain snap.street_name '{snap_name}'.\n"
            f"  Normalised snap key:    {snap_key!r}\n"
            f"  Normalised address key: {address_key!r}\n"
            f"The car box is displaying a street name different from the resolved segment."
        )

    def test_message_highlights_match_resolve_side(self, client):
        """
        The message ► must be on the side that matches car_side.
        The resolved segment drives both car_side and message — they must agree.
        """
        lat, lon = 37.821326, -122.280705
        resp = client.post("/check", json={"lat": lat, "lon": lon, "region": "bay_area"})
        assert resp.status_code == 200, resp.text
        data = resp.json()

        car_side = data.get("car_side")
        message  = data.get("message", "")

        if car_side is None:
            pytest.skip("car_side is None — resolver could not determine side")

        labeled = "Even" if car_side == "even" else "Odd"
        lines   = message.splitlines()

        # Single-street case: "► Street: …" — no per-side label; skip check.
        if message.startswith("► Street:"):
            return

        highlighted = [l for l in lines if l.startswith("►")]
        assert len(highlighted) >= 1, f"No ► in message: {message!r}"
        assert any(labeled in l for l in highlighted), (
            f"car_side={car_side!r} but ► not on '{labeled}' line.\n"
            f"message:\n{message}"
        )

    def test_no_schedule_gives_cornflowerblue_and_false(self, client):
        """
        A point with no street data should give urgency=False and the
        matching GeoJSON features should all be cornflowerblue.
        """
        # US geographic center — well outside any loaded city
        lat, lon = 39.50, -98.35
        resp = client.post("/check", json={"lat": lat, "lon": lon})
        assert resp.status_code in (200, 503), resp.text

        if resp.status_code == 503:
            pytest.skip("No region data for this coord — expected")

        data = resp.json()
        urgency = data.get("urgency")
        features = (data.get("geojson") or {}).get("features", [])

        if data.get("snap") is not None:
            pytest.skip("Unexpectedly resolved a segment for a remote coord")

        # snap is None → urgency should be False
        assert urgency in (False, None), (
            f"Expected no urgency for off-map point; got {urgency!r}"
        )
        for f in features:
            color = f["properties"].get("urgency", "cornflowerblue")
            assert color == "cornflowerblue", (
                f"Off-map feature should be cornflowerblue; got {color!r}"
            )
