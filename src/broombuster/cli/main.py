import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from broombuster import car, data_loader, email_alerts, maps
from broombuster.cities import CITIES, REGIONS
from broombuster.domains import for_city

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Regional mode: load all cities in a region together.
# Available regions: "bay_area", "chicago"  (see src/cities.py → REGIONS)
REGION = "bay_area"

# Set SINGLE_CITY_MODE = True to load only the city named in CITY below.
# Useful while developing or when other cities' data files aren't available yet.
SINGLE_CITY_MODE = False

# City used when SINGLE_CITY_MODE = True.
# Available keys: "oakland", "san_francisco", "berkeley", "alameda", "chicago_all"
CITY = "oakland"

# Manual location override (uses region manual_default when None).
# Set to your car's position to override.
# bay_area example: 37.821326, -122.280705  (2931 Chestnut St, Oakland)
# chicago example:  41.996593,   -87.665282     (near N Glenwood Ave)
MANUAL_LAT = None
MANUAL_LON = None

PLOT              = True   # Open an interactive map in the browser
SEND_NOTIFICATION = False  # Send an email when sweeping is today or tomorrow
CHECK_INTERVAL_H  = 1     # Hours between checks when running continuously

# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BroomBuster — street-sweeping alert")
    p.add_argument("--region", default=None, choices=list(REGIONS.keys()),
                   help="Region to load (default: value of REGION above)")
    p.add_argument("--city", default=None, choices=list(CITIES.keys()),
                   help="City used with --single (default: value of CITY above)")
    p.add_argument("--single", action="store_true", default=False,
                   help="Load only the city named by --city")
    p.add_argument("--lat", type=float, default=None, metavar="LAT",
                   help="Manual latitude override")
    p.add_argument("--lon", type=float, default=None, metavar="LON",
                   help="Manual longitude override")
    p.add_argument("--no-plot", action="store_true", default=False,
                   help="Skip the interactive map")
    p.add_argument("--notify", action="store_true", default=False,
                   help="Send email when sweeping is today or tomorrow")
    p.add_argument("--loop", action="store_true", default=False,
                   help="Run continuously, sleeping CHECK_INTERVAL_H hours between checks")
    p.add_argument("--refresh", action="store_true", default=False,
                   help="Force re-download of cached city data files")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # CLI arguments override module-level defaults
    _region     = args.region or REGION
    _city       = args.city   or CITY
    _single     = args.single or SINGLE_CITY_MODE
    _manual_lat = args.lat if args.lat is not None else MANUAL_LAT
    _manual_lon = args.lon if args.lon is not None else MANUAL_LON
    _plot       = PLOT and not args.no_plot
    _send_notif = args.notify or SEND_NOTIFICATION

    if _single:
        city_cfg = CITIES[_city]
        myCity   = data_loader.load_city_data(_city, force_refresh=args.refresh)
        myCity["_city"] = _city
        default_lat = city_cfg["manual_default"]["lat"]
        default_lon = city_cfg["manual_default"]["lon"]
    else:
        region_cfg  = REGIONS[_region]
        myCity      = data_loader.load_region_data(_region, force_refresh=args.refresh)
        _rdefault   = region_cfg.get("manual_default", region_cfg["center"])
        default_lat = _rdefault["lat"]
        default_lon = _rdefault["lon"]

    lat = _manual_lat if _manual_lat is not None else default_lat
    lon = _manual_lon if _manual_lon is not None else default_lon

    # Project once; the resolver expects EPSG:3857. Reusing the same object
    # across loop iterations keeps the name-index cache hot (keyed by id(gdf)).
    myCity_3857 = myCity.to_crs("EPSG:3857")

    myCar = car.Car(lat=lat, lon=lon)

    # City key closest to the car — restricts cross-city name collisions.
    def _nearest_city(lat, lon):
        active = [_city] if _single else REGIONS[_region]["cities"]
        best, best_d = active[0], float("inf")
        for ck in active:
            c = CITIES[ck]["center"]
            d = (c["lat"] - lat) ** 2 + (c["lon"] - lon) ** 2
            if d < best_d:
                best, best_d = ck, d
        return best

    def _region_of(city_key):
        for rk, rv in REGIONS.items():
            if city_key in rv["cities"]:
                return rk
        return _region

    try:
        while True:
            myCar.set_location(lat, lon)
            city_key = _nearest_city(myCar.lat, myCar.lon)
            myCar._city = city_key
            tz = REGIONS.get(_region_of(city_key), {}).get("tz", "UTC")
            local_now = datetime.now(ZoneInfo(tz))

            # Same resolver + sweeping plugin the /check endpoint uses.
            sweeping = next(
                (p for p in for_city(city_key) if p.domain_id == "sweeping"), None
            )
            if sweeping is None:
                print(f"No sweeping data for {CITIES[city_key]['name']}.")
                break
            resolved = sweeping.resolve_for(myCity_3857, myCar.lat, myCar.lon, city_key)
            result = sweeping.format(resolved, myCity_3857, local_now)
            if resolved is not None:
                myCar.street_name = resolved.street_display or resolved.street_name
            schedule_even = result.extras.get("schedule_even", [])
            schedule_odd = result.extras.get("schedule_odd", [])
            message = result.extras.get("message") or (
                "Car not near a mapped street." if resolved is None else ""
            )
            urgency = result.urgency if result.urgency in ("today", "tomorrow") else False

            print()
            print(myCar)
            print(message)

            if _plot:
                maps.plot_map(myCar, myCity, schedule_even=schedule_even,
                              schedule_odd=schedule_odd, message=message, local_now=local_now)

            if _send_notif and urgency:
                email_alerts.send_email(message, urgency=urgency)

            if not args.loop:
                break
            print(f"\nSleeping {CHECK_INTERVAL_H} h … (Ctrl-C to exit)\n")
            time.sleep(CHECK_INTERVAL_H * 3600)

    except KeyboardInterrupt:
        print("\nExiting…")


if __name__ == "__main__":
    main()
