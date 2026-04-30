"""Default cache/data directory resolution (spec §9, §18.3).

Library users get sensible per-platform user-cache locations by default;
CLI users can override via env vars or command-line flags. Same defaults
apply to ``Pipeline.from_config`` and the CLI commands so a single set of
ABS downloads serves every notebook and project on the machine.

Resolution order for both functions:

1. Explicit kwarg / CLI flag (handled by callers — not visible here).
2. ``CENSUS_AUGMENT_DATA_DIR`` / ``CENSUS_AUGMENT_CACHE_DIR`` env var.
3. Platform user cache via :mod:`platformdirs`.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

_APP_NAME = "census-augment"

_ENV_DATA_DIR = "CENSUS_AUGMENT_DATA_DIR"
_ENV_CACHE_DIR = "CENSUS_AUGMENT_CACHE_DIR"


def default_data_dir() -> Path:
    """Default location for downloaded ABS data (boundaries + DataPacks).

    Resolves ``CENSUS_AUGMENT_DATA_DIR`` env var first, then falls back to
    ``<platformdirs.user_cache_dir>/data/`` (e.g. ``~/.cache/census-augment/data/``
    on Linux).
    """
    override = os.environ.get(_ENV_DATA_DIR)
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME)) / "data"


def default_cache_dir() -> Path:
    """Default location for the geocoding cache.

    Resolves ``CENSUS_AUGMENT_CACHE_DIR`` env var first, then falls back to
    ``<platformdirs.user_cache_dir>/cache/``.
    """
    override = os.environ.get(_ENV_CACHE_DIR)
    if override:
        return Path(override)
    return Path(platformdirs.user_cache_dir(_APP_NAME)) / "cache"
