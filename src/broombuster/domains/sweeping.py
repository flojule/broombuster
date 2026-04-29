"""Street-sweeping domain — output helpers.

In Step 3 this module gains a SweepingPlugin class and becomes the first
DomainPlugin in the registry. For now it holds only the plain-text message
composer that was previously in `src/notification.py`. The composer is
sweeping-specific (treats schedule_even/schedule_odd as two parallel
side-of-street schedules, highlights the car's side with a leading ►), so
it lives next to the future plugin rather than in a generic email module.

The function is re-exported from `src/notification.py` for backward
compatibility with existing call sites and tests; new code should import
from here directly.
"""


def compose_message(schedule_even, schedule_odd, car_side):
    """Return a plain-text schedule summary matching the map info panel layout."""

    def _dedup_parts(entries):
        valid = [e for e in entries if e and len(e) >= 3]
        seen, parts = set(), []
        for entry in valid:
            key = (entry[1], entry[2])
            if key not in seen:
                t = entry[2]
                body = entry[1] if not t else f"{entry[1]} — {t}"
                parts.append(body)
                seen.add(key)
        return parts

    def _fmt_plain(parts, label, highlight):
        prefix = "►" if highlight else " "
        if not parts:
            return f"{prefix} {label}: no sweeping"
        return f"{prefix} {label}: {' / '.join(parts)}"

    even_parts = _dedup_parts(schedule_even)
    odd_parts  = _dedup_parts(schedule_odd)

    if even_parts and even_parts == odd_parts:
        return f"► Street: {' / '.join(even_parts)}"

    even_line = _fmt_plain(even_parts, "Even side", highlight=(car_side == "even"))
    odd_line  = _fmt_plain(odd_parts,  "Odd side",  highlight=(car_side == "odd"))
    return f"{even_line}\n{odd_line}"
