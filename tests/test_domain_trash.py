"""ZoneTrashPlugin: zone resolve + per-stream schedule + urgency.

Synthetic polygons only — no real city data, no network. Validates the
trash plugin satisfies the DomainPlugin protocol and reuses the sweeping
day-code helpers (weekly + biweekly + DATES + no-sweep).
"""

import datetime
import os
from zoneinfo import ZoneInfo

os.environ.setdefault("DEV_MODE", "1")

import geopandas
import pandas as pd
import shapely.geometry

from broombuster.domains import DomainPlugin
from broombuster.domains.trash import ZoneTrashPlugin

_TZ = ZoneInfo("America/Los_Angeles")
# parse_sweeping_code expands weekly codes around the REAL current month, so
# date-sensitive assertions must anchor to today, not an arbitrary fixed date.
_TODAY = datetime.date.today()
_TOMORROW = _TODAY + datetime.timedelta(days=1)
_AFTER = _TODAY + datetime.timedelta(days=2)
_NOW = datetime.datetime.combine(_TODAY, datetime.time(9, 0), tzinfo=_TZ)


def _dates(d: datetime.date) -> str:
    return f"DATES:{d.isoformat()}"


# A square around the origin in EPSG:3857 metres; the car sits at (0,0).
_ZONE = shapely.geometry.Polygon([(-50, -50), (50, -50), (50, 50), (-50, 50)])
# Point (0,0) in EPSG:3857 corresponds to lat/lon 0,0.
_CAR_LAT, _CAR_LON = 0.0, 0.0


def _zones(**cols):
    """One-zone GeoDataFrame in EPSG:3857 with the given stream columns."""
    row = {"ZONE_NAME": "Zone A", "_city": "testville", **cols}
    df = pd.DataFrame([row])
    df["geometry"] = _ZONE
    return geopandas.GeoDataFrame(df, crs="EPSG:3857")


def _cities(**trash):
    return {"testville": {"name": "Testville", "trash": {"kind": "zone", **trash}}}


def _plugin(zones_gdf, cities=None):
    return ZoneTrashPlugin(loader=lambda ck: zones_gdf,
                           cities=cities or _cities())


class TestSupportsCity:
    def test_supports_only_zone_kind(self):
        p = ZoneTrashPlugin(cities=_cities())
        assert p.supports_city("testville") is True

    def test_unconfigured_city_unsupported(self):
        p = ZoneTrashPlugin(cities={"x": {"name": "X"}})
        assert p.supports_city("x") is False

    def test_recollect_kind_not_supported_by_zone_plugin(self):
        p = ZoneTrashPlugin(cities={"x": {"trash": {"kind": "recollect"}}})
        assert p.supports_city("x") is False

    def test_protocol(self):
        assert isinstance(ZoneTrashPlugin(), DomainPlugin)

    def test_subject_is_home(self):
        # Trash is located at the residence, not the parked car.
        assert ZoneTrashPlugin().subject == "home"


class TestResolveFor:
    def test_car_inside_zone_resolves(self):
        p = _plugin(_zones(GARBAGE="ME"))
        r = p.resolve_for(None, _CAR_LAT, _CAR_LON, "testville")
        assert r is not None and r.is_polygon

    def test_car_outside_returns_none(self):
        p = _plugin(_zones(GARBAGE="ME"))
        # ~1 degree away → far outside the 100 m square.
        assert p.resolve_for(None, 1.0, 1.0, "testville") is None

    def test_no_data_returns_none(self):
        p = ZoneTrashPlugin(loader=lambda ck: None, cities=_cities())
        assert p.resolve_for(None, _CAR_LAT, _CAR_LON, "testville") is None


class TestFormat:
    def _result(self, zones, cities=None):
        p = _plugin(zones, cities)
        r = p.resolve_for(None, _CAR_LAT, _CAR_LON, "testville")
        return p.format(r, None, _NOW)

    def test_weekly_codes_render_without_desc(self):
        res = self._result(_zones(GARBAGE="ME", RECYCLE="ME", ORGANICS="ME"))
        assert res.domain_id == "trash"
        assert res.label == "Trash day"
        joined = " ".join(res.schedule_lines)
        assert "Garbage:" in joined and "Recycling:" in joined and "Organics:" in joined
        assert "Mon" in joined

    def test_collection_today_is_today_urgency(self):
        res = self._result(_zones(GARBAGE=_dates(_TODAY)))
        assert res.urgency == "today"

    def test_collection_tomorrow_is_tomorrow_urgency(self):
        res = self._result(_zones(GARBAGE=_dates(_TOMORROW)))
        assert res.urgency == "tomorrow"

    def test_future_only_is_safe(self):
        res = self._result(_zones(GARBAGE=_dates(_AFTER)))
        assert res.urgency == "safe"

    def test_multiple_streams_worst_case_today(self):
        # Garbage future, recycling today → overall today.
        res = self._result(_zones(GARBAGE=_dates(_AFTER), RECYCLE=_dates(_TODAY)))
        assert res.urgency == "today"

    def test_biweekly_needs_desc_column(self):
        zones = _zones(RECYCLE="M13", RECYCLE_DESC="Mon 1st & 3rd")
        cities = _cities(streams=[
            {"label": "Recycling", "column": "RECYCLE", "desc_column": "RECYCLE_DESC"},
        ])
        res = self._result(zones, cities)
        joined = " ".join(res.schedule_lines)
        assert "Recycling:" in joined and "Mon" in joined

    def test_no_sweep_code_skipped(self):
        res = self._result(_zones(GARBAGE="N", RECYCLE="ME"))
        joined = " ".join(res.schedule_lines)
        assert "Garbage:" not in joined
        assert "Recycling:" in joined

    def test_empty_streams_yields_no_collection_line(self):
        res = self._result(_zones())
        assert res.schedule_lines == ["No collection scheduled"]
        assert res.urgency == "safe"

    def test_none_resolved_is_safe(self):
        res = ZoneTrashPlugin().format(None, None, _NOW)
        assert res.urgency == "safe"
        assert res.schedule_lines

    def test_zone_name_in_extras(self):
        res = self._result(_zones(GARBAGE="ME"))
        assert res.extras["zone"] == "Zone A"
        assert res.extras["streams"].get("Garbage") == "ME"
