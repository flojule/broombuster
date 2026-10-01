"""
Tests for the domain plugin registry, the SweepingPlugin output contract, and
the /check response shape built from it.
"""

import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from broombuster import domains
from broombuster.domains import DomainPlugin
from broombuster.domains.registry import for_city
from broombuster.domains.sweeping import SweepingPlugin

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
        self.label = street_display or street_name


# ---------------------------------------------------------------------------
# A. Registry shape
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_get_by_id(self):
        assert domains.get("sweeping").domain_id == "sweeping"
        assert domains.get("trash").subject == "home"

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

    def test_schedule_lines_collapse_identical_sides(self):
        """When both sides are identical, schedule_lines is one unlabelled line."""
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
# C. /check response shape
# ---------------------------------------------------------------------------

class TestCheckResponseShape:
    def test_check_shape(self, app_client):
        # Known coord that resolves to a Bay Area sweeping segment.
        resp = app_client.post("/check", json={
            "lat": 37.821326, "lon": -122.280705, "region": "bay_area",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data) == {"region", "city", "address", "address_pending", "domains"}
        (sweeping,) = [d for d in data["domains"] if d["id"] == "sweeping"]
        assert set(sweeping) == {"id", "label", "urgency", "schedule_lines", "extras"}
        assert sweeping["urgency"] in ("today", "tomorrow", "safe")
        assert set(sweeping["extras"]) == {
            "car_side", "side_labels", "schedule_even", "schedule_odd", "detail_html"}
