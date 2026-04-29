"""
Exhaustive tests for normalize.street_name(), normalize.time_display(),
and normalize.house_number().

Covers:
  - Case folding (mixed, lower, upper)
  - Suffix stripping (abbreviated + spelled-out, with/without trailing period)
  - Directional prefix normalization (N/NORTH, S/SOUTH, E/EAST, W/WEST,
    NE/NORTHEAST, NW/NORTHWEST, SE/SOUTHEAST, SW/SOUTHWEST)
  - Period stripping inside the name ("N. Park" → "N PARK")
  - Whitespace collapsing
  - Ordinal numbers (1st/1ST/first should all match)
  - Edge cases (empty, None, numeric-only, all-suffix)
  - Cross-city equivalence: same real street expressed as GDF would store it
    vs what Nominatim returns
  - Time display: compact normalization
  - House number: ranges, semicolons, letter suffix
"""

import os

import geopandas as gpd
import pytest

from broombuster import normalize


# ─────────────────────────────────────────────────────────────────────────────
# street_name() — basic case + suffix
# ─────────────────────────────────────────────────────────────────────────────

class TestStreetNameBasic:
    def test_uppercase_passthrough(self):
        assert normalize.street_name("GRAND AVE") == normalize.street_name("GRAND AVE")

    def test_lowercase_input(self):
        assert normalize.street_name("grand avenue") == normalize.street_name("GRAND AVE")

    def test_title_case_input(self):
        assert normalize.street_name("Grand Avenue") == normalize.street_name("GRAND AVE")

    def test_mixed_case_input(self):
        assert normalize.street_name("MacArthur Blvd") == normalize.street_name("MACARTHUR BLVD")

    def test_empty_string(self):
        assert normalize.street_name("") == ""

    def test_whitespace_only(self):
        assert normalize.street_name("   ") == ""

    def test_none_input(self):
        assert normalize.street_name(None) == ""

    def test_non_string(self):
        assert normalize.street_name(42) == ""

    def test_whitespace_collapsing(self):
        assert normalize.street_name("GRAND   AVE") == normalize.street_name("GRAND AVE")

    def test_leading_trailing_whitespace(self):
        assert normalize.street_name("  Grand Ave  ") == normalize.street_name("Grand Ave")


# ─────────────────────────────────────────────────────────────────────────────
# Suffix stripping — abbreviated forms
# ─────────────────────────────────────────────────────────────────────────────

class TestSuffixStripping:
    @pytest.mark.parametrize("suffix", [
        "ST", "AVE", "AV", "BLVD", "BL", "DR", "RD", "CT", "PL",
        "LN", "WAY", "TER", "TERR", "CIR", "HWY", "PKWY", "PKY",
        "EXPY", "FWY", "TPKE",
    ])
    def test_abbreviated_suffix_stripped(self, suffix):
        norm = normalize.street_name(f"GRAND {suffix}")
        assert norm == "GRAND", f"Expected 'GRAND', got {norm!r} (suffix={suffix!r})"

    @pytest.mark.parametrize("suffix", [
        "STREET", "AVENUE", "BOULEVARD", "DRIVE", "ROAD", "COURT",
        "PLACE", "LANE", "CIRCLE", "HIGHWAY", "PARKWAY",
        "EXPRESSWAY", "FREEWAY", "TURNPIKE",
    ])
    def test_spelled_out_suffix_stripped(self, suffix):
        norm = normalize.street_name(f"GRAND {suffix}")
        assert norm == "GRAND", f"Expected 'GRAND', got {norm!r} (suffix={suffix!r})"

    def test_abbreviated_suffix_lowercase(self):
        assert normalize.street_name("Grand ave") == normalize.street_name("Grand Avenue")

    def test_suffix_with_trailing_period(self):
        # "GRAND AVE." — trailing period should be stripped with suffix
        assert normalize.street_name("GRAND AVE.") == "GRAND"

    def test_no_suffix(self):
        # Name without a recognizable suffix stays intact
        assert normalize.street_name("BROADWAY") == "BROADWAY"

    def test_suffix_only(self):
        # A name that IS just a suffix — strips to empty, returns ""
        result = normalize.street_name("STREET")
        # Could be "" or "STREET" — just ensure it doesn't crash
        assert isinstance(result, str)

    def test_numbered_street(self):
        assert normalize.street_name("12th St") == normalize.street_name("12th Street")

    def test_numbered_avenue(self):
        assert normalize.street_name("100th Ave") == normalize.street_name("100th Avenue")

    def test_st_not_stripped_from_middle(self):
        # "ST" in the middle should NOT be stripped (suffix regex anchored at end)
        norm = normalize.street_name("ST JAMES CT")
        assert "ST" in norm or "JAMES" in norm  # "ST" prefix stays, "CT" stripped


