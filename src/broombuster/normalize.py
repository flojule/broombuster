"""
Single source of truth for all data normalization in BroomBuster.

Import from here instead of defining normalization locally.
Every comparison of street names, times, and house numbers should go through
these functions so that data from different cities / sources is treated
identically.
"""

import re

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
_DIR_COLLAPSE = {v: k for k, v in _DIR_EXPAND.items()}

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

# Reverse mapping of full directional to short abbreviation.
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

    # Leading directional?
    first_up = toks[0].upper()
    dir_token = None
    rest = toks
    # Match multi-word directionals (NORTHWEST etc.) or abbreviations
    if first_up in _DIR_ABBR:
        dir_token = _DIR_ABBR[first_up]
        rest = toks[1:]
    else:
        # Also match spelled-out variants
        f_up = first_up
        if f_up in _DIR_EXPAND.values():
            dir_token = _DIR_COLLAPSE.get(f_up)
            rest = toks[1:]

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


def _digits_only(s: str | None) -> str | None:
    """Strip whitespace from inside a captured digit group (Alameda PDF artifact)."""
    return re.sub(r"\s+", "", s) if s else s


def _fmt_part(h: str, m: str | None, ap: str) -> str:
    ap = re.sub(r"[\s,]+", "", ap).upper()
    h = _digits_only(h) or h
    m = _digits_only(m)
    mn = int(m) if m else 0
    if mn:
        return f"{int(h)}:{mn:02d}{ap}"
    return f"{int(h)}{ap}"


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
    if s.upper() in ("", "N/A", "NONE", "NAN"):
        return "N/A"
    match = _TIME_RANGE_RE.search(s)
    if not match:
        return s
    h1, m1, ap1, h2, m2, ap2 = match.groups()
    return f"{_fmt_part(h1, m1, ap1)}–{_fmt_part(h2, m2, ap2)}"


# ── Sweep schedule display ────────────────────────────────────────────────────

# Canonical weekday tokens. Source data mixes forms (Tue/Tues, Thu/Thurs); we
# unify every surface to Mon/Tue/Wed/Thu/Fri/Sat/Sun. Value = (Mon..Sun rank,
# display form).
_WEEKDAY_CANON = {
    "MON": (0, "Mon"), "MONDAY": (0, "Mon"),
    "TUE": (1, "Tue"), "TUES": (1, "Tue"), "TUESDAY": (1, "Tue"),
    "WED": (2, "Wed"), "WEDS": (2, "Wed"), "WEDNESDAY": (2, "Wed"),
    "THU": (3, "Thu"), "THUR": (3, "Thu"), "THURS": (3, "Thu"), "THURSDAY": (3, "Thu"),
    "FRI": (4, "Fri"), "FRIDAY": (4, "Fri"),
    "SAT": (5, "Sat"), "SATURDAY": (5, "Sat"),
    "SUN": (6, "Sun"), "SUNDAY": (6, "Sun"),
}

_EVERY_PAREN_RE = re.compile(r"\s*\(every\)", re.IGNORECASE)
_OF_MONTH_RE = re.compile(r"\s*\bof\s+(?:the\s+)?month\b", re.IGNORECASE)
_AND_RE = re.compile(r"\band\b", re.IGNORECASE)
_TRIM_EDGE_RE = re.compile(r"^[\s,]+|[\s,]+$")


def _weekday_first(desc: str) -> str:
    """Move a trailing/embedded weekday token to the front, canonicalized.

    "1st & 3rd Wed" -> "Wed 1st & 3rd"; "Mon 1st & 3rd" -> "Mon 1st & 3rd";
    "Every Wed" -> "Every Wed". Leaves descriptors with no weekday untouched.
    """
    toks = desc.split()
    if not toks:
        return desc
    every = toks[0].lower() == "every"
    body = toks[1:] if every else toks
    wd_idx = None
    for i, t in enumerate(body):
        if t.strip(".,").upper() in _WEEKDAY_CANON:
            wd_idx = i
            break
    if wd_idx is None:
        return desc
    disp = _WEEKDAY_CANON[body[wd_idx].strip(".,").upper()][1]
    rest = body[:wd_idx] + body[wd_idx + 1:]
    parts = (["Every"] if every else []) + [disp] + rest
    return " ".join(parts).strip()


def sweep_body(desc: str, time: str = "") -> str:
    """Canonical schedule line from a raw desc + time, e.g. "Wed 1st & 3rd, 9AM-12PM".

    Rules (unified across card, hover, zone popup): drop "(every)" and
    "of month", "and" -> "&", weekday first, time via time_display (minutes
    only when non-zero) appended once. Returns "" for empty input.
    """
    d = desc if isinstance(desc, str) else ""
    d = _EVERY_PAREN_RE.sub("", d)
    d = _TIME_RANGE_RE.sub("", d)          # drop time embedded in desc (SF)
    d = _OF_MONTH_RE.sub("", d)
    d = _AND_RE.sub("&", d)
    d = _WHITESPACE_RE.sub(" ", d)
    d = _TRIM_EDGE_RE.sub("", d)           # strip dangling commas/space
    d = _WHITESPACE_RE.sub(" ", d).strip()
    d = _weekday_first(d)
    if not d or d.upper() == "N/A":
        return ""
    t = time_display(time or "")
    if t in ("", "N/A") or t in d:
        return d
    return f"{d}, {t}"


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


def car_side(number: int | None) -> str:
    """Return 'even' or 'odd' based on a street number (defaults to 'odd' when unknown)."""
    return "even" if (number and number % 2 == 0) else "odd"
