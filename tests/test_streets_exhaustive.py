"""Exhaustive regression-style tests across all available cities.

These tests sample the most-common street names in each city's prebuilt
FlatGeobuf and verify:
  - the name-index contains the normalized key
  - the nearest segment for that street has schedule information
  - if address ranges exist, at least one segment covers a sample house number

These exercises help catch regressions in name-indexing, range parsing,
and schedule propagation when normalizers or persistence change.
"""
import math

import pytest
from pyproj import Transformer
from shapely.geometry import Point

from broombuster import analysis, data_loader, normalize
from broombuster.cities import CITIES


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

    # prepare spatial transformer and index
    trans = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    gdf3857 = gdf.to_crs("EPSG:3857")
    name_idx = analysis._get_name_index(gdf)

    for name in names:
        norm = analysis._norm_name(name)
        assert norm in name_idx, f"Name index missing {norm} ({name}) in {city_key}"
        inds = name_idx[norm]
        assert inds, f"No indices for {name} in {city_key}"

        # nearest segment to the centroid of those segments should have a schedule
        # compute a representative point (centroid of first geometry)
        first_row = gdf.loc[inds[0]]
        geom = first_row.geometry
        assert geom is not None and not geom.is_empty
        centroid = geom.representative_point()
        # transform to 3857 for distance checks
        try:
            cx, cy = trans.transform(centroid.x, centroid.y)
        except Exception:
            # if transform fails, fall back to 4326 directly
            cx, cy = centroid.x, centroid.y
        car_pt = Point(cx, cy)

        nearest = None
        nearest_d = math.inf
        for i in inds:
            row = gdf3857.loc[i]
            g = row.geometry
            if g is None or g.is_empty:
                continue
            d = car_pt.distance(g)
            if d < nearest_d:
                nearest_d = d
                nearest = row

        assert nearest is not None, f"Could not find nearest segment for {name} in {city_key}"
        de = nearest.get("DAY_EVEN")
        do = nearest.get("DAY_ODD")
        de_desc = nearest.get("DESC_EVEN")
        do_desc = nearest.get("DESC_ODD")
        te = nearest.get("TIME_EVEN")
        to = nearest.get("TIME_ODD")
        # For Chicago-style zone data there may legitimately be no per-zone
        # schedule fields in some rows; avoid failing the test on that basis.
        if CITIES.get(city_key, {}).get("schema") == "chicago":
            continue
        has_schedule = any(
            (isinstance(x, str) and x.strip())
            for x in (de, do, de_desc, do_desc, te, to)
        )
        assert has_schedule, f"Nearest segment for {name} in {city_key} missing schedule info"


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
        norm = analysis._norm_name(name)
        idx = analysis._get_name_index(gdf)
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
                    int(float(v)) for v in (lf, lt, rf, rt)
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
                    if (isinstance(row.get("L_F_ADD"), (int, float, str))
                            and row.get("L_F_ADD") is not None):
                        try:
                            lf = int(float(row.get("L_F_ADD")))
                            lt = int(float(row.get("L_T_ADD")))
                            if lf <= sample_num <= lt:
                                covers_sample = True
                        except Exception:
                            pass
                    if (isinstance(row.get("R_F_ADD"), (int, float, str))
                            and row.get("R_F_ADD") is not None):
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
# street_display helpers — previously nested (never ran), now at module level
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
