"""G-NAF Core data source (spec §4.3, §19).

Wraps the local DuckDB connection that backs the G-NAF geocoder. Three
distribution modes (spec §19.2):

- ``cache`` *(default)* — query GeoParquet files in
  ``<data_dir>/gnaf/{release}/``. Files are placed there either by
  :meth:`GnafDataSource.fetch` (which downloads anonymously from the
  ``s3_base_url`` bucket — defaults to the ``gnaf-loader`` snapshot at
  ``s3://minus34.com/opendata/``) or by manually populating the
  directory.
- ``remote`` — DuckDB queries S3 directly via httpfs (deferred).
- ``official`` — fetch official PSV from data.gov.au and build a local
  DuckDB (deferred).

In v1.1 the ``cache`` mode is fully wired: a cache miss triggers an
anonymous boto3 listing/download against ``s3_base_url``. ``remote`` /
``official`` continue to raise ``NotImplementedError`` with migration
messages.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

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

# A release on the gnaf-loader S3 lives at ``geoscape-{YYYYMM}/`` directly
# under the configured base prefix; .parquet files for the address table
# live one level deeper, under ``geoparquet/``. Captured here so the parser
# only matches the canonical layout (spec §19.2 default).
_RELEASE_DIR_RE = re.compile(r"geoscape-(\d{6})/")
_RELEASE_PARQUET_SUBDIR = "geoparquet"


def _parse_s3_url(url: str) -> tuple[str, str]:
    """Split ``s3://bucket/some/prefix`` into ``("bucket", "some/prefix")``.

    Trailing slash on the prefix is stripped. Empty prefix is returned as
    an empty string (i.e. bucket-root).
    """
    if not url.startswith("s3://"):
        raise ValueError(
            f"Expected an s3:// URL; got {url!r}. "
            "GnafDataSource currently only supports anonymous S3 fetch; "
            "if you have G-NAF on disk already, populate "
            "<data_dir>/gnaf/{YYYYMM}/ manually instead."
        )
    rest = url.removeprefix("s3://")
    if "/" in rest:
        bucket, prefix = rest.split("/", 1)
    else:
        bucket, prefix = rest, ""
    return bucket, prefix.rstrip("/")


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
      cache exists, falls through to listing the configured S3 bucket
      and picking the highest ``geoscape-{YYYYMM}/`` prefix.

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

    @property
    def s3_base_url(self) -> str:
        """The configured S3 base URL (read-only after construction)."""
        return self._s3_base_url

    def is_cached(self) -> bool:
        """True if at least one valid release is cached locally."""
        return bool(self._find_cached_releases())

    # ---- public methods -------------------------------------------------

    def fetch(self, refresh: bool = False) -> Path:
        """Ensure G-NAF data is available locally; return the release dir.

        For ``mode='cache'``:

        - Returns the cached release dir if one exists (and ``refresh`` is
          False).
        - On cache miss (or ``refresh=True``), anonymously downloads all
          ``*.parquet`` files for the resolved release from
          ``{s3_base_url}/geoscape-{release}/geoparquet/`` to
          ``<data_dir>/gnaf/{release}/``.
        - If ``release='latest'`` and there is no local cache, the latest
          ``geoscape-*`` prefix on S3 is discovered first.
        - If ``release='latest'`` *and* ``refresh=True``, the latest is
          re-resolved against S3 even if a local cache exists, so a
          newer release is picked up when one drops.

        For ``mode='remote'`` / ``mode='official'``, raises
        :class:`NotImplementedError` with a migration message.
        """
        if self._mode != "cache":
            raise NotImplementedError(
                f"GnafDataSource mode={self._mode!r} is not yet implemented "
                "in this release. Only 'cache' is supported. "
                "Set geocoding.gnaf.mode: cache in your config and either "
                "let `census-augment fetch --gnaf` populate the cache "
                "or place GeoParquet files manually in "
                f"{self.gnaf_root}/{{YYYYMM}}/."
            )

        # `refresh` + 'latest' must re-resolve directly against S3,
        # bypassing the local cache: that's the whole point of refreshing.
        # Otherwise a newer release that just dropped on S3 is invisible
        # because _resolve_release prefers the cache when present.
        if refresh and self._release_request == "latest":
            on_s3 = self._list_releases_on_s3()
            if not on_s3:
                raise RuntimeError(
                    f"refresh requested but no geoscape-*/ releases "
                    f"found at {self._s3_base_url}."
                )
            self._resolved_release = on_s3[-1]
            _log.info(
                "Refreshing latest from S3: picked %s (S3: %s)",
                self._resolved_release,
                on_s3,
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

        # Cache miss / refresh requested: download from S3.
        return self._download_release_from_s3(self.resolved_release)

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
        """Resolve ``release='latest'`` to a specific YYYYMM.

        Tries local cache first (offline-friendly). If empty, falls
        through to listing S3.
        """
        if self._release_request != "latest":
            return self._release_request

        cached = self._find_cached_releases()
        if cached:
            picked = cached[-1]
            _log.info(
                "Resolved release='latest' from cache: %s (cache: %s)",
                picked,
                cached,
            )
            return picked

        # No local cache — try S3.
        try:
            on_s3 = self._list_releases_on_s3()
        except Exception as e:
            raise RuntimeError(
                "release='latest' cannot be resolved: no G-NAF releases "
                f"are cached under {self.gnaf_root} and listing the "
                f"configured S3 bucket ({self._s3_base_url}) failed: "
                f"{type(e).__name__}: {e}. "
                "Specify an explicit release like '202602', check your "
                "network, or pre-populate the cache."
            ) from e

        if not on_s3:
            raise RuntimeError(
                "release='latest' cannot be resolved: no G-NAF releases "
                f"are cached under {self.gnaf_root}, and no "
                f"geoscape-{{YYYYMM}}/ prefixes were found at "
                f"{self._s3_base_url}. Has the bucket layout changed?"
            )

        picked = on_s3[-1]
        _log.info(
            "Resolved release='latest' from S3: %s (S3: %s)", picked, on_s3
        )
        return picked

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

    def _make_s3_client(self) -> Any:
        """Anonymous boto3 S3 client.

        The gnaf-loader bucket grants public-read but doesn't accept
        signed requests from arbitrary AWS accounts. We use UNSIGNED so
        the client never tries to look up credentials.

        Imported lazily so the rest of the package doesn't pay the boto3
        startup cost for callers that never touch S3 (e.g. tests that
        pre-populate the cache).
        """
        import boto3  # noqa: PLC0415 — lazy import is intentional
        from botocore import UNSIGNED  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        return boto3.client(
            "s3",
            config=Config(signature_version=UNSIGNED),
        )

    def _list_releases_on_s3(self) -> list[str]:
        """List all ``geoscape-{YYYYMM}/`` prefixes in the bucket (sorted ASC).

        Uses an anonymous list-objects request with ``Delimiter='/'`` so
        we only see top-level common prefixes — no need to paginate
        through every parquet file in every release.
        """
        bucket, base_prefix = _parse_s3_url(self._s3_base_url)
        # list_objects_v2 with Delimiter expects a prefix that ends in /
        # (or empty). Normalise here so empty bases work too.
        list_prefix = (base_prefix + "/") if base_prefix else ""

        s3 = self._make_s3_client()
        paginator = s3.get_paginator("list_objects_v2")

        releases: set[str] = set()
        for page in paginator.paginate(
            Bucket=bucket, Prefix=list_prefix, Delimiter="/"
        ):
            for cp in page.get("CommonPrefixes", []) or []:
                # Strip the base prefix to get just "geoscape-{YYYYMM}/"
                full = cp["Prefix"]
                if list_prefix and full.startswith(list_prefix):
                    suffix = full[len(list_prefix):]
                else:
                    suffix = full
                m = _RELEASE_DIR_RE.match(suffix)
                if m:
                    releases.add(m.group(1))

        return sorted(releases)

    def _download_release_from_s3(self, release: str) -> Path:
        """Download all ``*.parquet`` files for ``release`` to local cache.

        Files land under ``<data_dir>/gnaf/{release}/`` with atomic-write
        semantics (download to ``.tmp``, rename on success). Files that
        already exist locally are skipped — re-running after a partial
        download resumes from where it left off.

        Returns the local release directory. Raises ``RuntimeError`` if
        the S3 prefix has no parquet files (release doesn't exist or
        bucket layout has shifted).
        """
        bucket, base_prefix = _parse_s3_url(self._s3_base_url)
        prefix = (base_prefix + "/") if base_prefix else ""
        release_prefix = (
            f"{prefix}geoscape-{release}/{_RELEASE_PARQUET_SUBDIR}/"
        )

        s3 = self._make_s3_client()
        paginator = s3.get_paginator("list_objects_v2")

        # Collect all parquet keys for the release (with sizes for tqdm).
        parquet_objects: list[tuple[str, int]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=release_prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key.endswith(".parquet"):
                    parquet_objects.append((key, int(obj["Size"])))

        if not parquet_objects:
            raise RuntimeError(
                f"No .parquet files found at "
                f"s3://{bucket}/{release_prefix}. "
                f"Either release {release!r} doesn't exist on this S3 bucket, "
                "or its layout has changed. Check "
                f"{self._s3_base_url} and the configured release."
            )

        rel_dir = self.gnaf_root / release
        rel_dir.mkdir(parents=True, exist_ok=True)

        total_bytes = sum(size for _, size in parquet_objects)
        _log.info(
            "Downloading %d parquet file(s) for G-NAF release %s "
            "(%.1f MB total) from s3://%s/%s",
            len(parquet_objects),
            release,
            total_bytes / (1024 * 1024),
            bucket,
            release_prefix,
        )

        for key, size in parquet_objects:
            filename = key.rsplit("/", 1)[-1]
            dest = rel_dir / filename
            tmp = rel_dir / f"{filename}.tmp"

            if dest.exists() and dest.stat().st_size == size:
                _log.debug(
                    "Skipping %s: already present (%d bytes)", filename, size
                )
                continue

            # Clean up any leftover .tmp from a previous interrupted run.
            if tmp.exists():
                tmp.unlink()

            self._download_one(s3, bucket, key, tmp, size, filename)

            # Atomic rename into place. Cross-platform safe.
            tmp.replace(dest)
            _log.debug("Wrote %s (%d bytes)", dest, size)

        return rel_dir

    @staticmethod
    def _download_one(
        s3: Any,
        bucket: str,
        key: str,
        dest_tmp: Path,
        size: int,
        display_name: str,
    ) -> None:
        """Download a single S3 object to ``dest_tmp`` with a progress bar.

        Streams the object body directly via ``get_object`` rather than
        going through ``boto3.s3.transfer.download_file``: the latter
        does an internal HEAD that doesn't compose well with UNSIGNED
        clients + moto, and we don't need the multi-part transfer
        machinery for parquet files of this size.

        On any exception, the partial ``.tmp`` file is removed so a retry
        starts clean. tqdm is imported lazily so callers who never
        download don't pay for it.
        """
        from tqdm import tqdm  # noqa: PLC0415

        chunk_size = 8 * 1024 * 1024  # 8 MiB; cheap memory, decent throughput

        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            with (
                tqdm(
                    total=size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=display_name,
                    leave=False,
                ) as pbar,
                dest_tmp.open("wb") as f,
            ):
                while True:
                    chunk = body.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    pbar.update(len(chunk))
        except BaseException:
            # Clean up the partial download so the next attempt starts fresh.
            if dest_tmp.exists():
                try:
                    dest_tmp.unlink()
                except OSError:
                    pass
            raise

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
