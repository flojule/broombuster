from datetime import datetime


class Car:
    def __init__(self, lat=37.84609980886195, lon=-122.25964399184454):
        self.name = 'my car'
        self._city: str | None = None  # set after construction by the caller
        # Street info — set by the caller from the resolved segment.
        self.street_name = None
        self.street_number = None
        self.set_location(lat, lon)

    def set_location(self, lat, lon, time=None):
        if time is None:
            time = datetime.now()
        self.lat = lat
        self.lon = lon
        self.time = time

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
