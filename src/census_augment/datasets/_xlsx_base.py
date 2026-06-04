"""Shared plumbing for the SEIFA / ERP / DSS / ABS Personal Income XLSX datasets.

The four single-file XLSX datasets all share an identical skeleton:

- Lazy release resolution (static URL for SEIFA, landing-page scrape
  for ERP / ABS Personal Income, CKAN lookup for DSS) populating
  ``self._resolved_release`` and ``self._resolved_url``.
- Streaming download to a ``.tmp`` file with atomic-rename, gated on
  ``_xlsx_path.exists()`` for cache hits, retrying transient ABS
  failures via :mod:`census_augment._http_retry`.
- Parquet sidecar pattern: parse the XLSX once, cache the result as
  ``<stem>.parquet``, read parquet on subsequent ``load()`` calls.

The only dataset-specific bits are:

- The filename stem (e.g. ``seifa-2021``, ``erp-sa2-2024``).
- The release-resolution mechanism (overrides ``_resolve_release``).
- The XLSX parser (overrides ``_parse_xlsx``).
- Optional per-release columns the parser doesn't produce (DSS adds
  ``release_quarter``, ABS Personal Income adds
  ``reference_financial_year`` — overrides ``_post_parse``).

Before this base existed each of `_seifa.py` / `_erp.py` / `_dss.py` /
`_abs_pia.py` reimplemented the plumbing inline, producing four ~330-line
modules where ~70% of the code was identical. Pulling the shared base
collapses them to ~150 lines each, all dataset-specific.

The pre-existing :class:`DatasetFetcher` Protocol (see
``datasets/_protocol.py``) is still the contract subclasses
implement. This base is an implementation convenience, not a part of
the public registry contract — subclasses with a fundamentally
different shape (e.g. a parquet-native source, or one that fans out
across multiple files) can implement the protocol directly without
inheriting from this base.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import pandas as pd
import requests

from .._http_retry import retry_stream_get

_log = logging.getLogger(__name__)


class _AbsXlsxDataset:
    """Shared fetch / load plumbing for the four ABS XLSX datasets."""

    #: Human-readable label for log messages.
    _label: ClassVar[str] = ""

    #: Glob pattern used to detect any cached release on disk before
    #: ``resolved_release`` is known. Subclasses override.
    _cache_glob: ClassVar[str] = "*.xlsx"

    def __init__(
        self,
        *,
        release: str = "latest",
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 256 * 1024,
        timeout: float = 60.0,
    ) -> None:
        self._release_request = str(release)
        self._root = Path(root)
        self._session = session if session is not None else requests.Session()
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._resolved_release: str | None = None
        self._resolved_url: str | None = None
        #: Name of the SA2 code column in the parsed DataFrame; used as
        #: the parquet sidecar's index.  Subclasses can override in their
        #: own ``__init__`` (e.g. SEIFA sets this per-release to reflect
        #: the ASGS edition: ``sa2_code_2016`` vs ``sa2_code_2021``).
        self._sa2_index_name: str = "sa2_code_2021"

    # ---- DatasetFetcher protocol --------------------------------------

    @property
    def resolved_release(self) -> str:
        """Concrete release identifier — resolves lazily on first access."""
        if self._resolved_release is None:
            self._resolve_release()
        assert self._resolved_release is not None
        return self._resolved_release

    @property
    def is_cached(self) -> bool:
        """True if some cached XLSX exists on disk.

        When the release has already been resolved we check the
        specific file; otherwise we fall back to the glob pattern so
        callers can probe cache state without paying the
        release-resolution cost (which is a network round-trip for
        most subclasses).
        """
        if self._resolved_release is not None:
            return self._xlsx_path.exists()
        return self._root.exists() and any(self._root.glob(self._cache_glob))

    @property
    def _xlsx_path(self) -> Path:
        return self._root / f"{self._filename_stem(self.resolved_release)}.xlsx"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"{self._filename_stem(self.resolved_release)}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Resolve release if needed, download XLSX if not cached.

        Returns the local XLSX path. Retries transient ABS failures
        per :mod:`census_augment._http_retry`.
        """
        self._resolve_release()
        if self._xlsx_path.exists() and not refresh:
            _log.debug("%s cached at %s", self._label, self._xlsx_path)
            return self._xlsx_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._xlsx_path.with_suffix(self._xlsx_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info(
            "Downloading %s (%s) from %s",
            self._label,
            self.resolved_release,
            url,
        )
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label=self._label,
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._xlsx_path)
        _log.info("Saved %s to %s", self._label, self._xlsx_path)
        return self._xlsx_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by SA2 code.

        Caches the parsed parquet alongside the XLSX so repeat
        ``load()`` calls are cheap (XLSX parsing is the slow part;
        parquet read is instant). Subclasses with release-derived
        columns hook into ``_post_parse``.
        """
        if self._resolved_release is not None and self._parquet_path.exists():
            return pd.read_parquet(self._parquet_path).set_index(self._sa2_index_name)

        xlsx = self.fetch()
        df = self._parse_xlsx(xlsx)
        df = self._post_parse(df)
        df.reset_index().to_parquet(self._parquet_path, index=False)
        return df

    # ---- Hooks subclasses override ------------------------------------

    def _filename_stem(self, release: str) -> str:
        """Return the on-disk basename (without extension) for ``release``."""
        raise NotImplementedError("subclass must override _filename_stem")

    def _resolve_release(self) -> None:
        """Populate ``self._resolved_release`` and ``self._resolved_url``.

        Idempotent — early-exit when already resolved. Static-URL
        subclasses (e.g. SEIFA) can set both attributes in their
        ``__init__`` and leave this as a no-op via the early return.
        """
        raise NotImplementedError("subclass must override _resolve_release")

    def _parse_xlsx(self, xlsx_path: Path) -> pd.DataFrame:
        """Parse the XLSX into a DataFrame indexed by SA2 code."""
        raise NotImplementedError("subclass must override _parse_xlsx")

    def _post_parse(self, df: pd.DataFrame) -> pd.DataFrame:
        """Inject release-derived columns into the parsed DataFrame.

        Default: no-op. DSS adds ``release_quarter`` here; ABS Personal
        Income adds ``reference_financial_year``. SEIFA / ERP don't need
        this hook because their parsers produce all columns directly.
        """
        return df
