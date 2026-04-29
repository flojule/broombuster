"""
Static-scan regression test: prevent the dual-source address bug from
returning to the frontend.

After Step 1 of the re-architecture, the canonical address is produced by
the backend and rendered verbatim by the frontend. Several patterns must
not reappear in `frontend/index.html`:

  - `car.address = ...`         — writes to a client-side address field
  - `reverseGeocode(`            — client-side reverse geocoding
  - `car.address || sched`       — the dual-source fallback chain

The forward-geocode (typed-address-to-coordinates) Nominatim call is
allowed: it converts user input into lat/lon to move the pin, and does
not write `car.address`.
"""
import os
import re

_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


def _read():
    with open(_FRONTEND, encoding="utf-8") as f:
        return f.read()


def test_no_car_address_writes():
    """`car.address = ...` must not appear — address is server-derived."""
    src = _read()
    matches = re.findall(r"\bcar\.address\s*=", src)
    assert not matches, (
        "car.address writes found in frontend — Step 1 forbids client-side "
        "address state. Render `sched.address` from /check verbatim instead."
    )


def test_no_reverse_geocode_function():
    """`reverseGeocode(` must not appear — the backend produces the address."""
    src = _read()
    assert "reverseGeocode(" not in src, (
        "Frontend reverseGeocode() found — Step 1 moved reverse geocoding "
        "to the backend (see api/api.py and gps.maybe_house_number)."
    )


def test_no_dual_source_address_fallback():
    """The `car.address || sched?.address` chain must not reappear."""
    src = _read()
    # Match either ordering, with or without optional chaining.
    pattern = re.compile(
        r"car\.address\s*\|\|\s*sched|sched\??\.address\s*\|\|\s*car\.address"
    )
    assert not pattern.search(src), (
        "Dual-source address fallback found — Step 1 forbids mixing "
        "client-side and server-side address strings."
    )


def test_nominatim_reverse_endpoint_absent():
    """The Nominatim /reverse endpoint must not be called from the frontend."""
    src = _read()
    assert "nominatim.openstreetmap.org/reverse" not in src, (
        "Nominatim reverse-geocode endpoint found in frontend — only the "
        "/search endpoint (forward geocode for typed-address input) is allowed."
    )
