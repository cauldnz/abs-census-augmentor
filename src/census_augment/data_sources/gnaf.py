"""G-NAF Core data source (spec §4.3, §19).

Wraps the local DuckDB connection that backs the G-NAF geocoder. Three
distribution modes (spec §19.2):

- ``cache`` *(default)* — query GeoParquet files in
  ``<data_dir>/gnaf/{release}/``. Files are placed there either by
  ``fetch()`` (when remote-listing lands) or by manually populating the
  directory (works today even before remote-listing is implemented).
- ``remote`` — DuckDB queries S3 directly via httpfs (deferred).
- ``official`` — fetch official PSV from data.gov.au and build a local
  DuckDB (deferred).

Phase 2 of the v1.0 implementation only ships ``cache`` mode plumbing
(the most common path). ``remote`` and ``official`` raise
``NotImplementedError`` with clear migration messages until they land.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import duckdb

_log = logging.getLogger(__name__)

# Default endpoints from spec §6.1 / §19.
DEFAULT_GNAF_S3_BASE_URL = "s3://minus34.com/opendata"
DEFAULT_GNAF_OFFICIAL_BASE_URL = "https://data.gov.au/data/dataset"

# Minimum schema we require from G-NAF Core. Extended in Phase 4 once the
# geocoder needs the component-match columns (street, locality, etc.).
_REQUIRED_COLUMNS = frozenset(
    {
        "ADDRESS_DETAIL_PID",
        "ADDRESS_LABEL",
        "LATITUDE",
        "LONGITUDE",
        "MB_CODE",
        # POSTCODE is required for Tier 2 / Tier 3 pre-filtering (spec §19.3);
        # without it those tiers would have to scan all 15M rows.
        "POSTCODE",
    }
)

GnafMode = Literal["remote", "cache", "official"]


class GnafDataSource:
    """G-NAF Core data source for the address geocoder.

    Construct with the release / mode / cache directory, then call
    :meth:`open_connection` to get a DuckDB connection wired to query the
    ``gnaf`` view. The view's schema is validated against
    :data:`_REQUIRED_COLUMNS` on first connection.

    Release-resolution rules (spec §19.2 + §6.1):

    - ``release="202602"`` (or any 6-digit YYYYMM) → use that release directly.
    - ``release="latest"`` → the highest-numbered subdirectory under
      ``<data_dir>/gnaf/`` that contains ``*.parquet`` files. If no such
      cache exists, raises (S3 listing for fresh "latest" resolution is
      deferred).

    Once resolved, the release is recorded so subsequent calls in the
    same instance use a stable value.
    """

    def __init__(
        self,
        *,
        release: str = "latest",
        datum: str = "GDA2020",
        mode: GnafMode = "cache",
        data_dir: Path,
        s3_base_url: str = DEFAULT_GNAF_S3_BASE_URL,
        official_base_url: str = DEFAULT_GNAF_OFFICIAL_BASE_URL,
    ) -> None:
        if datum not in ("GDA2020", "GDA94"):
            raise ValueError(
                f"datum must be 'GDA2020' or 'GDA94'; got {datum!r}"
            )
        if mode not in ("remote", "cache", "official"):
            raise ValueError(
                f"mode must be 'remote', 'cache', or 'official'; got {mode!r}"
            )
        if release != "latest":
            self._validate_release_format(release)

        self._release_request = release
        self._datum = datum
        self._mode = mode
        self._data_dir = Path(data_dir)
        self._s3_base_url = s3_base_url.rstrip("/")
        self._official_base_url = official_base_url.rstrip("/")
        self._resolved_release: str | None = None
        self._connection: duckdb.DuckDBPyConnection | None = None

    # ---- properties ----------------------------------------------------

    @property
    def datum(self) -> str:
        return self._datum

    @property
    def mode(self) -> GnafMode:
        return self._mode

    @property
    def gnaf_root(self) -> Path:
        """Directory holding all cached G-NAF releases."""
        return self._data_dir / "gnaf"

    @property
    def resolved_release(self) -> str:
        """The actual YYYYMM in use. Resolved lazily on first access."""
        if self._resolved_release is None:
            self._resolved_release = self._resolve_release()
        return self._resolved_release

    @property
    def release_dir(self) -> Path:
        """Local directory for the resolved release."""
        return self.gnaf_root / self.resolved_release

    def is_cached(self) -> bool:
        """True if at least one valid release is cached locally."""
        return bool(self._find_cached_releases())

    # ---- public methods -------------------------------------------------

    def fetch(self, refresh: bool = False) -> Path:
        """Ensure G-NAF data is available locally; return the release dir.

        For ``mode='cache'``, this returns the cached release dir if one
        exists. Fetching from S3 (the next thing this method should do
        for ``cache`` mode) is deferred to a follow-up commit; in the
        meantime, populate ``<data_dir>/gnaf/{release}/`` manually.

        For ``mode='remote'`` / ``mode='official'``, raises
        :class:`NotImplementedError` with a migration message.
        """
        if self._mode != "cache":
            raise NotImplementedError(
                f"GnafDataSource mode={self._mode!r} is not yet implemented "
                "in this release. Only 'cache' is supported. "
                "Set geocoding.gnaf.mode: cache in your config and either "
                "let `census-augment fetch --gnaf` populate the cache "
                "(when that lands) or place GeoParquet files manually in "
                f"{self.gnaf_root}/{{YYYYMM}}/."
            )

        # cache mode: if we have a cached release matching the request, return it.
        if not refresh:
            try:
                resolved = self.resolved_release
            except RuntimeError:
                pass
            else:
                rel_dir = self.gnaf_root / resolved
                if rel_dir.exists() and list(rel_dir.glob("*.parquet")):
                    _log.debug(
                        "Using cached G-NAF release %s at %s", resolved, rel_dir
                    )
                    return rel_dir

        # Cache miss / refresh requested. S3 download is deferred.
        raise NotImplementedError(
            "Fetching G-NAF from S3 is not yet implemented. To use cache "
            "mode today, manually place GeoParquet files in "
            f"{self.gnaf_root}/{{YYYYMM}}/ (e.g. download from "
            f"`{self._s3_base_url}/geoscape-{{YYYYMM}}/geoparquet/` "
            "and copy in)."
        )

    def open_connection(self) -> duckdb.DuckDBPyConnection:
        """Open (or return cached) a DuckDB connection wired to query G-NAF.

        On first call: locates the cached release, validates the schema,
        creates an in-memory DuckDB connection with a ``gnaf`` view that
        unions all Parquet files in the release directory.
        """
        if self._connection is not None:
            return self._connection

        release_dir = self.fetch()
        self._validate_schema(release_dir)

        con = duckdb.connect(":memory:")
        # DuckDB needs forward-slash paths for the read_parquet glob even on Windows.
        glob = str(release_dir / "*.parquet").replace("\\", "/")
        con.execute(f"CREATE VIEW gnaf AS SELECT * FROM read_parquet('{glob}')")
        _log.info(
            "Opened G-NAF connection: release=%s, files=%d",
            self.resolved_release,
            len(list(release_dir.glob("*.parquet"))),
        )
        self._connection = con
        return con

    def close(self) -> None:
        """Close the DuckDB connection if it's open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            _log.debug("Closed G-NAF connection")

    # ---- internals ------------------------------------------------------

    @staticmethod
    def _validate_release_format(release: str) -> None:
        """Releases are 6-digit YYYYMM strings (e.g. ``'202602'``)."""
        if not (len(release) == 6 and release.isdigit()):
            raise ValueError(
                f"release must be 'latest' or a 6-digit YYYYMM string "
                f"(e.g. '202602'); got {release!r}"
            )

    def _resolve_release(self) -> str:
        """Resolve ``release='latest'`` to a specific YYYYMM."""
        if self._release_request != "latest":
            return self._release_request

        cached = self._find_cached_releases()
        if cached:
            picked = cached[-1]
            _log.info(
                "Resolved release='latest' to %s (from cache: %s)",
                picked,
                cached,
            )
            return picked

        raise RuntimeError(
            "release='latest' cannot be resolved: no G-NAF releases are "
            f"cached under {self.gnaf_root}, and S3 listing is not yet "
            "implemented. Specify an explicit release like '202602' or "
            "pre-populate the cache."
        )

    def _find_cached_releases(self) -> list[str]:
        """Return YYYYMM directory names found locally (sorted ascending).

        A directory is "cached" only if it contains at least one
        ``*.parquet`` file — empty directories are ignored.
        """
        if not self.gnaf_root.exists():
            return []
        candidates: list[str] = []
        for p in self.gnaf_root.iterdir():
            if p.is_dir() and len(p.name) == 6 and p.name.isdigit():
                if list(p.glob("*.parquet")):
                    candidates.append(p.name)
        return sorted(candidates)

    @staticmethod
    def _validate_schema(release_dir: Path) -> None:
        """Sanity-check the cached Parquet has the expected G-NAF Core columns."""
        import pyarrow.parquet as pq  # imported here so cache-only callers don't pay the cost upfront

        parquet_files = sorted(release_dir.glob("*.parquet"))
        if not parquet_files:
            raise RuntimeError(
                f"No .parquet files in G-NAF release directory {release_dir}"
            )
        schema = pq.read_schema(str(parquet_files[0]))  # type: ignore[no-untyped-call]
        present = set(schema.names)
        missing = _REQUIRED_COLUMNS - present
        if missing:
            raise RuntimeError(
                f"G-NAF Parquet at {parquet_files[0]} is missing required "
                f"columns: {sorted(missing)}. Got: {sorted(present)}. "
                "This usually means the Parquet wasn't generated from "
                "G-NAF Core, or the Geoscape schema has changed."
            )
