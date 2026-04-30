# Census Augmentation Tool

Python CLI tool that augments Australian location datasets with ABS Census data at the SA2 statistical area level.

---

## Source of Truth

**`spec.md` is the source of truth for all design decisions.**

- Always consult `spec.md` before making implementation choices.
- If something isn't covered in `spec.md`, ask the user before assuming — do not invent behavior.
- If a design change is needed, update `spec.md` first, then update the code to match. Never let the code diverge silently from the spec.
- The "Resolved Decisions" section of `spec.md` (§14) explains *why* certain choices were made — read it before challenging them.

---

## Tech Stack

- Python 3.11+
- Package + env management: `uv` (preferred) or `pip + venv`
- Config: `pydantic` v2 + `pyyaml`
- CLI: `typer`
- Spatial: `geopandas`, `shapely`, `pyproj`
- Tabular: `pandas`
- HTTP: `requests`
- Excel metadata: `openpyxl`
- Progress: `tqdm`
- Testing: `pytest`, with `responses` (or `pytest-httpx`) for HTTP mocking

---

## Commands

```bash
# Install (editable + dev extras)
uv pip install -e ".[dev]"

# Run tests
pytest

# Lint + format
ruff check .
ruff format .

# Type check
mypy src/

# Run the tool
census-augment run --config config.yaml

# Discover variables
census-augment discover --search "income"
```

---

## Conventions

- **Type hints everywhere.** `mypy` should pass before committing.
- **Pydantic models** for config and any structured intermediate data.
- **Tests next to features.** Every new module gets a corresponding `tests/test_<module>.py`. No new logic merges without a test.
- **Mock all network calls in tests.** Never hit ABS or Nominatim from the test suite.
- **Small functions, pure where possible.** Side effects (I/O, network) live at the edges.
- **Errors should be loud and helpful.** Use Pydantic validation errors with context, not bare exceptions.
- **Logging over print.** Use the `logging` module; the CLI configures handlers.

---

## What Not To Do

- Do **not** check in anything under `data/` or `cache/`. Per-folder `.gitignore` files handle this; do not override them.
- Do **not** commit secrets, API keys, or any user data files.
- Do **not** implement features that aren't in `spec.md` without asking the user first.
- Do **not** skip writing tests for new logic.
- Do **not** hit live ABS or Nominatim endpoints in unit tests. Use fixtures and mocks.
- Do **not** bypass the variable resolver in `catalog.py` to access DataPack files directly from other modules.
- Do **not** parse the ABS download HTML pages. Filenames are constructed deterministically from config (see `spec.md` §4).

---

## Project Layout

See `spec.md` §5 for the full tree. Key entry points:

- `src/census_augment/cli.py` — Typer entry point
- `src/census_augment/config.py` — Pydantic config models + loader
- `src/census_augment/pipeline.py` — Orchestrates input → geocode → spatial → enrich → output
- `src/census_augment/catalog.py` — Resolves friendly variable names against DataPack metadata
- `tests/fixtures/` — Tiny sample inputs and mocked DataPack/boundary slices

---

## When Stuck

1. Re-read the relevant section of `spec.md`.
2. Check the "Resolved Decisions" log (§14) — your question may already be answered.
3. Ask the user before guessing about ABS data formats, file structures, or census variable semantics.