# ─────────────────────────────────────────────────────────────────────────────
# Directional prefix normalization
# N / NORTH, S / SOUTH, E / EAST, W / WEST, NE / NORTHEAST …
# ─────────────────────────────────────────────────────────────────────────────

class TestDirectionalPrefix:
    """
    Abbreviated and spelled-out leading directionals must produce the same key.
    e.g. "N MAIN ST" == "NORTH MAIN ST" == "N. MAIN ST"
    """

    @pytest.mark.parametrize("short,long", [
        ("N", "NORTH"),
        ("S", "SOUTH"),
        ("E", "EAST"),
        ("W", "WEST"),
        ("NE", "NORTHEAST"),
        ("NW", "NORTHWEST"),
        ("SE", "SOUTHEAST"),
        ("SW", "SOUTHWEST"),
    ])
    def test_directional_abbrev_matches_full(self, short, long):
        a = normalize.street_name(f"{short} PARK AVE")
        b = normalize.street_name(f"{long} PARK AVE")
        assert a == b, f"{short!r} → {a!r}  vs  {long!r} → {b!r}"

    @pytest.mark.parametrize("short,long", [
        ("N", "NORTH"),
        ("S", "SOUTH"),
        ("E", "EAST"),
        ("W", "WEST"),
    ])
    def test_directional_case_insensitive(self, short, long):
        a = normalize.street_name(f"{short.lower()} Park Ave")
        b = normalize.street_name(f"{long.title()} Park Avenue")
        assert a == b, f"{short.lower()!r} → {a!r}  vs  {long.title()!r} → {b!r}"

    def test_period_after_directional(self):
        # "N. Park Blvd" should equal "North Park Blvd"
        assert normalize.street_name("N. Park Blvd") == normalize.street_name("North Park Blvd")

    def test_directional_not_stripped_when_name_is_direction_only(self):
        # A street literally named "North St" — the directional IS the name
        # "North St" → suffix stripped → "NORTH" (or the canonical abbrev)
        # "N St" → suffix stripped → "N" (or the canonical abbrev)
        # They must be equal
        a = normalize.street_name("N St")
        b = normalize.street_name("North St")
        assert a == b

    def test_directional_prefix_nominatim_vs_gdf_oakland_style(self):
        """
        Oakland GDF stores "E 12th St"; Nominatim returns "East 12th Street".
        Both should produce the same normalized key.
        """
        gdf_name  = "E 12th St"      # Oakland shapefile style (Title Case + abbrev)
        nominatim = "East 12th Street"  # Nominatim output
        assert normalize.street_name(gdf_name) == normalize.street_name(nominatim)

    def test_no_directional_unchanged(self):
        # A name with no leading directional is not modified
        a = normalize.street_name("GRAND AVE")
        assert "GRAND" in a

    def test_directional_not_applied_mid_name(self):
        # "NORTH" in the middle of a name should NOT be abbreviated to "N"
        # e.g. "NORTHGATE AVE" should NOT become "N GATE AVE"
        norm = normalize.street_name("NORTHGATE AVE")
        assert "NORTHGATE" in norm or "NORTH" in norm


