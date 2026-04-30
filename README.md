# Australian Census Augmentation Tool

Python CLI that augments Australian location datasets with ABS Census data at the SA2 statistical area level.

> **Status:** Early development. Project skeleton and design specification are in place; implementation is in progress.

## What it does

Takes a CSV of Australian locations — addresses, coordinates, or a mix per row — and produces an enriched CSV with selected ABS Census variables attached for the SA2 statistical area each location falls within.

```
Input CSV  →  Geocoding  →  Spatial Join  →  Census Enrichment  →  Output CSV
              (Nominatim)   (SA2 polygons)   (DataPack lookup)
```

Geocoded addresses, ASGS boundary files, and Census DataPacks are cached locally so re-runs are fast.

## Requirements

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip` + `venv`

## Installation

```bash
uv pip install -e ".[dev]"
```

## Usage

> **Not yet implemented.** The commands below are the planned interface per [`spec.md`](spec.md) §11.

```bash
# Augment a dataset
census-augment run --config config.yaml

# Discover variables by keyword or table
census-augment discover --search "income"
census-augment discover --table G02

# Pre-fetch ABS data
census-augment fetch --boundaries --census

# Validate a config without running the pipeline
census-augment validate --config config.yaml
```

## Project layout

See [`spec.md`](spec.md) §5 for the full tree.

## Documentation

- [`spec.md`](spec.md) — full specification; **the source of truth for design decisions**.
- [`CLAUDE.md`](CLAUDE.md) — contributor conventions and tooling notes.

## Development

```bash
pytest                            # Run tests
ruff check . && ruff format .     # Lint and format
mypy src/                         # Type check
```

## Data sources

The tool downloads ABS data on first run. Nothing is committed to the repo — see [`data/README.md`](data/README.md) and [`cache/README.md`](cache/README.md).

## License

MIT — see [`LICENSE`](LICENSE).
