"""Common protocol every dataset fetcher implements (spec §20.3).

Mirrors the existing :class:`BoundariesDataSource` / :class:`DataPacksDataSource`
shape: a ``fetch()`` method that ensures data is on disk (downloading
if needed) and a ``load()`` method that returns a DataFrame.

Dataset-specific fetchers (``_gcp``, ``_seifa``, ``_erp``, ``_dss``,
``_ato``) implement this Protocol; the registry indexes by namespace
and the pipeline calls them uniformly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DatasetFetcher(Protocol):
    """Fetch + load a SA2-keyed dataset for the enrichment pipeline."""

    def fetch(self, refresh: bool = False) -> Path:
        """Ensure data is available locally; return the cache path.

        Idempotent. ``refresh=True`` forces re-download when the source
        has new data.
        """
        ...

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021``.

        Columns are the dataset's variables, named per the spec's
        ``namespace.<field>`` convention (the namespace prefix is
        stripped — the registry adds it back when resolving).
        """
        ...

    @property
    def is_cached(self) -> bool:
        """True if local cache is populated (fetch is a no-op)."""
        ...

    @property
    def resolved_release(self) -> str:
        """Release identifier in use (e.g. ``"2021"``, ``"2024-Q4"``).

        Resolved lazily on first call so ``release="latest"`` lookups
        only hit the source once.
        """
        ...
