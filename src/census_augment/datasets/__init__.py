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

# The single process-wide registry instance. Built from the on-disk
# spec markdown files; fetchers are bound below by importing each
# built-in dataset module (each module's tail calls
# `registry.register_fetcher(...)`).
registry = Registry.from_repo_specs()

# Import each built-in fetcher module so its module-level
# `register_fetcher` call runs. This intentionally happens after
# `registry = Registry.from_repo_specs()` so the registration is
# attached to the canonical singleton. Suppressed-unused-import lint:
# the modules' side-effect is the whole point.
from . import _abs_ba as _abs_ba  # noqa: F401, E402
from . import _abs_ba_lga as _abs_ba_lga  # noqa: F401, E402
from . import _abs_pia as _abs_pia  # noqa: F401, E402
from . import _aihw_apc as _aihw_apc  # noqa: F401, E402
from . import _aihw_cmh as _aihw_cmh  # noqa: F401, E402
from . import _aihw_ed as _aihw_ed  # noqa: F401, E402
from . import _aihw_medicare as _aihw_medicare  # noqa: F401, E402
from . import _aihw_mh as _aihw_mh  # noqa: F401, E402
from . import _dss as _dss  # noqa: F401, E402
from . import _erp as _erp  # noqa: F401, E402
from . import _seifa as _seifa  # noqa: F401, E402

__all__ = [
    "DatasetFetcher",
    "DatasetSpec",
    "Registry",
    "VariableSpec",
    "registry",
]
