"""G-NAF Core data source (spec §4.3, §19).

Wraps the local DuckDB connection that backs the G-NAF geocoder. Three
distribution modes (spec §19.2):

- ``cache`` *(default)* — query GeoParquet files in
  ``<data_dir>/gnaf/{release}/``. Files are placed there either by
  :meth:`GnafDataSource.fetch` (which downloads anonymously from the
  ``s3_base_url`` bucket — defaults to the ``gnaf-loader`` snapshot at
  ``s3://minus34.com/opendata/``) or by manually populating the
  directory.
- ``remote`` — DuckDB queries GeoParquet directly over HTTPS via the
  ``httpfs`` extension. No download; queries pull only the bytes they
  need. Best for prototyping, CI, and disk-constrained environments.
- ``official`` — fetch official PSV from data.gov.au and build a local
  DuckDB (deferred).

Two parquet layouts are supported (spec §19.2.1, auto-detected at
connection time):

- **gnaf-loader** (the production bucket's actual layout): G-NAF data
  lives in subdirectories named after the underlying tables. The
  geocoder pulls from ``address_principal_census_{year}_boundaries/``
  — gnaf-loader's denormalised join of address principals with the
  ABS census boundary IDs. Column names are PostgreSQL-lowercase
  (``gnaf_pid``, ``address``, ``latitude``, ``mb_{year}_code``); the
  ``gnaf`` view aliases them to the uppercase ``ADDRESS_DETAIL_PID``
  etc. that the geocoder queries.
- **legacy / bring-your-own**: a single flat parquet (or several) at
  the root of the release directory, with already-uppercase columns
  matching :data:`_REQUIRED_COLUMNS`. Used by the test fixtures and
  by users who pre-build their own G-NAF parquet from the official
  PSV.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal, NamedTuple

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
GnafLayoutStyle = Literal["gnaf-loader", "legacy"]

# A release on the gnaf-loader S3 lives at ``geoscape-{YYYYMM}/`` directly
# under the configured base prefix; .parquet files for the address table
# live one level deeper, under ``geoparquet/``. Captured here so the parser
# only matches the canonical layout (spec §19.2 default).
_RELEASE_DIR_RE = re.compile(r"geoscape-(\d{6})/")
_RELEASE_PARQUET_SUBDIR = "geoparquet"

# gnaf-loader publishes G-NAF Core's address principals (one row per
# address) at this subdirectory of geoparquet/. The table has all the
# columns the geocoder needs in one place: gnaf_pid (→ ADDRESS_DETAIL_PID),
# address (→ ADDRESS_LABEL), latitude / longitude, postcode, and
# year-suffixed MB code (mb_2021_code, mb_2016_code).
#
# Sibling subdirectories include:
#   - address_aliases/ — alternative names for the same address.
#   - address_principal_admin_boundaries/ — gnaf_pid → LGA / POA / RA / state.
#   - address_principal_census_2021_boundaries/ — gnaf_pid → MB / SA1-4 / GCCSA / ... codes.
#   - address_principal_census_2016_boundaries/ — same for 2016 ASGS.
# **None of those siblings carry the address column or lat/lon.** The
# join-with-boundaries tables are pure ID-mapping tables; the address
# data lives in `address_principals/` only. Issue #17 was caused by
# v1.2.2/v1.2.3 mistakenly targeting `*_census_<year>_boundaries/` as
# the denormalised source — DuckDB then couldn't bind `address` because
# that column simply isn't there.
_GNAF_LOADER_PRIMARY_SUBDIR = "address_principals"


class _GnafLayout(NamedTuple):
    """Describes how a particular bucket / cache directory is laid out.

    Returned by ``_detect_*_layout`` helpers and consumed by the view
    builder.

    Attributes:
        style: Which layout was detected. Affects how the ``gnaf`` view
            is constructed.
        parquet_locators: For remote layouts, the S3 keys (full paths
            including the bucket prefix). For cache layouts, the local
            ``Path`` objects pointing at the on-disk parquets. The view
            builder converts these to ``read_parquet([...])`` arguments.
        view_select_clause: The body of ``SELECT ... FROM
            read_parquet(...)`` that creates the ``gnaf`` view. ``"*"``
            for legacy (passthrough); a column-alias list for
            gnaf-loader.
    """

    style: GnafLayoutStyle
    parquet_locators: list[Any]  # list[str] (S3 keys) or list[Path]
    view_select_clause: str


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
    - ``release="latest"``:

        * In ``cache`` mode, prefers the highest-numbered subdirectory
          under ``<data_dir>/gnaf/`` that contains ``*.parquet`` files;
          falls back to the highest ``geoscape-{YYYYMM}/`` prefix on
          S3 if no local cache exists.
        * In ``remote`` mode, always lists S3 — local cache is ignored
          (the whole point is to skip the download).

    Once resolved, the release is recorded so subsequent calls in the
    same instance use a stable value.

    The optional ``s3_https_endpoint`` parameter lets you point at a
    non-AWS S3-compatible endpoint (MinIO, Cloudflare R2, a moto test
    server, ...). When set, it overrides both the boto3 listing endpoint
    and the URLs DuckDB reads from in ``remote`` mode. The default —
    ``None`` — uses ``https://{bucket}.s3.amazonaws.com/{key}`` per
    AWS's virtual-hosted style.
    """

    def __init__(
        self,
        *,
        release: str = "latest",
        datum: str = "GDA2020",
        mode: GnafMode = "cache",
        data_dir: Path,
        s3_base_url: str = DEFAULT_GNAF_S3_BASE_URL,
        s3_https_endpoint: str | None = None,
        parquet_filter: str | None = None,
        census_year: int = 2021,
        official_base_url: str = DEFAULT_GNAF_OFFICIAL_BASE_URL,
    ) -> None:
        if datum not in ("GDA2020", "GDA94"):
            raise ValueError(f"datum must be 'GDA2020' or 'GDA94'; got {datum!r}")
        if mode not in ("remote", "cache", "official"):
            raise ValueError(f"mode must be 'remote', 'cache', or 'official'; got {mode!r}")
        if release != "latest":
            self._validate_release_format(release)
        if not (1900 <= census_year <= 2100):
            raise ValueError(f"census_year must be a plausible year; got {census_year!r}")

        self._release_request = release
        self._datum = datum
        self._mode = mode
        self._data_dir = Path(data_dir)
        self._s3_base_url = s3_base_url.rstrip("/")
        self._s3_https_endpoint = s3_https_endpoint.rstrip("/") if s3_https_endpoint else None
        self._parquet_filter: re.Pattern[str] | None = (
            re.compile(parquet_filter) if parquet_filter else None
        )
        self._census_year = census_year
        self._official_base_url = official_base_url.rstrip("/")
        self._resolved_release: str | None = None
        self._resolved_bucket_region: str | None = None
        self._connection: duckdb.DuckDBPyConnection | None = None

    # ---- gnaf-loader layout helpers --------------------------------------

    @property
    def _gnaf_loader_subdir(self) -> str:
        """Subdirectory under ``geoparquet/`` containing the G-NAF Core
        address-principals table (the single source for the geocoder)."""
        return _GNAF_LOADER_PRIMARY_SUBDIR

    def _gnaf_loader_view_select(self) -> str:
        """SELECT clause aliasing gnaf-loader's lowercase columns to our
        expected uppercase schema.

        gnaf-loader's ``address_principals`` table carries one row per
        address with all the components the geocoder needs:

        - ``gnaf_pid``           → ADDRESS_DETAIL_PID
        - ``address``            → just the street portion of ADDRESS_LABEL
                                   (e.g. "115 LAWRENCE ROAD" — no locality
                                   / state / postcode). The view
                                   concatenates address + locality_name +
                                   state + postcode to build the full
                                   normalised label that
                                   :func:`normalize_address` produces from
                                   user input, so Tier 1 exact-match works.
        - ``locality_name``      → suburb / town name
        - ``state``              → 2-3 letter abbreviation (NSW, VIC, ...)
        - ``latitude``           → LATITUDE
        - ``longitude``          → LONGITUDE
        - ``postcode``           → POSTCODE
        - ``mb_{year}_code``     → MB_CODE  (the table carries both
                                              ``mb_2016_code`` and
                                              ``mb_2021_code``; we pick
                                              one based on the
                                              ``census_year`` constructor
                                              argument)

        ``CAST`` to a deterministic type lets DuckDB unify schemas across
        partitioned parquet files; it also normalises gnaf-loader's
        ``decimal128`` lat/lon and ``int64`` MB codes into the types
        the geocoder expects.

        ``CONCAT_WS`` skips NULL components silently (we don't want a
        spurious double-space if locality_name happens to be NULL on
        a particular row).
        """
        year = self._census_year
        return (
            'CAST(gnaf_pid AS VARCHAR) AS "ADDRESS_DETAIL_PID", '
            "CAST(CONCAT_WS(' ', address, locality_name, state, postcode) "
            'AS VARCHAR) AS "ADDRESS_LABEL", '
            'CAST(latitude AS DOUBLE) AS "LATITUDE", '
            'CAST(longitude AS DOUBLE) AS "LONGITUDE", '
            'CAST(postcode AS VARCHAR) AS "POSTCODE", '
            f'CAST(mb_{year}_code AS VARCHAR) AS "MB_CODE"'
        )

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

        For ``mode='remote'``: nothing to fetch — querying happens
        directly over HTTPS. Raises a clear error so the user notices
        the misconfiguration rather than silently no-opping.

        For ``mode='official'``: raises :class:`NotImplementedError`.
        """
        if self._mode == "remote":
            raise RuntimeError(
                "fetch() is meaningless in remote mode — there's nothing "
                "to download. Either switch to mode='cache' (download "
                "the parquet to disk), or call open_connection() directly "
                "and let DuckDB stream from S3."
            )
        if self._mode == "official":
            raise NotImplementedError(
                "GnafDataSource mode='official' is not yet implemented. "
                "Use 'cache' or 'remote' instead."
            )

        # `refresh` + 'latest' must re-resolve directly against S3,
        # bypassing the local cache: that's the whole point of refreshing.
        # Otherwise a newer release that just dropped on S3 is invisible
        # because _resolve_release prefers the cache when present.
        if refresh and self._release_request == "latest":
            on_s3 = self._list_releases_on_s3()
            if not on_s3:
                raise RuntimeError(
                    f"refresh requested but no geoscape-*/ releases found at {self._s3_base_url}."
                )
            self._resolved_release = on_s3[-1]
            _log.info(
                "Refreshing latest from S3: picked %s (S3: %s)",
                self._resolved_release,
                on_s3,
            )

        # cache mode: if we have a cached release matching the request, return it.
        # We probe via _detect_local_layout so that *both* flat (legacy) and
        # subdirectory (gnaf-loader) layouts count as "cached" — otherwise
        # we'd needlessly re-download every run for users whose cache uses
        # the gnaf-loader layout.
        if not refresh:
            try:
                resolved = self.resolved_release
            except RuntimeError:
                pass
            else:
                rel_dir = self.gnaf_root / resolved
                if rel_dir.exists():
                    try:
                        self._detect_local_layout(rel_dir)
                    except RuntimeError:
                        pass
                    else:
                        _log.debug(
                            "Using cached G-NAF release %s at %s",
                            resolved,
                            rel_dir,
                        )
                        return rel_dir

        # Cache miss / refresh requested: download from S3.
        return self._download_release_from_s3(self.resolved_release)

    def open_connection(self) -> duckdb.DuckDBPyConnection:
        """Open (or return cached) a DuckDB connection wired to query G-NAF.

        Dispatches on ``mode``:

        - ``cache``: locates the cached release (downloading from S3 if
          needed), validates the schema, creates a ``gnaf`` view over
          the local Parquet files.
        - ``remote``: lists the release's parquet keys on S3, loads
          DuckDB's ``httpfs`` extension, creates a ``gnaf`` view that
          ``read_parquet`` s the public HTTPS URLs directly. No
          download.
        - ``official``: raises :class:`NotImplementedError`.

        The resulting connection is cached on the instance — repeat
        calls return the same connection.
        """
        if self._connection is not None:
            return self._connection

        if self._mode == "cache":
            self._connection = self._open_cache_connection()
        elif self._mode == "remote":
            self._connection = self._open_remote_connection()
        elif self._mode == "official":
            raise NotImplementedError(
                "GnafDataSource mode='official' is not yet implemented. "
                "Use 'cache' or 'remote' instead."
            )
        else:  # pragma: no cover — guarded by __init__
            raise AssertionError(f"unreachable mode: {self._mode!r}")
        return self._connection

    def close(self) -> None:
        """Close the DuckDB connection if it's open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            _log.debug("Closed G-NAF connection")

    # ---- mode-specific connection openers --------------------------------

    def _open_cache_connection(self) -> duckdb.DuckDBPyConnection:
        """Cache-mode: ``gnaf`` view over local Parquet files.

        Detects whether the cache holds gnaf-loader-style subdirectories
        (preferred) or legacy flat parquets, builds the appropriate
        view, validates the resulting schema.
        """
        release_dir = self.fetch()
        layout = self._detect_local_layout(release_dir)

        con = duckdb.connect(":memory:")
        self._create_gnaf_view(con, layout)
        self._validate_schema_post_view(con)
        _log.info(
            "Opened G-NAF cache connection: release=%s, layout=%s, files=%d",
            self.resolved_release,
            layout.style,
            len(layout.parquet_locators),
        )
        return con

    def _open_remote_connection(self) -> duckdb.DuckDBPyConnection:
        """Remote-mode: ``gnaf`` view over HTTPS URLs via DuckDB's httpfs.

        Detects layout against the configured S3 bucket. Loads
        ``httpfs``, builds the view, validates. Each query pulls only
        the bytes it needs (parquet column projection + HTTP range
        requests).
        """
        release = self.resolved_release
        layout = self._detect_remote_layout(release)

        con = duckdb.connect(":memory:")
        # httpfs is auto-installable from DuckDB's extension repo. INSTALL is
        # idempotent (re-running with the extension cached is a no-op).
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        self._create_gnaf_view(con, layout)
        self._validate_schema_post_view(con)

        _log.info(
            "Opened G-NAF remote connection: release=%s, layout=%s, files=%d, endpoint=%s",
            release,
            layout.style,
            len(layout.parquet_locators),
            self._s3_https_endpoint or "https://*.s3.amazonaws.com",
        )
        return con

    # ---- view construction ----------------------------------------------

    def _create_gnaf_view(
        self,
        con: duckdb.DuckDBPyConnection,
        layout: _GnafLayout,
    ) -> None:
        """Create the ``gnaf`` view from a resolved layout.

        Both cache and remote modes funnel through here. The layout
        carries everything that differs between modes (locators) and
        between styles (the SELECT clause). DuckDB-side, the view
        always exposes the uppercase column names the geocoder expects.
        """
        # SQL list literal of locators (URLs for remote, file paths for cache).
        # Forward-slash paths for cache mode so Windows backslashes don't
        # break DuckDB's parser.
        items = [self._locator_to_sql_string(loc) for loc in layout.parquet_locators]
        list_sql = "[" + ", ".join(items) + "]"
        con.execute(
            f"CREATE VIEW gnaf AS SELECT {layout.view_select_clause} FROM read_parquet({list_sql})"
        )

    @staticmethod
    def _locator_to_sql_string(loc: Any) -> str:
        """Quote a locator for inclusion in a DuckDB SQL list literal."""
        # Path objects (cache mode): convert to forward-slash string.
        if isinstance(loc, Path):
            normalised = str(loc).replace("\\", "/")
            return f"'{normalised}'"
        # Strings (remote mode HTTPS URLs): used as-is.
        return f"'{loc}'"

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

        - cache mode: prefers local cache (offline-friendly), falls back
          to S3.
        - remote mode: ignores local cache; always lists S3.
        """
        if self._release_request != "latest":
            return self._release_request

        # Cache mode: prefer local. Remote mode: skip local entirely.
        if self._mode == "cache":
            cached = self._find_cached_releases()
            if cached:
                picked = cached[-1]
                _log.info(
                    "Resolved release='latest' from cache: %s (cache: %s)",
                    picked,
                    cached,
                )
                return picked

        # No useable local cache (or remote mode) — list S3.
        try:
            on_s3 = self._list_releases_on_s3()
        except Exception as e:
            raise RuntimeError(
                "release='latest' cannot be resolved: "
                f"listing the configured S3 bucket "
                f"({self._s3_base_url}) failed: "
                f"{type(e).__name__}: {e}. "
                "Specify an explicit release like '202602', check your "
                "network, or pre-populate the cache (cache mode only)."
            ) from e

        if not on_s3:
            raise RuntimeError(
                "release='latest' cannot be resolved: "
                f"no geoscape-{{YYYYMM}}/ prefixes were found at "
                f"{self._s3_base_url}. Has the bucket layout changed?"
            )

        picked = on_s3[-1]
        _log.info("Resolved release='latest' from S3: %s (S3: %s)", picked, on_s3)
        return picked

    def _find_cached_releases(self) -> list[str]:
        """Return YYYYMM directory names found locally (sorted ascending).

        A directory counts as "cached" when ``_detect_local_layout``
        finds either layout — flat parquets at the root *or* a
        gnaf-loader subdirectory. Empty directories are ignored.
        """
        if not self.gnaf_root.exists():
            return []
        candidates: list[str] = []
        for p in self.gnaf_root.iterdir():
            if not (p.is_dir() and len(p.name) == 6 and p.name.isdigit()):
                continue
            try:
                self._detect_local_layout(p)
            except RuntimeError:
                continue
            candidates.append(p.name)
        return sorted(candidates)

    def _make_s3_client(self) -> Any:
        """Anonymous boto3 S3 client.

        The gnaf-loader bucket grants public-read but doesn't accept
        signed requests from arbitrary AWS accounts. We use UNSIGNED so
        the client never tries to look up credentials.

        If ``s3_https_endpoint`` was configured (S3-compatible mirror
        or a moto test server), it's threaded through as ``endpoint_url``
        so listing hits the same place DuckDB will read from.

        Imported lazily so the rest of the package doesn't pay the boto3
        startup cost for callers that never touch S3 (e.g. tests that
        pre-populate the cache).
        """
        import boto3  # noqa: PLC0415 — lazy import is intentional
        from botocore import UNSIGNED  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        kwargs: dict[str, Any] = {
            "config": Config(signature_version=UNSIGNED),
        }
        if self._s3_https_endpoint:
            kwargs["endpoint_url"] = self._s3_https_endpoint
            # S3-compatible servers (and moto) typically use path-style
            # addressing. DuckDB likewise. Force it on the boto3 side too.
            kwargs["config"] = Config(
                signature_version=UNSIGNED,
                s3={"addressing_style": "path"},
            )
        return boto3.client("s3", **kwargs)

    def _build_object_url(self, bucket: str, key: str) -> str:
        """HTTPS URL DuckDB should read for ``s3://bucket/key``.

        - Override set (``s3_https_endpoint``): path-style on the
          configured endpoint, ``{endpoint}/{bucket}/{key}``. For
          S3-compatible mirrors or test servers (moto, MinIO, R2, ...).
        - Bucket name contains a dot (e.g. ``minus34.com``): forced
          path-style on the bucket's *regional* endpoint
          ``https://s3.{region}.amazonaws.com/{bucket}/{key}``.
          Two reasons:

          - Virtual-hosted style ``minus34.com.s3.amazonaws.com``
            fails TLS hostname verification because AWS's wildcard
            cert ``*.s3.amazonaws.com`` only covers a single
            subdomain level. (Issue #8 / v1.2.2.)
          - Path-style on the *global* endpoint
            ``https://s3.amazonaws.com/{bucket}/{key}`` returns
            HTTP 301 with ``x-amz-bucket-region`` for any bucket
            outside ``us-east-1``. Boto3 follows that redirect; the
            DuckDB ``httpfs`` extension does **not** — queries error
            out with "HTTP 301 Moved Permanently". (Issue #17.)

          The bucket's region is discovered once via boto3's
          ``head_bucket`` and cached on the instance.

        - Otherwise: virtual-hosted style on AWS,
          ``https://{bucket}.s3.amazonaws.com/{key}``.
        """
        if self._s3_https_endpoint:
            return f"{self._s3_https_endpoint}/{bucket}/{key}"
        if "." in bucket:
            region = self._resolve_bucket_region(bucket)
            return f"https://s3.{region}.amazonaws.com/{bucket}/{key}"
        return f"https://{bucket}.s3.amazonaws.com/{key}"

    def _resolve_bucket_region(self, bucket: str) -> str:
        """Return the AWS region the bucket lives in, cached on the instance.

        Used by :meth:`_build_object_url` to construct path-style URLs
        for dotted bucket names (which can't go via the wildcard cert
        and can't follow 301 redirects under DuckDB httpfs).

        Strategy: ``head_bucket`` is the cheapest way to discover the
        region. Even when the request hits the wrong endpoint and
        comes back as 301 / 400, the response carries
        ``x-amz-bucket-region`` in its headers, so we extract it from
        the ``ClientError``.
        """
        if self._resolved_bucket_region is not None:
            return self._resolved_bucket_region

        client = self._make_s3_client()
        try:
            response = client.head_bucket(Bucket=bucket)
            region = response.get("BucketRegion")
            if not region:
                region = (
                    response.get("ResponseMetadata", {})
                    .get("HTTPHeaders", {})
                    .get("x-amz-bucket-region")
                )
        except Exception as exc:  # noqa: BLE001 — we extract from the exception
            # ClientError stashes response headers on the exception.
            response_attr = getattr(exc, "response", {}) or {}
            region = (
                response_attr.get("ResponseMetadata", {})
                .get("HTTPHeaders", {})
                .get("x-amz-bucket-region")
            )
            if not region:
                raise

        if not region:
            # Last-ditch fallback so we don't blow up on a partial
            # response. ``us-east-1`` accepts any bucket via the
            # global endpoint (with a 301 redirect for non-us-east-1
            # buckets, which we just absorbed above).
            region = "us-east-1"

        # ``region`` came from a dict.get() — narrow to str for mypy.
        region_str = str(region)
        self._resolved_bucket_region = region_str
        _log.debug("Resolved S3 region for %s: %s", bucket, region_str)
        return region_str

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
        for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []) or []:
                # Strip the base prefix to get just "geoscape-{YYYYMM}/"
                full = cp["Prefix"]
                if list_prefix and full.startswith(list_prefix):
                    suffix = full[len(list_prefix) :]
                else:
                    suffix = full
                m = _RELEASE_DIR_RE.match(suffix)
                if m:
                    releases.add(m.group(1))

        return sorted(releases)

    def _list_parquet_objects_on_s3(self, release: str) -> list[tuple[str, int]]:
        """Return ``[(key, size_bytes), ...]`` for the G-NAF Core ``*.parquet``
        files in the release.

        Tries the gnaf-loader layout first (parquets under
        ``address_principal_census_{year}_boundaries/``). Falls back to
        legacy flat parquets at the root of ``geoparquet/`` if no
        boundaries subdirectory is found — this preserves the
        bring-your-own-parquet path for users who pre-build a single
        denormalised parquet from the official Geoscape PSV.

        The optional ``parquet_filter`` regex still applies to legacy
        listings (matched against the relative key) — useful for mirrors
        with non-default flat-parquet conventions. It is *ignored* under
        the gnaf-loader layout where the subdirectory itself does the
        scoping.

        Shared between cache mode (download) and remote mode (URL
        construction). Sizes are used by cache mode's resume-skip
        check; remote mode ignores them.
        """
        bucket, base_prefix = _parse_s3_url(self._s3_base_url)
        prefix = (base_prefix + "/") if base_prefix else ""
        release_prefix = f"{prefix}geoscape-{release}/{_RELEASE_PARQUET_SUBDIR}/"

        # gnaf-loader: try the year-specific boundaries subdirectory first.
        loader_subdir_prefix = f"{release_prefix}{self._gnaf_loader_subdir}/"
        loader_objs = self._list_parquets_under_prefix(bucket, loader_subdir_prefix)
        if loader_objs:
            return loader_objs

        # Fallback: legacy parquets under geoparquet/.
        #
        # Default (no parquet_filter): accept flat parquets only —
        # subdirectory contents like ``abs_2016_gccsa/part-*.parquet``
        # are skipped. This is the post-#9 behaviour and what most
        # bring-your-own users have.
        #
        # With parquet_filter set: the regex is matched against the
        # relative key (post-``geoparquet/``), and subdirectory
        # parquets are accepted if they match. Lets advanced users
        # point at custom layouts (e.g. a self-built parquet at
        # ``custom/addresses.parquet``).
        legacy_objs: list[tuple[str, int]] = []
        has_explicit_filter = self._parquet_filter is not None
        for key, size in self._list_parquets_under_prefix(bucket, release_prefix):
            relative = key[len(release_prefix) :] if key.startswith(release_prefix) else key
            if "/" in relative and not has_explicit_filter:
                # Subdirectory parquet without explicit user opt-in —
                # skip. Avoids picking up abs_2016_gccsa/,
                # osm_amenities/, etc.
                continue
            if not self._matches_legacy_filter(relative):
                _log.debug("Skipping legacy parquet (filter excluded): %s", key)
                continue
            legacy_objs.append((key, size))
        return legacy_objs

    def _list_parquets_under_prefix(self, bucket: str, list_prefix: str) -> list[tuple[str, int]]:
        """Return ``[(key, size_bytes), ...]`` for every ``*.parquet``
        anywhere under ``list_prefix`` (recursive)."""
        s3 = self._make_s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        objs: list[tuple[str, int]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key.endswith(".parquet"):
                    objs.append((key, int(obj["Size"])))
        return objs

    def _matches_legacy_filter(self, relative_key: str) -> bool:
        """Apply the optional ``parquet_filter`` regex to a legacy-layout key."""
        if self._parquet_filter is not None:
            return self._parquet_filter.search(relative_key) is not None
        # Default: any flat parquet (the path-depth check already happened
        # before we got here).
        return True

    def _detect_remote_layout(self, release: str) -> _GnafLayout:
        """Inspect the S3 bucket and return a layout descriptor.

        Tries gnaf-loader first (parquets under
        ``address_principal_census_{year}_boundaries/``). Falls back to
        legacy flat parquets. Raises if neither is found.
        """
        bucket, base_prefix = _parse_s3_url(self._s3_base_url)
        prefix = (base_prefix + "/") if base_prefix else ""
        release_prefix = f"{prefix}geoscape-{release}/{_RELEASE_PARQUET_SUBDIR}/"
        loader_prefix = f"{release_prefix}{self._gnaf_loader_subdir}/"

        loader_objs = self._list_parquets_under_prefix(bucket, loader_prefix)
        if loader_objs:
            urls = [self._build_object_url(bucket, key) for key, _ in loader_objs]
            return _GnafLayout(
                style="gnaf-loader",
                parquet_locators=urls,
                view_select_clause=self._gnaf_loader_view_select(),
            )

        # Legacy: same listing semantics as cache mode.
        legacy_objs = self._list_parquet_objects_on_s3(release)
        if legacy_objs:
            urls = [self._build_object_url(bucket, key) for key, _ in legacy_objs]
            return _GnafLayout(
                style="legacy",
                parquet_locators=urls,
                view_select_clause="*",
            )

        raise RuntimeError(
            f"No G-NAF parquet files found for release {release!r} at "
            f"s3://{bucket}/{release_prefix}. Tried gnaf-loader layout "
            f"({self._gnaf_loader_subdir}/) and legacy flat parquets. "
            "Either the release doesn't exist, or the bucket layout has "
            "changed. Configure data_sources.gnaf_parquet_filter to point "
            "at a custom layout if needed."
        )

    def _detect_local_layout(self, release_dir: Path) -> _GnafLayout:
        """Inspect a local cache directory and return a layout descriptor."""
        loader_dir = release_dir / self._gnaf_loader_subdir
        if loader_dir.is_dir():
            paths = sorted(loader_dir.glob("*.parquet"))
            if paths:
                return _GnafLayout(
                    style="gnaf-loader",
                    parquet_locators=list(paths),
                    view_select_clause=self._gnaf_loader_view_select(),
                )

        # Legacy: flat parquets at the root of release_dir.
        flat = sorted(release_dir.glob("*.parquet"))
        if flat:
            return _GnafLayout(
                style="legacy",
                parquet_locators=flat,
                view_select_clause="*",
            )

        raise RuntimeError(
            f"No G-NAF parquet files found in {release_dir}. Tried "
            f"gnaf-loader layout ({self._gnaf_loader_subdir}/) and "
            "legacy flat parquets. Populate the cache via "
            "`census-augment fetch --gnaf` or place parquet files "
            "manually."
        )

    def _download_release_from_s3(self, release: str) -> Path:
        """Download all G-NAF ``*.parquet`` files for ``release`` to local cache.

        For gnaf-loader layouts the file lands under
        ``<data_dir>/gnaf/{release}/{boundaries_subdir}/{filename}`` —
        the boundaries-subdirectory layer is preserved on disk so the
        cache-mode layout detector can find it on the next run.

        For legacy layouts the file lands flat at
        ``<data_dir>/gnaf/{release}/{filename}`` — same as before.

        Atomic-write semantics in both cases: download to ``.tmp``,
        rename on success. Files that already exist locally with a
        matching size are skipped, so re-running after a partial
        download resumes from where it left off.
        """
        bucket, base_prefix = _parse_s3_url(self._s3_base_url)
        prefix = (base_prefix + "/") if base_prefix else ""
        release_prefix = f"{prefix}geoscape-{release}/{_RELEASE_PARQUET_SUBDIR}/"

        parquet_objects = self._list_parquet_objects_on_s3(release)

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
            "Downloading %d parquet file(s) for G-NAF release %s (%.1f MB total) from s3://%s/%s",
            len(parquet_objects),
            release,
            total_bytes / (1024 * 1024),
            bucket,
            release_prefix,
        )

        s3 = self._make_s3_client()
        for key, size in parquet_objects:
            # Compute the local destination, preserving any subdirectory
            # structure under the release dir. This ensures gnaf-loader
            # boundaries parquets land in
            # ``<release_dir>/address_principal_census_{year}_boundaries/``
            # — exactly what _detect_local_layout expects to find.
            relative = (
                key[len(release_prefix) :]
                if key.startswith(release_prefix)
                else key.rsplit("/", 1)[-1]
            )
            dest = rel_dir / relative
            tmp = dest.with_name(dest.name + ".tmp")
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists() and dest.stat().st_size == size:
                _log.debug("Skipping %s: already present (%d bytes)", relative, size)
                continue

            # Clean up any leftover .tmp from a previous interrupted run.
            if tmp.exists():
                tmp.unlink()

            self._download_one(s3, bucket, key, tmp, size, dest.name)

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
    def _validate_schema_post_view(con: duckdb.DuckDBPyConnection) -> None:
        """Validate the ``gnaf`` view exposes the required columns.

        Used by both cache and remote modes, after ``_create_gnaf_view``
        has run. ``DESCRIBE`` is cheap — DuckDB answers from parquet
        footer metadata (small range request for remote, free for
        cache); no full file scan.

        For gnaf-loader layouts the view explicitly aliases each
        required column, so a missing-column failure here implies the
        underlying parquet doesn't carry the expected gnaf-loader
        schema (and the CREATE VIEW should already have failed). The
        validator is mainly a safety net for the legacy / BYO
        passthrough path.
        """
        rows = con.execute("DESCRIBE gnaf").fetchall()
        # DESCRIBE returns (column_name, column_type, null, key, default, extra)
        present = {r[0] for r in rows}
        missing = _REQUIRED_COLUMNS - present
        if missing:
            raise RuntimeError(
                f"G-NAF view is missing required columns: "
                f"{sorted(missing)}. Got: {sorted(present)}. "
                "Either the parquet wasn't generated from G-NAF Core, "
                "the Geoscape schema has changed, or the configured "
                "census_year doesn't match what's published in the bucket."
            )

    # Back-compat alias for tests / external callers from v1.0–v1.2.
    _validate_schema_remote = _validate_schema_post_view