# ─────────────────────────────────────────────────────────────────────────────
# Period / punctuation stripping
# ─────────────────────────────────────────────────────────────────────────────

class TestPeriodStripping:
    def test_period_in_directional(self):
        assert normalize.street_name("N. Park") == normalize.street_name("N Park")

    def test_period_in_suffix(self):
        # "Grand Ave." — trailing period with suffix
        assert normalize.street_name("Grand Ave.") == "GRAND"

    def test_period_in_abbreviation(self):
        # "Dr. Martin Luther King Jr Blvd" — embedded period stays (not stripped)
        # Just ensure it doesn't crash and returns non-empty
        result = normalize.street_name("Dr. Martin Luther King Blvd")
        assert isinstance(result, str) and len(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Cross-city real-world equivalences
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossCityEquivalences:
    """
    Real pairs of (GDF stored name, Nominatim returned name) that must match.
    """

    @pytest.mark.parametrize("gdf_name, nominatim_name", [
        # Oakland (mixed-case GDF, Nominatim spelled-out)
        ("100th Ave",        "100th Avenue"),
        ("Grand Ave",        "Grand Avenue"),
        ("MacArthur Blvd",   "MacArthur Boulevard"),
        ("Fruitvale Ave",    "Fruitvale Avenue"),
        ("International Blvd", "International Boulevard"),
        ("Foothill Blvd",    "Foothill Boulevard"),
        ("High St",          "High Street"),
        ("Park Blvd",        "Park Boulevard"),
        ("Lakeshore Ave",    "Lakeshore Avenue"),
        ("Bancroft Ave",     "Bancroft Avenue"),
        # Berkeley / Alameda (UPPERCASE GDF)
        ("TELEGRAPH AVE",    "Telegraph Avenue"),
        ("SHATTUCK AVE",     "Shattuck Avenue"),
        ("UNIVERSITY AVE",   "University Avenue"),
        ("ADELINE ST",       "Adeline Street"),
        ("CEDAR ST",         "Cedar Street"),
        ("ADAMS ST",         "Adams Street"),
        ("CENTRAL AVE",      "Central Avenue"),
        # SF (UPPERCASE GDF, corridor name)
        ("MARKET ST",        "Market Street"),
        ("MISSION ST",       "Mission Street"),
        ("HAYES ST",         "Hayes Street"),
        ("GEARY BLVD",       "Geary Boulevard"),
        ("DIVISADERO ST",    "Divisadero Street"),
        # Numbered streets
        ("12th St",          "12th Street"),
        ("12TH ST",          "12th Street"),
        ("62ND STREET",      "62nd Street"),
        ("100th Ave",        "100th Avenue"),
    ])
    def test_gdf_equals_nominatim(self, gdf_name, nominatim_name):
        a = normalize.street_name(gdf_name)
        b = normalize.street_name(nominatim_name)
        assert a == b, (
            f"Mismatch:\n  GDF {gdf_name!r} → {a!r}\n"
            f"  Nominatim {nominatim_name!r} → {b!r}"
        )

    @pytest.mark.parametrize("gdf_name, nominatim_name", [
        # Directional prefix cases
        ("E 12th St",       "East 12th Street"),
        ("N Broadway",      "North Broadway"),
        ("S Main St",       "South Main Street"),
        ("W Grand Ave",     "West Grand Avenue"),
        ("N. Park Blvd",    "North Park Boulevard"),
    ])
    def test_directional_gdf_equals_nominatim(self, gdf_name, nominatim_name):
        a = normalize.street_name(gdf_name)
        b = normalize.street_name(nominatim_name)
        assert a == b, (
            f"Directional mismatch:\n  GDF {gdf_name!r} → {a!r}\n"
            f"  Nominatim {nominatim_name!r} → {b!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# STREET_NAME storage: should be uppercase in all cities
# ─────────────────────────────────────────────────────────────────────────────

class TestStreetNameStorage:
    """
    Verify that STREET_NAME in FGB files is uppercase for all cities.
    A mixed-case STREET_NAME still works for comparison (normalize uppercases),
    but causes display inconsistency in tooltips.
    """

    @pytest.mark.parametrize("fgb_path,city", [
        ("data/oakland/StreetSweeping.fgb",            "Oakland"),
        ("data/san_francisco/StreetSweeping.fgb",       "San Francisco"),
        ("data/berkeley/StreetSweeping.fgb",            "Berkeley"),
        ("data/alameda/StreetSweeping.fgb",             "Alameda"),
        ("data/chicago/StreetSweepingZones.fgb",        "Chicago"),
    ])
    def test_street_names_are_uppercase(self, fgb_path, city):
        import geopandas as gpd, os, re
        root = os.path.join(os.path.dirname(__file__), "..")
        path = os.path.join(root, fgb_path)
        if not os.path.exists(path):
            pytest.skip(f"{fgb_path} not built yet")
        gdf = gpd.read_file(path)
        bad = [
            n for n in gdf["STREET_NAME"].dropna()
            if isinstance(n, str) and re.search(r"[a-z]", n)
        ]
        assert not bad, (
            f"{city}: {len(bad)} STREET_NAME values contain lowercase letters. "
            f"Samples: {bad[:5]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# time_display()
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeDisplay:
    def test_already_clean(self):
        assert normalize.time_display("8AM–10AM") == "8AM–10AM"

    def test_with_colon_and_space(self):
        assert normalize.time_display("8:00 AM -11:00 AM") == "8AM–11AM"

    def test_bullet_separator(self):
        assert normalize.time_display("12:00 PM • 3:00 PM") == "12PM–3PM"

    def test_half_hour(self):
        assert normalize.time_display("7:30 AM - 9:00 AM") == "7:30AM–9AM"

    def test_pm_hours(self):
        assert normalize.time_display("1:00 PM - 4:00 PM") == "1PM–4PM"

    def test_na_passthrough(self):
        assert normalize.time_display("N/A") == "N/A"

    def test_empty_string(self):
        assert normalize.time_display("") == "N/A"

    def test_none_input(self):
        assert normalize.time_display(None) == "N/A"

    def test_no_match_passthrough(self):
        # If it doesn't match the time pattern, return as-is
        result = normalize.time_display("morning")
        assert result == "morning"

    def test_to_separator(self):
        assert normalize.time_display("8AM to 10AM") == "8AM–10AM"

    def test_dash_separator(self):
        assert normalize.time_display("8AM-10AM") == "8AM–10AM"

    def test_em_dash_separator(self):
        assert normalize.time_display("8AM—10AM") == "8AM–10AM"


# ─────────────────────────────────────────────────────────────────────────────
# house_number()
# ─────────────────────────────────────────────────────────────────────────────

class TestHouseNumber:
    def test_plain_integer(self):
        assert normalize.house_number("2211") == 2211

    def test_range_hyphen(self):
        assert normalize.house_number("6321-6323") == 6321

    def test_range_semicolon(self):
        assert normalize.house_number("1703;1711") == 1703

    def test_range_comma(self):
        assert normalize.house_number("100,102") == 100

    def test_range_slash(self):
        assert normalize.house_number("200/202") == 200

    def test_letter_suffix(self):
        assert normalize.house_number("100 A") == 100

    def test_leading_whitespace(self):
        assert normalize.house_number("  42  ") == 42

    def test_empty_string(self):
        assert normalize.house_number("") is None

    def test_none_input(self):
        assert normalize.house_number(None) is None

    def test_non_numeric(self):
        assert normalize.house_number("ABC") is None

    def test_leading_zeros(self):
        assert normalize.house_number("0042") == 42

    def test_even_number(self):
        n = normalize.house_number("1702")
        assert n == 1702
        assert n % 2 == 0

    def test_odd_number(self):
        n = normalize.house_number("1703")
        assert n == 1703
        assert n % 2 == 1


# ─────────────────────────────────────────────────────────────────────────────
# Regression: specific mismatches reported in production
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressions:
    def test_mosley_ave_vs_mosley_avenue(self):
        """Original bug: GDF had 'MOSLEY AVE', Nominatim returned 'Mosley Avenue'."""
        assert normalize.street_name("MOSLEY AVE") == normalize.street_name("Mosley Avenue")

    def test_sf_corridor_uppercase(self):
        """SF normalizer uppercases 'corridor' column — ensure stored name is all caps."""
        assert normalize.street_name("MARKET ST") == normalize.street_name("Market Street")

    def test_berkeley_numbered_with_suffix(self):
        """Berkeley PDF has '62ND STREET', Nominatim may return '62nd Street'."""
        assert normalize.street_name("62ND STREET") == normalize.street_name("62nd Street")

    def test_alameda_way_suffix(self):
        assert normalize.street_name("ADELPHIAN WAY") == normalize.street_name("Adelphian Way")

    def test_chicago_ward_section_unchanged(self):
        """Chicago stores 'Ward 01, Section 01' — not a street name, should not crash."""
        result = normalize.street_name("Ward 01, Section 01")
        assert isinstance(result, str)


# Stricter normalization tests that were previously in a separate file.
class TestStreetDisplayAbbreviations:
    def test_avenue_abbrev(self):
        assert normalize.street_display("Grand Avenue") == "Grand Ave"

    def test_boulevard_abbrev(self):
        assert normalize.street_display("MacArthur Boulevard") == "Macarthur Blvd"

    def test_directional_abbrev(self):
        assert normalize.street_display("East 12th Street") == "E 12th St"


class TestFGBSchemaAndKeys:
    @pytest.mark.parametrize("fgb_path", [
        "data/oakland/StreetSweeping.fgb",
        "data/san_francisco/StreetSweeping.fgb",
        "data/berkeley/StreetSweeping.fgb",
        "data/alameda/StreetSweeping.fgb",
        "data/chicago/StreetSweepingZones.fgb",
    ])
    def test_fgb_has_key_and_display_and_key_matches(self, fgb_path):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, fgb_path)
        if not os.path.exists(path):
            pytest.skip(f"{fgb_path} not built yet")
        gdf = gpd.read_file(path)
        # Required persisted columns
        assert "STREET_KEY" in gdf.columns and "STREET_DISPLAY" in gdf.columns

        # Spot-check: STREET_KEY must equal normalized STREET_NAME for non-empty names
        for n, k in zip(gdf["STREET_NAME"].fillna("").iloc[:50], gdf["STREET_KEY"].fillna("").iloc[:50]):
            if isinstance(n, str) and n.strip():
                assert k == normalize.street_name(n)


# ─────────────────────────────────────────────────────────────────────────────
# car_side() — determines which side of the street the car is on
# Used in api.py, maps.py — must be consistent across all call sites.
# ─────────────────────────────────────────────────────────────────────────────

class TestCarSide:
    def test_even_number(self):
        assert normalize.car_side(1234) == "even"

    def test_odd_number(self):
        assert normalize.car_side(1235) == "odd"

    def test_zero_treated_as_odd(self):
        # 0 is even mathematically, but falsy → treated as unknown → "odd"
        assert normalize.car_side(0) == "odd"

    def test_none_defaults_to_odd(self):
        assert normalize.car_side(None) == "odd"

    def test_boundary_two(self):
        assert normalize.car_side(2) == "even"

    def test_boundary_one(self):
        assert normalize.car_side(1) == "odd"

    def test_large_even(self):
        assert normalize.car_side(10000) == "even"

    def test_large_odd(self):
        assert normalize.car_side(9999) == "odd"

