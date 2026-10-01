"""
Single source of truth for all data normalization in BroomBuster.

Import from here instead of defining normalization locally.
Every comparison of street names, times, and house numbers should go through
these functions so that data from different cities / sources is treated
identically.
"""

import re


def is_text(v) -> bool:
    """True only for non-empty strings (filters NaN, None, floats)."""
    return isinstance(v, str) and v.strip() != ""


# Placeholder strings source data uses for "no value".
_PLACEHOLDERS = frozenset({"", "N/A", "NA", "NONE", "NAN"})


def clean_text(v) -> str:
    """Stripped string, or "" for non-strings and placeholders ("N/A", "nan", …)."""
    s = v.strip() if isinstance(v, str) else ""
    return "" if s.upper() in _PLACEHOLDERS else s


# ── Street name ───────────────────────────────────────────────────────────────

# Trailing street-type suffixes — both abbreviated and spelled-out forms.
_SUFFIX_RE = re.compile(
    r"\b(ST|AVE|AV|BLVD|BL|DR|RD|CT|PL|LN|WAY|TER|TERR|"
    r"CIR|HWY|PKWY|PKY|EXPY|FWY|TPKE|"
    r"STREET|AVENUE|BOULEVARD|DRIVE|ROAD|COURT|PLACE|LANE|"
    r"CIRCLE|HIGHWAY|PARKWAY|EXPRESSWAY|FREEWAY|TURNPIKE)\.?\s*$",
    re.IGNORECASE,
)

_WHITESPACE_RE = re.compile(r"\s+")

# Maps abbreviated leading directionals to their canonical full-word form.
# Full words that are already canonical map through .get(d, d) unchanged.
_DIR_EXPAND = {
    "N":  "NORTH", "S":  "SOUTH", "E":  "EAST",  "W":  "WEST",
    "NE": "NORTHEAST", "NW": "NORTHWEST",
    "SE": "SOUTHEAST", "SW": "SOUTHWEST",
}

# Matches an abbreviated or full-word directional at the very start of the
# name, followed by whitespace and at least one more character.
# Longer alternatives listed first so "NORTHEAST" beats "NORTH" or "NE".
_DIR_PREFIX_RE = re.compile(
    r"^(NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST|NORTH|SOUTH|EAST|WEST|"
    r"NE|NW|SE|SW|N|S|E|W)\s+(?=\S)"
)


def street_name(raw: str) -> str:
    """
    Canonical key for street-name comparison.

    Rules applied in order:
      1. Uppercase + collapse internal whitespace
      2. Strip all periods (e.g. "N." → "N", "Ave." → "Ave")
      3. Normalize leading directional prefix to full word
         (N/N. → NORTH, E → EAST, NE → NORTHEAST, SOUTH → SOUTH, …)
      4. Strip trailing street-type suffix (Ave, Avenue, Blvd, …)
      5. Strip surrounding whitespace

    Examples:
      "Mosley Avenue"        →  "MOSLEY"
      "MOSLEY AVE"           →  "MOSLEY"
      "N. Park Blvd"         →  "NORTH PARK"
      "North Park Blvd"      →  "NORTH PARK"
      "E 12th St"            →  "EAST 12TH"
      "East 12th Street"     →  "EAST 12TH"
      "3rd St"               →  "3RD"
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""
    # 1. Uppercase + collapse whitespace
    n = _WHITESPACE_RE.sub(" ", raw.strip().upper())
    # 2. Strip periods (handles "N.", "Ave.", abbreviation dots)
    n = n.replace(".", "")
    n = _WHITESPACE_RE.sub(" ", n).strip()
    # 3. Normalize leading directional to full word
    n = _DIR_PREFIX_RE.sub(lambda m: _DIR_EXPAND.get(m.group(1), m.group(1)) + " ", n)
    # 4. Strip trailing suffix
    n = _SUFFIX_RE.sub("", n).strip()
    return n


# User-friendly display form for street names.
# Returns a short, readable title-cased name with abbreviated suffixes
# and abbreviated directionals (e.g. "East 12th Street" -> "E 12th St").
_SUFFIX_ABBR = {
    "STREET": "St", "ST": "St",
    "AVENUE": "Ave", "AVE": "Ave", "AV": "Ave",
    "BOULEVARD": "Blvd", "BLVD": "Blvd", "BL": "Blvd",
    "DRIVE": "Dr", "DR": "Dr",
    "ROAD": "Rd", "RD": "Rd",
    "COURT": "Ct", "CT": "Ct",
    "PLACE": "Pl", "PL": "Pl",
    "LANE": "Ln", "LN": "Ln",
    "WAY": "Way",
    "TERRACE": "Ter", "TER": "Ter", "TERR": "Ter",
    "CIRCLE": "Cir", "CIR": "Cir",
    "HIGHWAY": "Hwy", "HWY": "Hwy",
    "PARKWAY": "Pkwy", "PKWY": "Pkwy", "PKY": "Pkwy",
    "EXPRESSWAY": "Expy", "EXPY": "Expy",
    "FREEWAY": "Fwy", "FWY": "Fwy",
    "TURNPIKE": "Tpke", "TPKE": "Tpke",
}

# Full directional -> short abbreviation.
_DIR_ABBR = {v: k for k, v in _DIR_EXPAND.items()}


def street_display(raw: str) -> str:
    """
    Human-friendly, short street name appropriate for display and storage.

    Produces Title Case with short suffixes and abbreviated leading
    directionals. Returns empty string for non-string input.
    Examples:
      "Grand Avenue" -> "Grand Ave"
      "East 12th Street" -> "E 12th St"
      "MACARTHUR BOULEVARD" -> "Macarthur Blvd"
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""

    # Normalize spacing and remove periods for parsing
    s = _WHITESPACE_RE.sub(" ", raw.replace(".", " ").strip())

    # Work in uppercase for matching tokens, but keep original words
    toks = s.split()
    if not toks:
        return ""

    # Leading spelled-out directional -> abbreviation ("NORTHWEST" -> "NW").
    dir_token = _DIR_ABBR.get(toks[0].upper())
    rest = toks[1:] if dir_token else toks

    # Trailing suffix?
    suffix = None
    if rest:
        last_up = rest[-1].upper().rstrip('.')
        if last_up in _SUFFIX_ABBR:
            suffix = _SUFFIX_ABBR[last_up]
            rest = rest[:-1]

    # Build display parts
    # Title-case tokens but preserve ordinal suffixes (e.g., '12th' not '12Th')
    name = ""
    if rest:
        toks_out = []
        for t in rest:
            up = t.upper()
            m = re.match(r"^(\d+)([A-Z]+)$", up)
            if m:
                # number + letters (ordinal) -> keep number, lower-case suffix
                num, suf = m.groups()
                toks_out.append(f"{num}{suf.lower()}")
            else:
                toks_out.append(t.title())
        name = " ".join(toks_out)
    parts = []
    if dir_token:
        parts.append(dir_token)
    if name:
        parts.append(name)
    if suffix:
        parts.append(suffix)

    # Fallback: if everything stripped (e.g., input was just "STREET"),
    # return the original title-cased raw string trimmed.
    if not parts:
        return s.title()

    return " ".join(parts)


