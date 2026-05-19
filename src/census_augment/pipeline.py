"""Pipeline orchestration: input -> geocode -> spatial -> enrich -> output.

Two entry points (spec §3, §7, §18):

- :meth:`Pipeline.run` — file-in / file-out. Reads ``config.input.path``,
  writes ``config.output.path``. Used by the CLI's ``run`` command.
- :meth:`Pipeline.augment` — DataFrame in / DataFrame out, no file I/O.
  Used by notebooks and library code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pandas as pd

from .catalog import VariableCatalog
from .config import (
    CensusConfig,
    Config,
    DataSourcesConfig,
    GeocodingConfig,
    InputConfig,
    NominatimConfig,
    OutputConfig,
)
from .data_sources.boundaries import BoundariesDataSource
from .data_sources.datapacks import DataPacksDataSource
from .data_sources.gnaf import GnafDataSource
from .enrich import CensusEnricher
from .geocoding.base import GeocodeResult, Geocoder
from .geocoding.cache import GeocodeCache, NullCache
from .geocoding.gnaf import GnafGeocoder
from .geocoding.nominatim import NominatimGeocoder
from .data_sources.mb_correspondence import MbCorrespondenceDataSource, MbInfo
from .datasets._spec import TemporalDatasetMetadata
from .paths import default_cache_dir, default_data_dir
from .spatial import SpatialIndex

_log = logging.getLogger(__name__)

# Output column names per spec §8
_GEO_LAT_COL = "geo_lat"
_GEO_LON_COL = "geo_lon"
_GEO_SOURCE_COL = "geo_source"
_GEO_MATCH_SCORE_COL = "geo_match_score"
_SA2_CODE_COL = "sa2_code"
_SA2_NAME_COL = "sa2_name"
_SA2_RESOLUTION_COL = "sa2_resolution"
_RESERVED_OUTPUT_COLS = frozenset(
    {
        _GEO_LAT_COL,
        _GEO_LON_COL,
        _GEO_SOURCE_COL,
        _GEO_MATCH_SCORE_COL,
        _SA2_CODE_COL,
        _SA2_NAME_COL,
        _SA2_RESOLUTION_COL,
    }
)

#: SA2 resolution path values (spec §7.3, §8). Recorded per-row in the
#: ``sa2_resolution`` output column.
_SA2_RES_MB_CODE = "mb_code"  # fast path via the Mesh Block .dbf lookup
_SA2_RES_SPATIAL = "spatial_join"  # fallback point-in-polygon
_SA2_RES_UNMATCHED = "unmatched"  # had coords but fell outside any SA2

#: All possible ``geo_source`` values (spec §8 / §19.1). Order matters:
#: the human-readable summary lists tier counts in this order.
_GEO_SOURCE_VALUES: tuple[str, ...] = (
    "input",
    "gnaf_exact",
    "gnaf_component",
    "gnaf_fuzzy",
    "nominatim_cache",
    "nominatim_fresh",
    "failed",
)


def _is_gcp_variable_ref(ref: str) -> bool:
    """True if ``ref`` is shaped like a GCP variable (``G\\d+.<column>``).

    Used by :meth:`Pipeline.from_config` to split user-supplied variable
    refs into GCP-vs-non-GCP for validation. Non-GCP refs (SEIFA, ERP,
    DSS, ATO, PRESET, ...) are validated lazily by the enricher and
    registry.
    """
    if "." not in ref:
        return False
    namespace, _, _ = ref.partition(".")
    return bool(namespace) and namespace[0] == "G" and namespace[1:].isdigit()


@dataclass(frozen=True)
class RunSummary:
    """End-of-run statistics (spec §7.5).

    ``geo_input`` / ``geo_cache`` / ``geo_fresh`` / ``geo_failed`` partition
    rows by where their lat/lon came from. v1.0 also exposes ``geo_per_tier``,
    a per-tier histogram (``gnaf_exact``, ``gnaf_component``, etc.) so
    callers can see exactly which match strategies hit. The four legacy
    aggregates remain for backwards-compatible reading of older code.

    ``sa2_unmatched`` counts rows that *did* have lat/lon but didn't fall
    in any SA2. ``sa2_resolution_counts`` partitions SA2-resolved rows by
    which §7.3 path produced the result: ``mb_code`` (fast path),
    ``spatial_join`` (fallback), or ``unmatched`` (had coords but no SA2).

    ``fully_enriched`` / ``partially_enriched`` partition rows that got
    an SA2 by whether every configured variable resolved to a non-null
    value.

    ``unused_configured_columns`` lists locator columns that were set in
    config (or via override) but absent from the input DataFrame; they
    were dropped from the resolution chain for this run with a WARNING
    log.
    """

    total_rows: int
    geo_input: int
    geo_cache: int
    geo_fresh: int
    geo_failed: int
    sa2_unmatched: int
    fully_enriched: int
    partially_enriched: int
    geo_per_tier: dict[str, int] = field(default_factory=dict)
    sa2_resolution_counts: dict[str, int] = field(default_factory=dict)
    unused_configured_columns: list[str] = field(default_factory=list)

    def format_human_readable(self) -> str:
        body = (
            "Run summary:\n"
            f"  Total rows:           {self.total_rows}\n"
            "  Geocoding source:\n"
            f"    From input lat/lon: {self.geo_input}\n"
            f"    From cache:         {self.geo_cache}\n"
            f"    Freshly geocoded:   {self.geo_fresh}\n"
            f"    Failed:             {self.geo_failed}\n"
        )
        if self.geo_per_tier:
            body += "  Per-tier breakdown:\n"
            for tier in _GEO_SOURCE_VALUES:
                count = self.geo_per_tier.get(tier, 0)
                if count == 0:
                    continue
                body += f"    {tier:<20} {count}\n"
        body += f"  SA2 lookup:\n    Outside any SA2:    {self.sa2_unmatched}\n"
        if self.sa2_resolution_counts:
            body += "  SA2 resolution path:\n"
            for path in (_SA2_RES_MB_CODE, _SA2_RES_SPATIAL, _SA2_RES_UNMATCHED):
                count = self.sa2_resolution_counts.get(path, 0)
                if count == 0:
                    continue
                body += f"    {path:<20} {count}\n"
        body += (
            "  Enrichment:\n"
            f"    Fully enriched:     {self.fully_enriched}\n"
            f"    Partially enriched: {self.partially_enriched}\n"
        )
        if self.unused_configured_columns:
            body += "  Unused configured columns (absent from input):\n"
            for col in self.unused_configured_columns:
                body += f"    - {col}\n"
        return body


class _UnsetType:
    """Singleton sentinel distinguishing "kwarg not provided" from
    "kwarg explicitly set to None". Used by :meth:`Pipeline.augment` so
    callers can override a configured locator column to ``None`` without
    that being indistinguishable from "use the config default"."""

    _instance: _UnsetType | None = None

    def __new__(cls) -> _UnsetType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "_UNSET"


_UNSET: _UnsetType = _UnsetType()


#: Encodings tried, in order, when reading a user CSV. Excel exports
#: on Windows are routinely CP1252 / Windows-1252; some tools prefix
#: a UTF-8 BOM (``﻿``). Trying the BOM-aware variant first and
#: falling back to CP1252 means most non-UTF-8 inputs Just Work.
_CSV_READ_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1252")


def _read_csv_with_encoding_fallback(path: Path) -> pd.DataFrame:
    """Read a CSV trying UTF-8 (BOM-aware) then CP1252.

    Wraps :func:`pandas.read_csv` with the encoding cascade in
    :data:`_CSV_READ_ENCODINGS`. If all encodings fail with a
    :class:`UnicodeDecodeError`, raises :class:`ValueError` with an
    actionable hint that includes the path and the encodings tried.
    """
    last_error: UnicodeDecodeError | None = None
    for encoding in _CSV_READ_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as e:
            last_error = e
            continue
    raise ValueError(
        f"Could not decode {path} as any of {list(_CSV_READ_ENCODINGS)}. "
        f"Re-save the file as UTF-8 (most editors offer this in 'Save As' "
        f"-> 'Encoding'). Last decoder error: {last_error}."
    )


@dataclass(eq=False)
class AugmentResult:
    """Result of :meth:`Pipeline.augment` (spec §18.2).

    Attributes:
        df: Augmented DataFrame (input columns + geo + sa2 + enrichment).
            A new DataFrame; the input passed to ``augment`` is not mutated.
        summary: Per-run aggregate counts.
        added_columns: Names of columns this augment added (handy for
            ``df[result.added_columns]`` selection).
        is_fully_enriched: Boolean Series, indexed like ``df``. True for
            rows whose every enrichment cell is non-null.
        geocoding_failed: Boolean Series, indexed like ``df``. True for
            rows whose geocoding ended in ``failed``.
        sa2_unmatched: Boolean Series, indexed like ``df``. True for rows
            that resolved to a coordinate but didn't fall in any SA2 polygon.
    """

    df: pd.DataFrame
    summary: RunSummary
    added_columns: list[str]
    is_fully_enriched: pd.Series
    geocoding_failed: pd.Series
    sa2_unmatched: pd.Series
    #: Per-dataset set of releases touched by the run, populated only
    #: in temporal mode (``input.date_column`` set). ``None`` in
    #: cross-sectional mode. Keys are dataset ids, values are sorted
    #: sets of release identifiers.
    releases_used: dict[str, list[str]] | None = None


class Pipeline:
    """Orchestrates input -> geocode -> spatial -> enrich -> output.

    Construct directly with pre-built collaborators (for tests) or via:

    - :meth:`from_config` — wires everything from a ``Config`` (downloads
      boundaries / DataPacks if not cached).
    - :meth:`create` — notebook-friendly factory; minimal kwargs, defaults
      for the rest.
    """

    def __init__(
        self,
        *,
        config: Config,
        geocoders: list[Geocoder],
        spatial: SpatialIndex,
        enricher: CensusEnricher,
        mb_lookup: dict[str, MbInfo] | None = None,
    ) -> None:
        """Construct directly. Tests use this; production code typically
        goes through :meth:`from_config` or :meth:`create`.

        ``geocoders`` is an ordered list — the first to return a
        non-failed result for a given row wins (spec §7.2 cascading
        fallback). An empty list is rejected; callers wanting to bypass
        geocoding entirely should rely on lat/lon-only inputs.

        ``mb_lookup`` is the MB→SA2 dict (spec §7.3 fast path). When
        provided, rows whose geocoder returned an ``mb_code`` resolve
        SA2 in O(1) without a spatial join. When ``None`` (or when a
        geocoder doesn't produce ``mb_code``), the spatial-join fallback
        is used.
        """
        if not geocoders:
            raise ValueError(
                "Pipeline requires at least one geocoder; got an empty list. "
                "If you want to disable geocoding entirely, ensure all input "
                "rows have lat/lon set so the geocoder is never invoked."
            )
        self._config = config
        self._geocoders = geocoders
        self._spatial = spatial
        self._enricher = enricher
        self._mb_lookup = mb_lookup if mb_lookup is not None else {}
        self._validate_no_column_collisions()

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        data_dir: Path | None = None,
        cache_dir: Path | None = None,
    ) -> Pipeline:
        """Build a fully-wired pipeline from config.

        ``data_dir`` and ``cache_dir`` default to platform-appropriate user
        cache directories — see :mod:`census_augment.paths` and spec §9.
        Triggers HTTP downloads for boundaries and DataPacks if not cached;
        validates configured ``variables`` against the loaded DataPack
        metadata (spec §6.2 semantic check).
        """
        if data_dir is None:
            data_dir = default_data_dir()
        if cache_dir is None:
            cache_dir = default_cache_dir()

        # Cache layout is per-ASGS-edition (spec-temporal.md §13). The
        # `<year>` segment lets multiple editions coexist on disk for
        # future temporal-mode runs that touch more than one edition.
        boundary_year = str(config.census.year)
        boundaries_ds = BoundariesDataSource(
            census=config.census,
            base_url=config.data_sources.boundaries_base_url,
            root=data_dir / "boundaries" / boundary_year,
        )
        # Per-edition column names (Edition 2 uses SA2_MAIN16/SA2_NAME16;
        # Edition 3 uses SA2_CODE21/SA2_NAME21). The boundary source's
        # edition spec is the single source of truth.
        edition = boundaries_ds.edition
        spatial = SpatialIndex(
            boundaries_ds.load(),
            code_column=edition.sa2_code_column,
            name_column=edition.sa2_name_column,
        )

        datapacks_ds = DataPacksDataSource(
            census=config.census,
            base_url=config.data_sources.datapacks_base_url,
            root=data_dir / "census" / boundary_year,
        )
        catalog = VariableCatalog.from_data_source(datapacks_ds)
        # Pre-validate only the GCP-shape variables against the
        # catalog. Non-GCP variables (SEIFA / ERP / DSS / ATO /
        # PRESET) are validated lazily when the enricher hits each
        # dataset's fetcher; the registry surfaces clear errors then.
        gcp_variables = {
            friendly: ref for friendly, ref in config.variables.items() if _is_gcp_variable_ref(ref)
        }
        catalog.validate_variables(gcp_variables)
        enricher = CensusEnricher(
            datapacks=datapacks_ds,
            catalog=catalog,
            variables=config.variables,
            output_prefix=config.output.prefix,
            data_dir=data_dir,
        )

        cache: GeocodeCache
        if config.geocoding.cache_enabled:
            cache = GeocodeCache(cache_dir / "geocoding")
        else:
            cache = NullCache()

        # Build the geocoder chain in the order configured (spec §7.2).
        # Each provider's collaborators are wired only if it actually
        # appears in the chain — keeping G-NAF out of the providers list
        # avoids forcing a G-NAF download on Nominatim-only users.
        geocoders: list[Geocoder] = []
        mb_lookup: dict[str, MbInfo] | None = None
        for provider in config.geocoding.providers:
            if provider == "gnaf":
                gnaf_ds = GnafDataSource(
                    release=config.geocoding.gnaf.release,
                    datum=config.geocoding.gnaf.datum,
                    mode=config.geocoding.gnaf.mode,
                    data_dir=data_dir,
                    s3_base_url=config.data_sources.gnaf_s3_base_url,
                    s3_https_endpoint=config.data_sources.gnaf_s3_https_endpoint,
                    parquet_filter=config.data_sources.gnaf_parquet_filter,
                    census_year=config.census.year,
                    official_base_url=config.data_sources.gnaf_official_base_url,
                )
                geocoders.append(
                    GnafGeocoder(
                        data_source=gnaf_ds,
                        fuzzy_threshold=config.geocoding.gnaf.fuzzy_threshold,
                    )
                )
                # MB→SA2 fast-path lookup is shared by every G-NAF row
                # the chain produces. Built once per pipeline; the .dbf
                # is small enough that re-fetching per call would be
                # silly.
                if mb_lookup is None:
                    mb_ds = MbCorrespondenceDataSource(
                        year=config.census.year,
                        datum=config.geocoding.gnaf.datum,
                        base_url=config.data_sources.boundaries_base_url,
                        root=data_dir / "mb" / boundary_year,
                    )
                    mb_lookup = mb_ds.load_correspondence()
            elif provider == "nominatim":
                nominatim_cfg = config.geocoding.nominatim
                if nominatim_cfg.user_agent is None:
                    raise ValueError(
                        "geocoding.nominatim.user_agent is required when "
                        "'nominatim' is in geocoding.providers."
                    )
                geocoders.append(
                    NominatimGeocoder(
                        user_agent=nominatim_cfg.user_agent,
                        cache=cache,
                        rate_limit_per_second=(nominatim_cfg.rate_limit_per_second),
                    )
                )

        return cls(
            config=config,
            geocoders=geocoders,
            spatial=spatial,
            enricher=enricher,
            mb_lookup=mb_lookup,
        )

    @classmethod
    def create(
        cls,
        *,
        variables: dict[str, str],
        user_agent: str,
        address_column: str | None = None,
        latitude_column: str | None = None,
        longitude_column: str | None = None,
        date_column: str | None = None,
        output_prefix: str = "sa2_",
        data_dir: Path | None = None,
        cache_dir: Path | None = None,
    ) -> Pipeline:
        """Notebook-friendly factory (spec §18.1).

        Constructs a default ``Config`` from the supplied parameters and
        delegates to :meth:`from_config`. All other config sub-sections
        use their default values; for full control, construct ``Config``
        manually and call ``from_config`` directly.

        At least one locator (``address_column`` or both
        ``latitude_column``/``longitude_column``) must be provided —
        :class:`InputConfig` enforces this.

        Pass ``date_column`` to enable temporal mode (per-row release
        selection; see ``spec-temporal.md`` §9). The default temporal
        config is used; for non-default tuning (per-dataset resolution,
        out-of-range behaviour) construct ``Config`` manually and call
        ``from_config`` directly.
        """
        cfg = Config(
            input=InputConfig(
                address_column=address_column,
                latitude_column=latitude_column,
                longitude_column=longitude_column,
                date_column=date_column,
            ),
            output=OutputConfig(prefix=output_prefix),
            census=CensusConfig(),
            data_sources=DataSourcesConfig(),
            geocoding=GeocodingConfig(
                # Nominatim-only by default for the notebook factory:
                # G-NAF imposes a 10 GB cache or live S3 reads, neither
                # of which is what a notebook user typically wants for
                # a quick augmentation. Users wanting the full G-NAF +
                # Nominatim chain construct a ``Config`` manually and
                # call ``Pipeline.from_config`` directly.
                providers=["nominatim"],
                nominatim=NominatimConfig(user_agent=user_agent),
            ),
            variables=variables,
        )
        return cls.from_config(cfg, data_dir=data_dir, cache_dir=cache_dir)

    def run(self) -> RunSummary:
        """File-in / file-out execution (CLI path).

        Reads ``config.input.path``, augments, writes ``config.output.path``.
        Both must be set; raises ``ValueError`` if either is ``None``.
        Library users wanting DataFrame in/out should call
        :meth:`augment` instead.
        """
        if self._config.input.path is None:
            raise ValueError(
                "Pipeline.run() requires config.input.path; for DataFrame "
                "in/out use Pipeline.augment(df) instead."
            )
        if self._config.output.path is None:
            raise ValueError(
                "Pipeline.run() requires config.output.path; for DataFrame "
                "in/out use Pipeline.augment(df) instead."
            )

        df = _read_csv_with_encoding_fallback(self._config.input.path)
        result = self.augment(df)

        output_path = self._config.output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.df.to_csv(output_path, index=False)
        _log.info("Wrote %d rows to %s", len(result.df), output_path)
        return result.summary

    def augment(
        self,
        df: pd.DataFrame,
        *,
        address_column: str | None | _UnsetType = _UNSET,
        latitude_column: str | None | _UnsetType = _UNSET,
        longitude_column: str | None | _UnsetType = _UNSET,
    ) -> AugmentResult:
        """DataFrame in / DataFrame out (library path; spec §18.2).

        Returns a fresh :class:`AugmentResult`; does *not* mutate ``df``.

        Column-name kwargs override whatever's in ``config.input.*`` for
        this call only. Three behaviours per kwarg:

        - **Omit** the kwarg → use ``config.input.*`` (or ``None`` if not set).
        - Pass ``"some_column"`` → use that column for this call.
        - Pass ``None`` → disable this locator for this call (useful when
          the configured column is intentionally absent from the
          DataFrame).

        Configured columns that are absent from ``df`` are dropped with a
        WARNING log and listed on
        :attr:`AugmentResult.summary.unused_configured_columns` — the call
        proceeds with the remaining locators rather than failing. If no
        usable locator remains, ``ValueError`` is raised.
        """
        addr_col = self._resolve_override(address_column, self._config.input.address_column)
        lat_col = self._resolve_override(latitude_column, self._config.input.latitude_column)
        lon_col = self._resolve_override(longitude_column, self._config.input.longitude_column)

        addr_col, lat_col, lon_col, unused_configured_columns = self._lenient_drop_absent_columns(
            df, addr_col, lat_col, lon_col
        )

        if not self._has_locator(addr_col, lat_col, lon_col):
            raise ValueError(
                f"augment(df) cannot proceed: no usable locator columns. "
                f"After applying overrides and dropping columns absent from "
                f"the DataFrame, the resolved locators are "
                f"address={addr_col!r}, latitude={lat_col!r}, "
                f"longitude={lon_col!r}. DataFrame columns: "
                f"{list(df.columns)}."
            )

        # Spec §8 guarantees that input columns are preserved unchanged.
        # If a user's DataFrame already has a column named one of the
        # reserved seven (`geo_lat`, `geo_lon`, `geo_source`,
        # `geo_match_score`, `sa2_code`, `sa2_name`, `sa2_resolution`),
        # blindly assigning to those names below would overwrite the
        # user's data and the original value would be lost forever. Fail
        # loud instead.
        input_collisions = sorted(set(df.columns) & _RESERVED_OUTPUT_COLS)
        if input_collisions:
            raise ValueError(
                "augment(df) cannot proceed: input DataFrame has columns "
                "whose names collide with reserved pipeline output columns "
                f"{sorted(_RESERVED_OUTPUT_COLS)!r}. Colliding columns in "
                f"your input: {input_collisions!r}. Rename them before "
                "calling augment() — the pipeline would otherwise silently "
                "overwrite your data (spec §8 guarantees input columns are "
                "preserved)."
            )

        df_out = df.copy()
        original_cols = list(df_out.columns)

        lats, lons, sources, mb_codes, match_scores = self._resolve_coordinates(
            df_out, addr_col=addr_col, lat_col=lat_col, lon_col=lon_col
        )
        df_out[_GEO_LAT_COL] = lats
        df_out[_GEO_LON_COL] = lons
        df_out[_GEO_SOURCE_COL] = sources
        df_out[_GEO_MATCH_SCORE_COL] = match_scores

        codes, names, sa2_resolution = self._resolve_sa2(lats=lats, lons=lons, mb_codes=mb_codes)
        df_out[_SA2_CODE_COL] = codes
        df_out[_SA2_NAME_COL] = names
        df_out[_SA2_RESOLUTION_COL] = sa2_resolution

        # Temporal mode (spec-temporal.md §9): when input.date_column is
        # set, bucket rows by per-dataset release and enrich each bucket
        # with its own release-overridden enricher. Cross-sectional mode
        # uses the configured enricher directly.
        if self._config.input.date_column is not None:
            df_out, releases_used = self._enrich_temporal(df, df_out)
        else:
            df_out = self._enricher.add_enrichment_columns(df_out, sa2_code_col=_SA2_CODE_COL)
            releases_used = None
        df_out = self._reorder_output_columns(df_out, original_cols)

        summary = self._build_summary(df_out, sources, sa2_resolution)
        added_columns = [c for c in df_out.columns if c not in original_cols]

        idx = df_out.index
        geocoding_failed = pd.Series(
            [s == "failed" for s in sources],
            index=idx,
            dtype=bool,
            name="geocoding_failed",
        )
        has_coords = df_out[_GEO_LAT_COL].notna() & df_out[_GEO_LON_COL].notna()
        has_sa2 = df_out[_SA2_CODE_COL].notna()
        sa2_unmatched = (has_coords & ~has_sa2).rename("sa2_unmatched")

        prefix = self._config.output.prefix
        enrichment_cols = [f"{prefix}{name}" for name in self._config.variables]
        # Same defensive check as _build_summary: handle the case where
        # the enricher didn't populate the expected columns (e.g. when a
        # test injects a stub enricher with variables={}).
        if enrichment_cols and all(c in df_out.columns for c in enrichment_cols):
            is_fully_enriched = (
                df_out[enrichment_cols].notna().all(axis=1).rename("is_fully_enriched")
            )
        else:
            is_fully_enriched = pd.Series(
                [False] * len(df_out),
                index=idx,
                dtype=bool,
                name="is_fully_enriched",
            )

        # Attach unused-columns to the summary so the CLI's "Run summary"
        # output shows them and library callers can read them off the
        # AugmentResult.
        summary_with_unused = RunSummary(
            **{**summary.__dict__, "unused_configured_columns": unused_configured_columns}
        )

        # Diagnostic: when rows came back partially enriched, name the
        # specific variables that nulled out. The summary already has
        # the aggregate count; this surfaces which configured variable
        # didn't resolve so users can pinpoint (e.g.) "SA2s outside
        # SEIFA coverage" without having to inspect the output CSV.
        if (
            summary_with_unused.partially_enriched > 0
            and enrichment_cols
            and all(c in df_out.columns for c in enrichment_cols)
        ):
            enriched_df = df_out.loc[has_sa2, enrichment_cols]
            for col in enrichment_cols:
                null_count = int(enriched_df[col].isna().sum())
                if null_count > 0:
                    _log.info(
                        "Enrichment column %r: %d/%d SA2-resolved rows null",
                        col,
                        null_count,
                        len(enriched_df),
                    )

        return AugmentResult(
            df=df_out,
            summary=summary_with_unused,
            added_columns=added_columns,
            is_fully_enriched=is_fully_enriched,
            geocoding_failed=geocoding_failed,
            sa2_unmatched=sa2_unmatched,
            releases_used=releases_used,
        )

    # ---- internals -------------------------------------------------------

    def _validate_no_column_collisions(self) -> None:
        prefix = self._config.output.prefix
        enrichment_columns = {f"{prefix}{name}" for name in self._config.variables}
        collisions = enrichment_columns & _RESERVED_OUTPUT_COLS
        if collisions:
            raise ValueError(
                f"Variable names collide with reserved output columns: "
                f"{sorted(collisions)}. Rename them or change `output.prefix`."
            )

    @staticmethod
    def _has_locator(
        addr_col: str | None,
        lat_col: str | None,
        lon_col: str | None,
    ) -> bool:
        return addr_col is not None or (lat_col is not None and lon_col is not None)

    @staticmethod
    def _resolve_override(override: str | None | _UnsetType, configured: str | None) -> str | None:
        """Apply a per-call kwarg override against the config default.

        Sentinel ``_UNSET`` means "use the configured default";
        ``None`` and ``"some_column"`` are explicit values.
        """
        if isinstance(override, _UnsetType):
            return configured
        return override

    @staticmethod
    def _lenient_drop_absent_columns(
        df: pd.DataFrame,
        addr_col: str | None,
        lat_col: str | None,
        lon_col: str | None,
    ) -> tuple[str | None, str | None, str | None, list[str]]:
        """Treat configured-but-absent columns as not configured.

        Logs a WARNING per absent column and returns them in
        ``unused_configured_columns`` so the run summary can surface them.
        Lat/lon must still be paired — if only one of them survives the
        absence check, the other is dropped to keep the contract clean
        (the surviving column is *not* listed as unused since it WAS
        present in ``df``).
        """
        unused: list[str] = []

        if addr_col is not None and addr_col not in df.columns:
            _log.warning(
                "Configured address column %r is not in the DataFrame; "
                "address-fallback disabled for this call",
                addr_col,
            )
            unused.append(addr_col)
            addr_col = None

        if lat_col is not None and lat_col not in df.columns:
            _log.warning(
                "Configured latitude column %r is not in the DataFrame; "
                "lat/lon resolution disabled",
                lat_col,
            )
            unused.append(lat_col)
            lat_col = None

        if lon_col is not None and lon_col not in df.columns:
            _log.warning(
                "Configured longitude column %r is not in the DataFrame; "
                "lat/lon resolution disabled",
                lon_col,
            )
            unused.append(lon_col)
            lon_col = None

        # Lat and lon come as a pair. If only one survived absence-check,
        # drop the other (it can't be used alone). The orphan was present
        # in df so don't list it as unused.
        if (lat_col is None) ^ (lon_col is None):
            lat_col = None
            lon_col = None

        return addr_col, lat_col, lon_col, unused

    def _resolve_coordinates(
        self,
        df: pd.DataFrame,
        *,
        addr_col: str | None,
        lat_col: str | None,
        lon_col: str | None,
    ) -> tuple[
        list[float | None],
        list[float | None],
        list[str],
        list[str | None],
        list[float | None],
    ]:
        """For each row, decide source and resolve lat/lon (spec §7.1, §7.2).

        Returns parallel lists ``(lats, lons, sources, mb_codes,
        match_scores)``. Precedence: lat/lon > address. For address
        rows, geocoders in ``self._geocoders`` are tried in order; the
        first non-failed result wins (spec §7.2). On miss across the
        whole chain, the row is marked ``failed`` and lat/lon are
        ``None``.

        ``mb_codes`` carries the geocoder's ABS Mesh Block code where
        produced (G-NAF rows). ``match_scores`` carries the fuzzy
        similarity score for ``gnaf_fuzzy`` rows; everything else gets
        ``None``.
        """
        has_latlon = lat_col is not None and lon_col is not None
        has_address = addr_col is not None

        lats: list[float | None] = []
        lons: list[float | None] = []
        sources: list[str] = []
        mb_codes: list[str | None] = []
        match_scores: list[float | None] = []

        for _, row in df.iterrows():
            if has_latlon:
                lat_val = row[cast(str, lat_col)]
                lon_val = row[cast(str, lon_col)]
                if pd.notna(lat_val) and pd.notna(lon_val):
                    lats.append(float(lat_val))
                    lons.append(float(lon_val))
                    sources.append("input")
                    mb_codes.append(None)
                    match_scores.append(None)
                    continue

            if has_address:
                addr_val = row[cast(str, addr_col)]
                if pd.notna(addr_val) and str(addr_val).strip():
                    result = self._geocode_with_chain(str(addr_val))
                    lats.append(result.lat)
                    lons.append(result.lon)
                    sources.append(result.source)
                    mb_codes.append(result.mb_code)
                    match_scores.append(result.match_score)
                    continue

            lats.append(None)
            lons.append(None)
            sources.append("failed")
            mb_codes.append(None)
            match_scores.append(None)

        return lats, lons, sources, mb_codes, match_scores

    def _geocode_with_chain(self, address: str) -> GeocodeResult:
        """Walk ``self._geocoders`` in order; first non-failed wins.

        Returns the last failure if every provider fails — that way the
        caller still gets a well-formed ``GeocodeResult`` carrying the
        original input. Logs at DEBUG when a provider falls through to
        the next.
        """
        last: GeocodeResult | None = None
        for provider in self._geocoders:
            result = provider.geocode(address)
            last = result
            if result.is_success:
                return result
            _log.debug(
                "Geocoder %s missed for %r; trying next provider",
                result.provider,
                address,
            )
        # Every provider missed. Return the last result so the caller has
        # a populated address_input/normalized; if for some reason the
        # geocoder list ran but produced nothing (impossible after the
        # constructor's empty-list guard), fall back to a synthetic
        # failure record so we never return None.
        if last is None:  # pragma: no cover — guarded in __init__
            from datetime import datetime, timezone

            return GeocodeResult(
                address_input=address,
                address_normalized=address,
                lat=None,
                lon=None,
                source="failed",
                provider="pipeline",
                timestamp=datetime.now(timezone.utc),
            )
        return last

    def _resolve_sa2(
        self,
        *,
        lats: list[float | None],
        lons: list[float | None],
        mb_codes: list[str | None],
    ) -> tuple[list[str | None], list[str | None], list[str | None]]:
        """Per-row SA2 lookup with the §7.3 fast path.

        Returns parallel ``(sa2_codes, sa2_names, resolutions)`` lists.
        ``resolutions[i]`` is one of ``"mb_code"`` (fast-path hit),
        ``"spatial_join"`` (fallback hit), or ``"unmatched"`` (had
        coords but no SA2). Rows with no coords get ``None`` in all
        three positions — they're already represented as ``failed`` in
        ``geo_source``.

        Implementation note (spec §7.3): rows are partitioned into
        MB-fast-path vs spatial-join groups and each group is processed
        in one batch — dict lookup for the former, single ``sjoin`` for
        the latter. Results re-merged to the original index order.
        """
        n = len(lats)
        codes_out: list[str | None] = [None] * n
        names_out: list[str | None] = [None] * n
        resolutions: list[str | None] = [None] * n

        spatial_indices: list[int] = []
        spatial_lats: list[float | None] = []
        spatial_lons: list[float | None] = []

        for i in range(n):
            mb = mb_codes[i]
            if mb is not None and mb in self._mb_lookup:
                info = self._mb_lookup[mb]
                codes_out[i] = info.sa2_code
                names_out[i] = info.sa2_name
                resolutions[i] = _SA2_RES_MB_CODE
                continue
            # Spatial-join fallback: row has coords but no MB hit (could
            # be a Nominatim row, a lat/lon-input row, or a G-NAF row
            # whose mb_code wasn't in the correspondence dict).
            if lats[i] is not None and lons[i] is not None:
                spatial_indices.append(i)
                spatial_lats.append(lats[i])
                spatial_lons.append(lons[i])

        if spatial_indices:
            sj_codes, sj_names = self._spatial.lookup_many(spatial_lats, spatial_lons)
            for offset, orig_idx in enumerate(spatial_indices):
                code = sj_codes[offset]
                name = sj_names[offset]
                if code is None:
                    resolutions[orig_idx] = _SA2_RES_UNMATCHED
                    continue
                codes_out[orig_idx] = code
                names_out[orig_idx] = name
                resolutions[orig_idx] = _SA2_RES_SPATIAL

        return codes_out, names_out, resolutions

    def _reorder_output_columns(self, df: pd.DataFrame, original_cols: list[str]) -> pd.DataFrame:
        prefix = self._config.output.prefix
        # Per-dataset <dataset_id>_release columns added by temporal mode
        # (spec-temporal.md §11). In cross-sectional mode these don't
        # exist; in temporal mode they slot in after the SA2 trio and
        # before the enrichment values.
        release_cols = sorted(c for c in df.columns if c.endswith("_release"))
        desired = (
            original_cols
            + [_GEO_LAT_COL, _GEO_LON_COL, _GEO_SOURCE_COL, _GEO_MATCH_SCORE_COL]
            + [_SA2_CODE_COL, _SA2_NAME_COL, _SA2_RESOLUTION_COL]
            + release_cols
            + [f"{prefix}{name}" for name in self._config.variables]
        )
        existing = [c for c in desired if c in df.columns]
        return df[existing]

    def _build_summary(
        self,
        df: pd.DataFrame,
        sources: list[str],
        sa2_resolution: list[str | None],
    ) -> RunSummary:
        # Per-tier histogram (spec §7.5). Initialise every known tier to
        # zero so consumers can index any value without KeyError.
        geo_per_tier: dict[str, int] = {tier: 0 for tier in _GEO_SOURCE_VALUES}
        for s in sources:
            geo_per_tier[s] = geo_per_tier.get(s, 0) + 1

        # v1.0: source values are provider-prefixed (spec §8 / §19.1).
        # The legacy aggregates remain so existing summary readers keep
        # working — they classify across the new value set.
        geo_input = geo_per_tier.get("input", 0)
        geo_cache = geo_per_tier.get("nominatim_cache", 0)
        geo_fresh = (
            geo_per_tier.get("nominatim_fresh", 0)
            + geo_per_tier.get("gnaf_exact", 0)
            + geo_per_tier.get("gnaf_component", 0)
            + geo_per_tier.get("gnaf_fuzzy", 0)
        )
        geo_failed = geo_per_tier.get("failed", 0)

        # SA2 resolution path histogram (spec §7.5).
        sa2_resolution_counts: dict[str, int] = {
            _SA2_RES_MB_CODE: 0,
            _SA2_RES_SPATIAL: 0,
            _SA2_RES_UNMATCHED: 0,
        }
        for res in sa2_resolution:
            if res is None:
                continue
            sa2_resolution_counts[res] = sa2_resolution_counts.get(res, 0) + 1

        has_coords = df[_GEO_LAT_COL].notna() & df[_GEO_LON_COL].notna()
        has_sa2 = df[_SA2_CODE_COL].notna()
        sa2_unmatched = int((has_coords & ~has_sa2).sum())

        prefix = self._config.output.prefix
        enrichment_cols = [f"{prefix}{name}" for name in self._config.variables]
        if enrichment_cols and all(c in df.columns for c in enrichment_cols):
            all_enriched = df[enrichment_cols].notna().all(axis=1)
            fully_enriched = int((has_sa2 & all_enriched).sum())
            partially_enriched = int((has_sa2 & ~all_enriched).sum())
        else:
            fully_enriched = 0
            partially_enriched = 0

        return RunSummary(
            total_rows=len(df),
            geo_input=geo_input,
            geo_cache=geo_cache,
            geo_fresh=geo_fresh,
            geo_failed=geo_failed,
            sa2_unmatched=sa2_unmatched,
            fully_enriched=fully_enriched,
            partially_enriched=partially_enriched,
            geo_per_tier=geo_per_tier,
            sa2_resolution_counts=sa2_resolution_counts,
        )

    # ---- temporal-mode orchestrator (spec-temporal.md §9) ----------------

    def _enrich_temporal(
        self,
        original_df: pd.DataFrame,
        df_out: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, list[str]]]:
        """Per-bucket enrichment for temporal mode.

        Resolves a per-dataset release for each row, buckets rows by the
        per-dataset release tuple, then for each bucket runs the
        configured enricher with that bucket's release overrides and
        concatenates the results.

        Adds per-dataset ``<dataset_id>_release`` columns to the output.

        Phase E.2 scope: single-edition only. If any resolved release
        is on an ASGS edition other than ``temporal.reference_edition``,
        raises a clear error pointing at Phase F (cross-edition support).

        Returns ``(enriched_df, releases_used)`` where ``releases_used``
        maps dataset id → sorted list of releases touched.
        """
        from collections import defaultdict  # noqa: PLC0415
        from datetime import date  # noqa: PLC0415

        from ._temporal import resolve_release, to_date  # noqa: PLC0415
        from .datasets import registry  # noqa: PLC0415

        date_col = self._config.input.date_column
        assert date_col is not None  # narrowed by caller

        if date_col not in original_df.columns:
            raise ValueError(
                f"input.date_column {date_col!r} is not in the input DataFrame; "
                f"columns are: {list(original_df.columns)}"
            )

        temporal_cfg = self._config.temporal
        reference_edition = temporal_cfg.reference_edition

        # Coerce each row's date column value to a `date`. Bad rows fail
        # loud — same pattern as the rest of the pipeline.
        try:
            row_dates: list[date] = [to_date(v) for v in original_df[date_col].tolist()]
        except ValueError as e:
            raise ValueError(f"input.date_column {date_col!r} has unparseable values: {e}") from e

        # Identify which datasets the configured variables touch, and
        # for each, fetch the registered TemporalDatasetMetadata.
        # PRESET refs are also expanded — they're stored at the
        # PRESET registry level and reference underlying datasets.
        touched_dataset_ids = self._touched_dataset_ids()

        dataset_metadata: dict[str, "TemporalDatasetMetadata"] = {}
        for did in touched_dataset_ids:
            try:
                spec = registry.get(did)
            except Exception:  # noqa: BLE001 — RegistryError, NameError, ...
                # Dataset not in the registry — skip (treat as not
                # temporal-aware).
                continue
            if spec.temporal is None:
                _log.warning(
                    "Dataset %r has no `temporal:` block declared; falling back "
                    "to its configured release for all rows.",
                    did,
                )
                continue
            dataset_metadata[did] = spec.temporal

        # Per-row, per-dataset: resolve the release id.
        # row_release_tuples[i] is a tuple ((dataset_id, release_id), ...)
        # sorted by dataset_id so identical tuples bucket together.
        row_release_tuples: list[tuple[tuple[str, str], ...]] = []
        for i, d in enumerate(row_dates):
            row_releases: list[tuple[str, str]] = []
            for did in sorted(dataset_metadata):
                md = dataset_metadata[did]
                # Per-dataset resolution override?
                per_ds = temporal_cfg.per_dataset.get(did)
                rule = (
                    per_ds.resolution if per_ds and per_ds.resolution else temporal_cfg.resolution
                )
                rel = resolve_release(
                    d,
                    metadata=md,
                    rule=rule,
                    out_of_range=temporal_cfg.out_of_range,
                    dataset_id=did,
                    row_index=int(original_df.index[i])
                    if isinstance(original_df.index[i], (int, str))
                    else i,
                )
                row_releases.append((did, rel))
            row_release_tuples.append(tuple(row_releases))

        # Single-edition validation (Phase E.2 scope).
        # Walk every (dataset, release) used; check each release's
        # asgs_edition_by_release entry matches reference_edition.
        for did, md in dataset_metadata.items():
            used_releases = {rel for tup in row_release_tuples for d2, rel in tup if d2 == did}
            for rel in used_releases:
                src_edition = md.asgs_edition_by_release.get(rel)
                if src_edition is None:
                    _log.warning(
                        "Dataset %r release %r has no asgs_edition_by_release "
                        "entry; assuming reference_edition=%d.",
                        did,
                        rel,
                        reference_edition,
                    )
                    continue
                if src_edition != reference_edition:
                    raise NotImplementedError(
                        f"Temporal-mode row resolved {did!r} → release {rel!r} "
                        f"(ASGS Edition {src_edition}), but the configured "
                        f"reference_edition is {reference_edition}. "
                        f"Cross-edition boundary lookup is Phase F work — until "
                        f"then, every resolved release must be on the reference "
                        f"edition. Either pin earlier ASGS-edition data out of "
                        f"the input, or pre-filter rows to dates that all "
                        f"resolve to Edition-{reference_edition} releases."
                    )

        # Bucket rows by release tuple.
        buckets: dict[tuple[tuple[str, str], ...], list[int]] = defaultdict(list)
        for i, tup in enumerate(row_release_tuples):
            buckets[tup].append(i)

        # Per-bucket: build a sub-enricher with the bucket's release
        # overrides, run add_enrichment_columns, concat.
        enriched_parts: list[pd.DataFrame] = []
        releases_used: dict[str, set[str]] = defaultdict(set)
        for bucket_releases, row_positions in buckets.items():
            sub_enricher = self._enricher_for_bucket(dict(bucket_releases))
            bucket_df = df_out.iloc[row_positions]
            enriched_bucket = sub_enricher.add_enrichment_columns(
                bucket_df, sa2_code_col=_SA2_CODE_COL
            )
            enriched_parts.append(enriched_bucket)
            for did, rel in bucket_releases:
                releases_used[did].add(rel)

        # Concat in original row order.
        enriched_full = pd.concat(enriched_parts).sort_index()

        # Append per-dataset release columns.
        for did in sorted(releases_used):
            col_name = f"{did}_release"
            # Build per-row release for this dataset by indexing back.
            release_per_row: list[str | None] = [None] * len(original_df)
            for i, tup in enumerate(row_release_tuples):
                for d2, rel in tup:
                    if d2 == did:
                        release_per_row[i] = rel
                        break
            enriched_full[col_name] = release_per_row

        return enriched_full, {did: sorted(rels) for did, rels in releases_used.items()}

    def _touched_dataset_ids(self) -> set[str]:
        """Set of dataset ids the configured variables resolve to.

        Walks the variables map and resolves each ref's namespace against
        the registry. PRESET refs are followed through to their source
        variables' datasets. GCP refs map to ``gcp_2021``.
        """
        from .datasets import registry  # noqa: PLC0415

        touched: set[str] = set()
        for ref in self._config.variables.values():
            namespace = ref.split(".", 1)[0] if "." in ref else ""
            # GCP-style ``G\d+.<col>`` resolves to the GCP dataset.
            if namespace and namespace[0] == "G" and namespace[1:].isdigit():
                touched.add("gcp_2021")
                continue
            # Otherwise look the namespace up against registered datasets.
            for spec in registry.list_datasets():
                if spec.namespace == namespace:
                    touched.add(spec.id)
                    break
        return touched

    def _enricher_for_bucket(
        self,
        release_overrides: dict[str, str],
    ) -> CensusEnricher:
        """Build a per-bucket :class:`CensusEnricher` with the given
        release overrides. Other state (catalog, variables, output
        prefix, data_dir) is inherited from the configured enricher.
        """
        return CensusEnricher(
            datapacks=self._enricher._datapacks,
            catalog=self._enricher._catalog,
            variables=self._enricher._variables,
            output_prefix=self._enricher._output_prefix,
            data_dir=self._enricher._data_dir,
            dataset_release_overrides=release_overrides,
        )
