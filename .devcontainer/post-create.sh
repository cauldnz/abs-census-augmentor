#!/usr/bin/env bash
# Devcontainer post-create: install uv, sync the project, smoke-test.
#
# Idempotent — safe to re-run. The devcontainer feature for Python
# already gives us 3.11; we add uv (project tool) and run uv sync to
# materialise the .venv with all dev deps.

set -euo pipefail

echo "==> Installing uv (Python package manager)..."
if ! command -v uv >/dev/null 2>&1; then
    # Astral's official installer. Pinned to a recent version because
    # uv's behaviour around lockfiles has churned; locking the version
    # keeps the devcontainer reproducible across rebuilds.
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer writes to ~/.local/bin which is on PATH for the
    # vscode user.
    export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

echo "==> Syncing project dependencies into .venv/ ..."
# `--all-extras` pulls in [dev] (pytest, ruff, mypy, responses, moto, ...).
# This populates .venv/ at the workspace root. VSCode picks it up via
# python.defaultInterpreterPath in devcontainer.json.
uv sync --all-extras

echo "==> Verifying the install (quick smoke test)..."
uv run census-augment --help >/dev/null
uv run python -c "
from census_augment.datasets import registry
from census_augment.features import features
print(f'  datasets: {len(registry.list_datasets())} registered')
print(f'  features: {len(features.list_features())} PRESETs')
"

# A few project-specific quality-of-life touches:
# - Tell git this directory is safe (devcontainer mounts can otherwise
#   trigger 'dubious ownership' warnings).
git config --global --add safe.directory "$(pwd)"

echo
echo "==> Devcontainer ready."
echo "   Run 'uv run pytest' to verify the full suite."
echo "   Run 'tools/demo/render.sh' to re-render the README demo (uses host Docker)."
