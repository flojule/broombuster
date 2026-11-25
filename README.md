# BroomBuster

Street-sweeping map for the San Francisco Bay Area and Chicago ([sources](data/README.md#sources)).

## Run

```bash
uv sync --locked --all-extras
./run.sh        # http://localhost:8000
```

Before a UI change, run the layout and accessibility check locally (not in CI, not enforced):
`uv run --extra visual python scripts/visual_scenarios.py` (screenshots in `build/visual/`).

## Docs

- [Requirements](docs/requirements.md)
- [Architecture (diagrams)](docs/architecture.md)
- [Dependencies (diagrams)](docs/dependencies.md)
- [Data](data/README.md)

## License

MIT (`LICENSE`). Vendored libraries: [frontend/vendor/README.md](frontend/vendor/README.md).
