# Makefile for abs-census-augmentor.
#
# Run `make` (no args) for a list of available targets.
#
# Conventions:
#   * Default target is `help` — running `make` with no args always
#     gives you something useful.
#   * Every target goes through `uv run ...`; no venv activation
#     needed (uv resolves .venv/ and adds it to PATH for the
#     subprocess). uv overhead is microseconds.
#   * POSIX/bash assumptions — targets use `rm -rf`, `find`, `||`,
#     pipes, etc. Windows users: open this repo in the
#     `.devcontainer/` (`Dev Containers: Reopen in Container` from
#     VSCode) or in WSL. Raw Windows cmd.exe / PowerShell won't have
#     `make`.
#
# Help text is parsed from `## ...` doc comments after each target,
# and `##@ Section` lines act as section headers. Add a new target,
# document it inline with `##`, and it shows up in `make help`
# automatically.

.DEFAULT_GOAL := help

.PHONY: help \
        install clean clean-all \
        test test-fast lint format typecheck check \
        smoke verify-real \
        demo demos check-readme-frames \
        build build-test

# ---- help ---------------------------------------------------------------

help: ## Show this help
	@echo "Usage: make <target>"
	@awk 'BEGIN{FS=":.*## "} \
	     /^##@ / {printf "\n%s:\n", substr($$0, 5); next} \
	     /^[a-zA-Z_-]+:.*## / {printf "  %-22s %s\n", $$1, $$2}' \
	     $(MAKEFILE_LIST)

##@ Setup

install: ## Install project + dev deps into .venv/
	uv sync --all-extras

clean: ## Remove caches and build artefacts (keeps .venv/)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage \
	       dist build src/*.egg-info
	rm -f tools/demo/output.csv tools/demo/preset-output.csv
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +

clean-all: clean ## clean + remove .venv/ (full reset)
	rm -rf .venv

##@ Test & quality

test: ## Run hermetic pytest suite
	uv run pytest

test-fast: ## pytest -x --ff (fail fast, failed-first)
	uv run pytest -x --ff

lint: ## ruff check .
	uv run ruff check .

format: ## ruff format . (writes files)
	uv run ruff format .

typecheck: ## mypy src/ tools/ tests/
	uv run mypy src/ tools/ tests/

check: lint typecheck test ## lint + typecheck + test (CI-equivalent)

##@ Smoke & real-data

smoke: ## Quick wire-up check (CLI, registries, PRESET specs)
	@uv run census-augment --help >/dev/null && echo "==> CLI: ok"
	@uv run python -c "from census_augment.datasets import registry; \
from census_augment.features import features; \
ds = registry.list_datasets(); \
ft = features.list_features(); \
print(f'==> Datasets: {len(ds)} registered ({\", \".join(s.id for s in ds)})'); \
print(f'==> Features: {len(ft)} PRESETs'); \
[print(f'    - {s.id}: {len(s.source_fields())} source refs') for s in ft]"

verify-real: ## Real-data parser check (hits live ABS endpoints)
	uv run python tools/verify_real_parsers.py

##@ Demos

demo: ## Render docs/demo.gif AND refresh its README scene-strip
	./tools/demo/render.sh
	uv run python tools/demo/refresh_readme_frames.py

demos: ## Render every tape AND refresh README scene-strips
	./tools/demo/render.sh --all
	uv run python tools/demo/refresh_readme_frames.py

check-readme-frames: ## Fail if README.md scene-strips are stale (CI lint)
	uv run python tools/demo/refresh_readme_frames.py --check

##@ Build

build: ## Build the wheel
	uv build --wheel

build-test: build ## Build wheel + run wheel-install regression test
	WHEEL_E2E=1 uv run pytest tests/test_wheel_bundles_specs.py
