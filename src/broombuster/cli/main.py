"""Command-line street-sweeping check — the same locate + sweeping plugin as /check.

    python -m broombuster.cli.main --lat 37.8213 --lon -122.2807
    python -m broombuster.cli.main --region chicago --notify --loop
"""

import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from broombuster import data_loader, domains, email_alerts, resolve
from broombuster.cities import REGIONS, region_for_point


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BroomBuster — street-sweeping alert")
    p.add_argument("--region", choices=list(REGIONS),
                   help="Region to load (default: nearest to --lat/--lon, else bay_area)")
    p.add_argument("--lat", type=float, help="Car latitude (default: region's manual_default)")
    p.add_argument("--lon", type=float, help="Car longitude")
    p.add_argument("--notify", action="store_true",
                   help="Email when sweeping is today or tomorrow (EMAIL_* in .env)")
    p.add_argument("--loop", action="store_true", help="Re-check every --interval hours")
    p.add_argument("--interval", type=float, default=1.0, help="Hours between --loop checks")
    args = p.parse_args()
    if (args.lat is None) != (args.lon is None):
        p.error("--lat and --lon go together")
    return args


def main() -> None:
    args = _parse_args()
    has_point = args.lat is not None
    region = args.region or (region_for_point(args.lat, args.lon) if has_point else "bay_area")
    point = REGIONS[region].get("manual_default", REGIONS[region]["center"])
    lat, lon = (args.lat, args.lon) if has_point else (point["lat"], point["lon"])

    # Project once; reusing the same frame keeps the per-GDF indexes warm.
    gdf_3857 = data_loader.load_region_data(region).to_crs("EPSG:3857")
    tz = ZoneInfo(REGIONS[region]["tz"])
    sweeping = domains.get("sweeping")

    try:
        while True:
            resolved, _city = resolve.locate(gdf_3857, lat, lon, region)
            result = sweeping.format(resolved, gdf_3857, datetime.now(tz))
            street = resolved.label if resolved else "unknown street"
            print(f"\n{lat:.5f}, {lon:.5f} — {street}")
            print("\n".join(result.schedule_lines))
            if args.notify and result.urgency in ("today", "tomorrow"):
                email_alerts.send_email(f"{street}\n" + "\n".join(result.schedule_lines),
                                        urgency=result.urgency)
            if not args.loop:
                break
            print(f"\nSleeping {args.interval} h … (Ctrl-C to exit)")
            time.sleep(args.interval * 3600)
    except KeyboardInterrupt:
        print("\nExiting…")


if __name__ == "__main__":
    main()
