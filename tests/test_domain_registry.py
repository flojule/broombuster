"""
Tests for the domain plugin registry and the SweepingPlugin (Step 3).

Two layers of coverage:
  1. Plugin-shape unit tests — does SweepingPlugin satisfy the
     DomainPlugin Protocol, do its outputs match the contract, does
     `for_city` filter correctly?
  2. Behavior-equivalence — for the same fixtures used in
     test_car_panel_consistency.py, the plugin's `format()` produces the
     same urgency/schedules/message as the inline analysis path. This
     guarantees Step 3 didn't drift behaviour.
"""

import os

os.environ.setdefault("DEV_MODE", "1")

import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from broombuster import analysis
from broombuster.domains import DomainPlugin, max_urgency
from broombuster.domains.registry import for_city, iter_plugins
from broombuster.domains.sweeping import (
    SweepingPlugin,
    compose_message,
)

# ---------------------------------------------------------------------------
# Shared synthetic-row fixtures (mirror of test_car_panel_consistency.py)
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("America/Los_Angeles")
_NOW_MORNING = datetime.datetime(2026, 4, 18, 9, 0, tzinfo=_TZ)
_TODAY    = _NOW_MORNING.date()
_TOMORROW = _TODAY + datetime.timedelta(days=1)
_AFTER    = _TODAY + datetime.timedelta(days=2)


def _dates(d: datetime.date) -> str:
    return f"DATES:{d.isoformat()}"


def _seg(**kw) -> pd.Series:
    defaults = {
        "STREET_NAME": "TEST ST",
        "STREET_DISPLAY": "Test St",
        "DAY_EVEN": None,
        "DAY_ODD": None,
        "DESC_EVEN": "",
        "DESC_ODD": "",
        "TIME_EVEN": "",
        "TIME_ODD": "",
        "L_F_ADD": None, "L_T_ADD": None,
        "R_F_ADD": None, "R_T_ADD": None,
    }
    defaults.update(kw)
    return pd.Series(defaults)


class _FakeResolved:
    """Mimics resolve.ResolvedCar enough for SweepingPlugin.format()."""
    def __init__(self, segment, side="even", street_name="TEST ST",
                 street_display="Test St", distance_m=1.0, is_polygon=False):
        self.segment = segment
        self.side = side
        self.street_name = street_name
        self.street_display = street_display
        self.distance_m = distance_m
        self.is_polygon = is_polygon


# ---------------------------------------------------------------------------
# A. Registry shape
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_iter_plugins_returns_at_least_sweeping(self):
        ids = [p.domain_id for p in iter_plugins()]
        assert "sweeping" in ids

    def test_for_city_filters_by_supports(self):
        # Bay Area + Chicago cities all run sweeping.
        for city_key in ("oakland", "san_francisco", "berkeley", "alameda",
                         "chicago_all"):
            ids = [p.domain_id for p in for_city(city_key)]
            assert "sweeping" in ids

    def test_for_city_unknown_city_returns_empty(self):
        ids = [p.domain_id for p in for_city("NOT_A_REAL_CITY")]
        assert ids == []

    def test_sweeping_plugin_satisfies_protocol(self):
        """SweepingPlugin matches the structural DomainPlugin Protocol."""
        plugin = SweepingPlugin()
        assert isinstance(plugin, DomainPlugin)
        assert plugin.domain_id == "sweeping"
        assert plugin.label == "Street sweeping"
        assert callable(plugin.supports_city)
        assert callable(plugin.resolve_for)
        assert callable(plugin.format)


# ---------------------------------------------------------------------------
# B. SweepingPlugin.format() output contract
# ---------------------------------------------------------------------------

class TestSweepingFormat:
    def setup_method(self):
        self.plugin = SweepingPlugin()

    def test_today_segment_yields_today_urgency(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        result = self.plugin.format(_FakeResolved(seg, side="even"), None, _NOW_MORNING)
        assert result.domain_id == "sweeping"
        assert result.label == "Street sweeping"
        assert result.urgency == "today"
        assert result.extras["car_side"] == "even"

    def test_tomorrow_segment_yields_tomorrow(self):
        seg = _seg(DAY_ODD=_dates(_TOMORROW))
        result = self.plugin.format(_FakeResolved(seg, side="odd"), None, _NOW_MORNING)
        assert result.urgency == "tomorrow"

    def test_safe_when_no_schedule(self):
        seg = _seg()
        result = self.plugin.format(_FakeResolved(seg, side="even"), None, _NOW_MORNING)
        # Legacy compute_urgency returns False; the plugin maps that to "safe"
        # so the DomainResult.urgency is always one of the three string values.
        assert result.urgency == "safe"

    def test_safe_when_schedule_is_in_future(self):
        seg = _seg(DAY_EVEN=_dates(_AFTER))
        result = self.plugin.format(_FakeResolved(seg, side="even"), None, _NOW_MORNING)
        assert result.urgency == "safe"

    def test_none_resolved_returns_safe_with_explanation(self):
        result = self.plugin.format(None, None, _NOW_MORNING)
        assert result.urgency == "safe"
        assert result.schedule_lines, "must include at least one explanatory line"
        assert result.extras["car_side"] is None

    def test_schedule_lines_match_compose_message_semantics(self):
        """When both sides are identical, _schedule_lines collapses to one
        bullet — matching compose_message's '► Street: …' single-line case."""
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM",
                   DAY_ODD="ME",  DESC_ODD="Mon", TIME_ODD="8AM-10AM")
        result = self.plugin.format(_FakeResolved(seg, side="even"), None, _NOW_MORNING)
        assert len(result.schedule_lines) == 1, result.schedule_lines

    def test_schedule_lines_two_when_sides_differ(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM",
                   DAY_ODD="WE",  DESC_ODD="Wed", TIME_ODD="9AM-11AM")
        result = self.plugin.format(_FakeResolved(seg, side="even"), None, _NOW_MORNING)
        assert len(result.schedule_lines) == 2

    def test_schedule_lines_car_side_first(self):
        seg = _seg(DAY_EVEN="ME", DESC_EVEN="Mon", TIME_EVEN="8AM-10AM",
                   DAY_ODD="WE",  DESC_ODD="Wed", TIME_ODD="9AM-11AM")
        # Car on the odd side — Odd line should come first.
        result = self.plugin.format(_FakeResolved(seg, side="odd"), None, _NOW_MORNING)
        assert result.schedule_lines[0].startswith("Odd:")
        assert result.schedule_lines[1].startswith("Even:")


