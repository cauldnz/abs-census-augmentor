# Development

← [back to docs index](index.md)

## Make targets

A `Makefile` wraps the common workflows. From the repo root:

```bash
make                      # list all targets
make install              # uv sync --all-extras
make smoke                # quick wire-up check (CLI + registries + PRESET specs)
make check                # lint + typecheck + test (CI-equivalent)
make test                 # hermetic pytest suite
make verify-real          # opt-in real-data check (hits live ABS endpoints)
make demos                # render every README demo GIF (+ refresh README scene strips)
make check-readme-frames  # fail if README scene strips are stale (CI lint)
make build                # build the wheel
```

If you'd rather skip Make, the underlying commands work too:

```bash
uv run pytest                     # 500+ hermetic tests; no real network
uv run ruff check . && uv run ruff format .     # Lint + format
uv run mypy src/ tools/           # Strict type check
```

The full suite is hermetic — every external interaction (Nominatim, ABS) is mocked. To validate against the live ABS endpoints, use the opt-in scripts in [`../tools/`](../tools/) (or `make verify-real`).

## Dev container (recommended)

This repo ships a [VSCode Dev Container](../.devcontainer/) that gives you a Linux Python 3.11 sandbox with `uv`, `gh`, `make`, the dev deps, and a native VHS install for demo rendering. Open the repo in VSCode and run `Dev Containers: Reopen in Container` — first build takes ~3-5 minutes, subsequent attaches are seconds. See [`../.devcontainer/README.md`](../.devcontainer/README.md) for details.

The dev container is what CI runs on, so `pytest` / `ruff` / `mypy` results inside the container match what gates PRs.

> **Windows users**: `make` doesn't ship with Windows by default and the Makefile uses POSIX/bash conventions. Open the dev container (or WSL) for the `make` targets, or fall back to `uv run ...` directly from PowerShell.

## Real-data verification

The hermetic test suite mocks ABS, Nominatim, and S3. For a separate check against live endpoints — useful when adding a new dataset / parser, or when investigating upstream drift — run:

```bash
make verify-real
```

This is the harness referenced by the "Real Data First" rule in [`../CLAUDE.md`](../CLAUDE.md). See [`../tools/README.md`](../tools/README.md) for the full list of probes.

## Project layout

See [`../spec.md` §5](../spec.md) for the full tree. Key entry points:

- `src/census_augment/cli.py` — Typer entry point (CLI commands)
- `src/census_augment/pipeline.py` — orchestrates input → geocode → spatial → enrich → output.
- `src/census_augment/__init__.py` — public library API surface.
- `src/census_augment/config.py` — Pydantic config models + YAML loader.
- `src/census_augment/catalog.py` — resolves friendly variable names against DataPack metadata.
- `tests/conftest.py` — shared fixtures (synthetic SA2 polygons + DataPack ZIP).
- `tools/` — scripts for verifying parsers against real ABS data.

## Contributing

PRs welcome. House rules:

- Read [`../CLAUDE.md`](../CLAUDE.md) — it codifies the "Real Data First" rule and other conventions.
- `make check` must pass.
- New logic gets a test.
- External schemas (column names, file paths, response shapes) come from a fixture / re-fetch script / `verify-real` probe — never from intuition. See the "Real Data First" section in `CLAUDE.md`.
