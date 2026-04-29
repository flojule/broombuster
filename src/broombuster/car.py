from datetime import datetime

import gps


class Car:
    def __init__(self, lat=37.84609980886195, lon=-122.25964399184454):
        self.name = 'my car'
        self._city: str | None = None  # set after construction in main.py
        # Street info — populated by get_info()
        self.street_name = None
        self.street_number = None
        self.streets = []
        self.set_location(lat, lon)

    def set_location(self, lat, lon, time=None):
        if time is None:
            time = datetime.now()
        self.lat = lat
        self.lon = lon
        self.time = time

    def get_info(self):
        """Reverse-geocode current coordinates to get street name/number and nearby streets."""
        try:
            self.street_name, self.street_number = gps.get_street_info(self)
        except Exception as e:
            print(f"Warning: could not fetch street name — {e}")
        try:
            self.streets = gps.get_nearby_streets(self)
        except Exception as e:
            print(f"Warning: could not fetch nearby streets — {e}")

    def __str__(self):
        if self.street_name:
            num = f"{self.street_number} " if self.street_number else ""
            addr = f"{num}{self.street_name}"
        else:
            addr = "unknown address"
        return (
            f"Car location: {self.lat:.5f}, {self.lon:.5f} — "
            f"{addr} (at {self.time.strftime('%Y-%m-%d %H:%M')})"
        )
