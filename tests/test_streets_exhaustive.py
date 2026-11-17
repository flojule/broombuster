"""Exhaustive regression-style tests across all available cities.

These tests sample the most-common street names in each city's prebuilt
FlatGeobuf and verify:
  - every common name normalizes to a STREET_KEY present in the data
  - the resolver places a car mid-block on that street, with schedule information
  - if address ranges exist, at least one segment covers a sample house number

These exercises help catch regressions in name-indexing, range parsing,
and schedule propagation when normalizers or persistence change.
"""

import math

import pytest
from pyproj import Transformer

from broombuster import analysis, data_loader, normalize, resolve
from broombuster.cities import CITIES


def _name_index(gdf) -> dict:
    """{STREET_KEY: [row labels]}."""
    return {k: list(ix) for k, ix in gdf.groupby("STREET_KEY").groups.items() if k}


def _load_city(city_key):
    try:
        gdf = data_loader.load_city_data(city_key)
    except FileNotFoundError:
        pytest.skip(f"Data for {city_key} not available; build the FGBs")
    return gdf


@pytest.mark.parametrize("city_key", list(CITIES.keys()))
def test_common_streets_index_and_schedule(city_key):
    gdf = _load_city(city_key)
    # choose up to 8 most frequent street names to exercise
    names = []
    if "STREET_NAME" in gdf.columns:
        vc = gdf["STREET_NAME"].dropna().astype(str).value_counts()
        # filter out empty/whitespace names
        names = [n for n in vc.index.tolist() if isinstance(n, str) and n.strip()][:8]
    if not names:
        pytest.skip(f"No STREET_NAME values for {city_key}")

    gdf3857 = gdf.to_crs("EPSG:3857")
    to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    name_idx = _name_index(gdf)

    for name in names:
        norm = normalize.street_name(name)
        assert norm in name_idx, f"Name index missing {norm} ({name}) in {city_key}"
        inds = name_idx[norm]
        assert inds, f"No indices for {name} in {city_key}"

        # A car parked mid-block on the street (inside the zone, for polygons).
        geom = gdf3857.loc[inds[0]].geometry
        assert geom is not None and not geom.is_empty
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            pt = geom.representative_point()
        else:
            pt = geom.interpolate(0.5, normalized=True)
        lon, lat = to_4326.transform(pt.x, pt.y)
        r = resolve.resolve_car_segment(gdf3857, lat, lon)

        if r.is_polygon:
            assert r.segment.geometry.contains(pt), f"{name} in {city_key}: zone not containing"
            continue
        assert r.distance_m < 1.0, f"{name} in {city_key}: resolved {r.distance_m:.1f} m away"
        same_line = r.segment.geometry.equals(geom)  # one line drawn under two names (freeways)
        assert r.segment["STREET_KEY"] == norm or same_line, f"{name} in {city_key}: {r.label}"
        even, odd = analysis.schedules_for_all_matching_rows(gdf3857, r)
        texts = [
            r.segment.get(c)
            for c in ("DAY_EVEN", "DAY_ODD", "DESC_EVEN", "DESC_ODD", "TIME_EVEN", "TIME_ODD")
        ]
        has_schedule = even or odd or any(isinstance(x, str) and x.strip() for x in texts)
        assert has_schedule, f"Resolved segment for {name} in {city_key} missing schedule info"


@pytest.mark.parametrize("city_key", list(CITIES.keys()))
def test_address_range_presence_and_sample_lookup(city_key):
    gdf = _load_city(city_key)
    if not any(c in gdf.columns for c in ("L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD")):
        pytest.skip(f"City {city_key} has no address range columns")

    vc = gdf["STREET_NAME"].dropna().astype(str).value_counts()
    names = vc.head(8).index.tolist()
    if not names:
        pytest.skip(f"No STREET_NAME values for {city_key}")

    for name in names:
        norm = normalize.street_name(name)
        idx = _name_index(gdf)
        inds = idx.get(norm, [])
        if not inds:
            continue
        has_any_range = False
        covers_sample = False
        sample_num = None
        for i in inds:
            try:
                row = gdf.loc[i]
            except Exception:
                # index label may refer to an outer/combined GDF; skip if not present
                continue
            try:
                lf = row.get("L_F_ADD")
                lt = row.get("L_T_ADD")
                rf = row.get("R_F_ADD")
                rt = row.get("R_T_ADD")
                nums = [
                    int(float(v))
                    for v in (lf, lt, rf, rt)
                    if v is not None and v != "" and not (isinstance(v, float) and math.isnan(v))
                ]
            except Exception:
                nums = []
            if nums:
                has_any_range = True
                # choose median value from available bounds as sample
                try:
                    low = int(float(row.get("L_F_ADD")))
                    high = int(float(row.get("L_T_ADD")))
                    sample_num = (low + high) // 2
                except Exception:
                    try:
                        low = int(float(row.get("R_F_ADD")))
                        high = int(float(row.get("R_T_ADD")))
                        sample_num = (low + high) // 2
                    except Exception:
                        sample_num = None
                if sample_num is not None:
                    if (
                        isinstance(row.get("L_F_ADD"), (int, float, str))
                        and row.get("L_F_ADD") is not None
                    ):
                        try:
                            lf = int(float(row.get("L_F_ADD")))
                            lt = int(float(row.get("L_T_ADD")))
                            if lf <= sample_num <= lt:
                                covers_sample = True
                        except Exception:
                            pass
                    if (
                        isinstance(row.get("R_F_ADD"), (int, float, str))
                        and row.get("R_F_ADD") is not None
                    ):
                        try:
                            rf = int(float(row.get("R_F_ADD")))
                            rt = int(float(row.get("R_T_ADD")))
                            if rf <= sample_num <= rt:
                                covers_sample = True
                        except Exception:
                            pass
                if covers_sample:
                    break

        # If the city has ranges for these streets, at least one should cover the sample
        if has_any_range:
            assert covers_sample, (
                f"For {name} in {city_key}, found ranges but none covered sample {sample_num}"
            )


# ---------------------------------------------------------------------------
# street_display helpers
# ---------------------------------------------------------------------------


def test_display_ordinals_preserved():
    import re

    cases = ["12th St", "62ND STREET", "100TH AVE"]
    for inp in cases:
        out = normalize.street_display(inp)
        assert isinstance(out, str) and out.strip()
        assert re.search(r"\d+(st|nd|rd|th)\b", out.lower()), (
            f"Ordinal not preserved in {inp!r} -> {out!r}"
        )


def test_suffix_only_display_fallback():
    # "STREET" alone has no name tokens — street_display abbreviates the
    # suffix, so the result is "St" (abbreviated form), not "Street".
    out = normalize.street_display("STREET")
    assert out in ("St", "Street"), f"Unexpected display for lone suffix: {out!r}"


def test_street_name_numeric_equivalence():
    assert normalize.street_name("100th Ave") == normalize.street_name("100TH AVE")
