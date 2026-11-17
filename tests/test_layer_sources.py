"""ArcGIS and Socrata fetching of the layer build, against stub servers (no network)."""

import pytest

from broombuster import layer_sources

_PAGING = {"objectIdField": "OBJECTID", "advancedQueryCapabilities": {"supportsPagination": True}}


class _Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def _feature(i):
    point = {"type": "Point", "coordinates": [-122.0 - i / 100, 37.8]}
    return {"type": "Feature", "id": i, "geometry": point, "properties": {"n": i}}


def _arcgis_server(monkeypatch, meta, honor_offset=True):
    """A stub ArcGIS layer of 5 features, 2 per page; returns the query parameters it got."""
    calls = []

    def _get(url, **p):
        calls.append(p)
        assert len(calls) < 50, "endless paging"
        if not url.endswith("/query"):
            return _Resp({"maxRecordCount": 2, "supportedQueryFormats": "JSON, geoJSON", **meta})
        if p.get("returnIdsOnly"):
            return _Resp({"objectIds": [4, 2, 0, 3, 1]})
        if "objectIds" in p:
            return _Resp({"features": [_feature(int(i)) for i in p["objectIds"].split(",")]})
        start = p["resultOffset"] if honor_offset else 0
        rows = [_feature(i) for i in range(5)][start : start + p["resultRecordCount"]]
        return _Resp({"features": rows, "exceededTransferLimit": start + len(rows) < 5})

    monkeypatch.setattr(layer_sources, "_get", _get)
    return calls


@pytest.mark.parametrize(
    ("meta", "honor_offset", "offset_pages"),
    [
        (_PAGING, True, 3),  # pages ordered by object id
        ({"objectIdField": "OBJECTID"}, True, 0),  # no supportsPagination: by id batches
        (_PAGING, False, 2),  # offset ignored: page 1 again, then by id batches
    ],
)
def test_arcgis_gets_every_feature_once(monkeypatch, meta, honor_offset, offset_pages):
    calls = _arcgis_server(monkeypatch, meta, honor_offset)
    gdf, _ = layer_sources.arcgis("https://example.test/FeatureServer/0")
    assert sorted(gdf["n"]) == [0, 1, 2, 3, 4]
    paged = [p for p in calls if "resultOffset" in p]
    assert len(paged) == offset_pages
    assert all(p["orderByFields"] == "OBJECTID" for p in paged)


def test_arcgis_error_in_body_raises(monkeypatch):
    def _get(url, **p):
        if url.endswith("/query"):
            return _Resp({"error": {"code": 400, "message": "Invalid query"}})
        return _Resp({"supportedQueryFormats": "geoJSON", **_PAGING})

    monkeypatch.setattr(layer_sources, "_get", _get)
    with pytest.raises(ValueError, match="Invalid query"):
        layer_sources.arcgis("https://example.test/FeatureServer/0")


def test_socrata_rows_and_row_limit(monkeypatch):
    seen = []

    def _get(url, **p):
        seen.append(p)
        if "/api/views/" in url:
            return _Resp({"rowsUpdatedAt": 1_790_000_000})
        return _Resp({"features": [_feature(0), _feature(1)]})

    monkeypatch.setattr(layer_sources, "_get", _get)
    gdf, asof = layer_sources.socrata("data.example.test", "abcd-1234", where="x = 1")
    assert list(gdf["n"]) == [0, 1] and asof == "2026-09-21"
    assert seen[-1]["$where"] == "x = 1"
    monkeypatch.setattr(layer_sources, "_SOCRATA_LIMIT", 2)  # a truncated answer
    with pytest.raises(ValueError, match="raise the limit"):
        layer_sources.socrata("data.example.test", "abcd-1234")