# ---------------------------------------------------------------------------
# C. Behavior-equivalence with the inline analysis path
# ---------------------------------------------------------------------------

class TestPluginEquivalence:
    """For the same segment + clock, the plugin's outputs must match what
    the inline analysis path would have produced. This guarantees Step 3
    introduced no behaviour change for sweeping users."""

    def setup_method(self):
        self.plugin = SweepingPlugin()

    def _run(self, seg, side, now=_NOW_MORNING):
        """Return (plugin_urgency_legacy, plugin_message, inline_urgency, inline_message)."""
        resolved = _FakeResolved(seg, side=side)
        result = self.plugin.format(resolved, None, now)
        # Plugin uses "safe" string; legacy uses False — translate for
        # apples-to-apples comparison against inline.
        plugin_urgency_legacy = result.urgency if result.urgency in (
            "today", "tomorrow"
        ) else False
        plugin_message = result.extras["message"]

        # Inline path (what /check did before Step 3).
        se, so = analysis.schedules_for_segment(seg)
        inline_urgency = analysis.compute_urgency(seg, local_now=now)
        inline_message = compose_message(se, so, side)
        return plugin_urgency_legacy, plugin_message, inline_urgency, inline_message

    def test_today_equivalence(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM")
        pu, pm, iu, im = self._run(seg, "even")
        assert pu == iu
        assert pm == im

    def test_tomorrow_equivalence(self):
        seg = _seg(DAY_ODD=_dates(_TOMORROW))
        pu, pm, iu, im = self._run(seg, "odd")
        assert pu == iu
        assert pm == im

    def test_no_schedule_equivalence(self):
        seg = _seg()
        pu, pm, iu, im = self._run(seg, "even")
        assert pu == iu
        assert pm == im

    def test_both_sides_today_equivalence(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_TODAY),  TIME_ODD="8AM-10AM")
        pu, pm, iu, im = self._run(seg, "even")
        assert pu == iu
        assert pm == im

    def test_mixed_today_future_equivalence(self):
        seg = _seg(DAY_EVEN=_dates(_TODAY), TIME_EVEN="8AM-10AM",
                   DAY_ODD=_dates(_AFTER))
        pu, pm, iu, im = self._run(seg, "odd")
        assert pu == iu
        assert pm == im


# ---------------------------------------------------------------------------
# D. max_urgency helper
# ---------------------------------------------------------------------------

class TestMaxUrgency:
    def test_today_beats_tomorrow_beats_safe(self):
        assert max_urgency("safe", "tomorrow", "today") == "today"
        assert max_urgency("safe", "tomorrow") == "tomorrow"
        assert max_urgency("safe", "safe") == "safe"

    def test_no_args_returns_safe(self):
        assert max_urgency() == "safe"

    def test_unknown_treated_as_safe(self):
        assert max_urgency("unknown", "tomorrow") == "tomorrow"


# ---------------------------------------------------------------------------
# E. Integration: /check must include `domains` and keep legacy fields
# ---------------------------------------------------------------------------

class TestCheckResponseShape:
    def test_check_includes_domains_array(self):
        from fastapi.testclient import TestClient

        from broombuster.api import app as app_module

        # Known coord that resolves to a Bay Area sweeping segment.
        lat, lon = 37.821326, -122.280705
        with TestClient(app_module.app) as client:
            resp = client.post("/check", json={
                "lat": lat, "lon": lon, "region": "bay_area",
            })
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # New field
        assert "domains" in data, "Step 3 must add `domains` to /check responses"
        assert isinstance(data["domains"], list)
        ids = [d["id"] for d in data["domains"]]
        assert "sweeping" in ids

        # Legacy fields unchanged
        for key in ("urgency", "schedule_even", "schedule_odd",
                    "car_side", "message", "address", "snap", "geojson"):
            assert key in data, f"legacy field {key!r} must remain"

    def test_geojson_features_tagged_with_domain(self):
        """Map features must carry properties.domain so the frontend can
        eventually layer per-domain styling."""
        from fastapi.testclient import TestClient

        from broombuster.api import app as app_module

        lat, lon = 37.821326, -122.280705
        with TestClient(app_module.app) as client:
            resp = client.post("/check", json={
                "lat": lat, "lon": lon, "region": "bay_area",
            })
        data = resp.json()
        features = (data.get("geojson") or {}).get("features", [])
        if not features:
            return  # no features at this coord — nothing to assert
        for f in features:
            domain = f.get("properties", {}).get("domain")
            assert domain == "sweeping", (
                f"feature missing properties.domain or wrong value: {domain!r}"
            )
