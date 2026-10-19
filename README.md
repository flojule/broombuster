# BroomBuster

Street-sweeping map for Oakland, San Francisco, Berkeley, Alameda and Chicago.

## Run

```bash
uv sync --locked --all-extras
./run.sh        # http://localhost:8000
```

Before a UI change, run the layout and accessibility check locally (not in CI, not enforced):
`uv run --extra visual python scripts/visual_scenarios.py` (screenshots in `build/visual/`).

## Docs

- [Architecture (diagrams)](docs/architecture.md)
- [Dependencies (diagrams)](docs/dependencies.md)
- [Phone setup: report where the car parked](docs/phone-setup.md)
- [Data](data/README.md)

## License

MIT (`LICENSE`). Vendored libraries: [frontend/vendor/README.md](frontend/vendor/README.md).