# ── Time display ──────────────────────────────────────────────────────────────

# Matches time ranges regardless of separator style or colon presence:
#   "8AM-10AM", "8:00 AM – 10:00 AM", "8AM to 10AM", "8:00 AM •10:00PM"
# Also tolerates the whitespace artifacts produced by Alameda's PDF parser:
#   "8:00 AM -11 :00 AM", "1 0:00 AM -1: 00 PM" — stray spaces inside hours,
#   minutes, and around colons. Each digit group allows internal spaces;
#   colons may be flanked by spaces.
_TIME_RANGE_RE = re.compile(
    r"(\d\s*\d?)\s*(?::\s*(\d\s*\d))?\s*(A[\s,]*M|P[\s,]*M)[\s,]*(?:[-–—•·o]|to)\s*"
    r"(\d\s*\d?)\s*(?::\s*(\d\s*\d))?\s*(A[\s,]*M|P[\s,]*M)",
    re.IGNORECASE,
)


def _clock_parts(h: str, m: str | None, ap: str) -> tuple[int, int, str]:
    """(hour, minute, "AM"|"PM") from captured groups, minus PDF stray spaces."""
    return int(re.sub(r"\s+", "", h)), int(re.sub(r"\s+", "", m)) if m else 0, \
        re.sub(r"[\s,]+", "", ap).upper()


def _fmt_part(h: str, m: str | None, ap: str) -> str:
    hh, mn, ap = _clock_parts(h, m, ap)
    return f"{hh}:{mn:02d}{ap}" if mn else f"{hh}{ap}"


def time_display(raw: str) -> str:
    """
    Normalize any time-range string to a compact 'HAM–HPM' form.

    Examples:
      "8:00 AM -11:00 AM"  →  "8AM–11AM"
      "10AM–1PM"           →  "10AM–1PM"   (already clean, returned as-is)
      "8:00 AM • 9:30 AM"  →  "8AM–9:30AM"
    Returns the raw string if the pattern does not match.
    """
    if not isinstance(raw, str):
        return "N/A"
    s = raw.strip()
    if s.upper() in ("", "N/A", "NA", "NONE", "NAN"):
        return "N/A"
    match = _TIME_RANGE_RE.search(s)
    if not match:
        return s
    h1, m1, ap1, h2, m2, ap2 = match.groups()
    return f"{_fmt_part(h1, m1, ap1)}–{_fmt_part(h2, m2, ap2)}"


# ── Sweep schedule display ────────────────────────────────────────────────────

# Weekday display forms, Mon=0..Sun=6.
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Bare week-of-month ordinal runs (digits 1–5 only) -> friendly ordinals:
#   "13" -> "1st & 3rd", "135" -> "1st, 3rd & 5th", "2" -> "2nd".
# Restricted to 1–5 so it never mangles other numbers (years, "10", …); a digit
# carrying a letter suffix ("1st") is skipped by the word-boundary guards.
_BARE_ORD_RE = re.compile(r"(?<!\w)([1-5]{1,5})(?!\w)")
_ORD_SUFFIX = {"1": "st", "2": "nd", "3": "rd"}


def pretty_ordinals(text: str) -> str:
    """Bare week-of-month digit runs -> ordinals: "13" -> "1st & 3rd"."""
    def _repl(m: "re.Match") -> str:
        parts = [d + _ORD_SUFFIX.get(d, "th") for d in m.group(1)]
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " & " + parts[-1]
    return _BARE_ORD_RE.sub(_repl, text)


# ── House number ──────────────────────────────────────────────────────────────

_NUM_SEP_RE = re.compile(r"[-;,/\s]")


def house_number(raw: str) -> int | None:
    """
    Parse the first integer from a house-number field.

    Handles ranges like "1703;1711", "6321-6323", "100 A", plain "2211".
    Returns None if no integer can be extracted.
    """
    if not raw:
        return None
    try:
        return int(_NUM_SEP_RE.split(raw.strip())[0])
    except (ValueError, TypeError):
        return None
