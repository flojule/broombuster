# BroomBuster

Street-sweeping map for Oakland, San Francisco, Berkeley, Alameda and Chicago.

## Use

1. Open the site and tap the map to drop your car. The card shows when its street is swept.
2. Add more cars, and a home in Oakland for trash pickup days.
3. Sign in with Google to sync cars across devices.
4. Optional: [have your phone update the car's position](docs/phone-setup.md) when you park.

## Run locally

```bash
uv sync --locked --all-extras
./run.sh        # http://localhost:8000
```

Deploying and operating: [docs/operations.md](docs/operations.md).

## License

MIT (`LICENSE`). Vendored libraries keep their own licences
(`frontend/vendor/README.md`); city data comes from each city's open-data portal.
