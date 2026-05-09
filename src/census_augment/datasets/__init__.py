"""Pluggable dataset registry (spec §20).

The pipeline's enrichment stage looks up registered datasets via this
module rather than hard-coding the GCP DataPack. Each dataset is
described by a markdown spec file (``datasets/<id>.md``) at the repo
root with YAML front-matter; the registry parses those files on first
access and indexes them by `id` and `namespace`.

Public API:

- :class:`DatasetSpec` — Pydantic model of the spec front-matter +
  parsed schema table.
- :class:`DatasetFetcher` — Protocol every dataset's fetcher
  implements: ``fetch(refresh) -> Path`` and
  ``load() -> pd.DataFrame`` (indexed by SA2 code).
- :class:`Registry` — singleton registry instance accessible as
  ``census_augment.datasets.registry``. Resolves variables, lists
  datasets, gets fetchers.

Spec files are at the repo root under ``datasets/`` and indexed at
import time. Custom datasets can be registered programmatically via
``registry.register_spec(...)``.
"""

from __future__ import annotations

from ._protocol import DatasetFetcher
from ._registry import Registry
from ._spec import DatasetSpec, VariableSpec

# The single process-wide registry instance.
registry = Registry.from_repo_specs()

__all__ = [
    "DatasetFetcher",
    "DatasetSpec",
    "Registry",
    "VariableSpec",
    "registry",
]
