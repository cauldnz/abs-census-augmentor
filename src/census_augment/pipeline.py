"""Pipeline orchestration: input -> geocode -> spatial -> enrich -> output.

Two entry points (spec §3, §7, §18):

- :meth:`Pipeline.run` — file-in / file-out. Reads ``config.input.path``,
  writes ``config.output.path``. Used by the CLI's ``run`` command.
- :meth:`Pipeline.augment` — DataFrame in / DataFrame out, no file I/O.
  Used by notebooks and library code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
    OutputConfig,
)
from .data_sources.boundaries import BoundariesDataSource
from .data_sources.datapacks import DataPacksDataSource
from .enrich import CensusEnricher
from .geocoding.base import Geocoder
from .geocoding.cache import GeocodeCache
from .geocoding.nominatim import NominatimGeocoder
from .paths import default_cache_dir, default_data_dir
from .spatial import SpatialIndex

_log = logging.getLogger(__name__)

# Output column names per spec §8
_GEO_LAT_COL = "geo_lat"
_GEO_LON_COL = "geo_lon"
_GEO_SOURCE_COL = "geo_source"
_SA2_CODE_COL = "sa2_code"
_SA2_NAME_COL = "sa2_name"
_RESERVED_OUTPUT_COLS = frozenset(
    {_GEO_LAT_COL, _GEO_LON_COL, _GEO_SOURCE_COL, _SA2_CODE_COL, _SA2_NAME_COL}
)


@dataclass(frozen=True)
class RunSummary:
    """End-of-run statistics (spec §7.5).

    ``geo_input`` / ``geo_cache`` / ``geo_fresh`` / ``geo_failed`` partition
    rows by where their lat/lon came from. ``sa2_unmatched`` counts rows that
    *did* have lat/lon but didn't fall in any SA2. ``fully_enriched`` /
    ``partially_enriched`` partition rows that got an SA2 by whether every
    configured variable resolved to a non-null value.
    """

    total_rows: int
    geo_input: int
    geo_cache: int
    geo_fresh: int
    geo_failed: int
    sa2_unmatched: int
    fully_enriched: int
    partially_enriched: int

    def format_human_readable(self) -> str:
        return (
            "Run summary:\n"
            f"  Total rows:           {self.total_rows}\n"
            "  Geocoding source:\n"
            f"    From input lat/lon: {self.geo_input}\n"
            f"    From cache:         {self.geo_cache}\n"
            f"    Freshly geocoded:   {self.geo_fresh}\n"
            f"    Failed:             {self.geo_failed}\n"
            "  SA2 lookup:\n"
            f"    Outside any SA2:    {self.sa2_unmatched}\n"
            "  Enrichment:\n"
            f"    Fully enriched:     {self.fully_enriched}\n"
            f"    Partially enriched: {self.partially_enriched}\n"
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
        geocoder: Geocoder,
        spatial: SpatialIndex,
        enricher: CensusEnricher,
    ) -> None:
        self._config = config
        self._geocoder = geocoder
        self._spatial = spatial
        self._enricher = enricher
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

        boundaries_ds = BoundariesDataSource(
            census=config.census,
            base_url=config.data_sources.boundaries_base_url,
            root=data_dir / "boundaries",
        )
        spatial = SpatialIndex(boundaries_ds.load())

        datapacks_ds = DataPacksDataSource(
            census=config.census,
            base_url=config.data_sources.datapacks_base_url,
            root=data_dir / "census",
        )
        catalog = VariableCatalog.from_data_source(datapacks_ds)
        catalog.validate_variables(config.variables)
        enricher = CensusEnricher(
            datapacks=datapacks_ds,
            catalog=catalog,
            variables=config.variables,
            output_prefix=config.output.prefix,
        )

        geocoder = NominatimGeocoder(
            user_agent=config.geocoding.user_agent,
            cache=GeocodeCache(cache_dir / "geocoding"),
            rate_limit_per_second=config.geocoding.rate_limit_per_second,
        )

        return cls(
            config=config,
            geocoder=geocoder,
            spatial=spatial,
            enricher=enricher,
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
        """
        cfg = Config(
            input=InputConfig(
                address_column=address_column,
                latitude_column=latitude_column,
                longitude_column=longitude_column,
            ),
            output=OutputConfig(prefix=output_prefix),
            census=CensusConfig(),
            data_sources=DataSourcesConfig(),
            geocoding=GeocodingConfig(user_agent=user_agent),
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

        df = pd.read_csv(self._config.input.path)
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
        address_column: str | None = None,
        latitude_column: str | None = None,
        longitude_column: str | None = None,
    ) -> AugmentResult:
        """DataFrame in / DataFrame out (library path; spec §18.2).

        Returns a fresh :class:`AugmentResult`; does *not* mutate ``df``.
        Column-name kwargs override whatever's in ``config.input.*`` for
        this call only — handy when one notebook DataFrame uses a different
        schema than the configured one.

        Raises ``ValueError`` if no locator is configured (neither address
        nor lat/lon) or if a configured column is missing from ``df``.
        """
        addr_col = address_column or self._config.input.address_column
        lat_col = latitude_column or self._config.input.latitude_column
        lon_col = longitude_column or self._config.input.longitude_column

        if not self._has_locator(addr_col, lat_col, lon_col):
            raise ValueError(
                "augment(df) requires at least one locator: pass "
                "address_column, or both latitude_column and longitude_column "
                "(or set them on Config.input)."
            )

        configured_cols = [c for c in (addr_col, lat_col, lon_col) if c is not None]
        missing = [c for c in configured_cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"DataFrame is missing configured columns: {missing}; "
                f"got: {list(df.columns)}"
            )

        df_out = df.copy()
        original_cols = list(df_out.columns)

        lats, lons, sources = self._resolve_coordinates(
            df_out, addr_col=addr_col, lat_col=lat_col, lon_col=lon_col
        )
        df_out[_GEO_LAT_COL] = lats
        df_out[_GEO_LON_COL] = lons
        df_out[_GEO_SOURCE_COL] = sources

        codes, names = self._spatial.lookup_many(lats, lons)
        df_out[_SA2_CODE_COL] = codes
        df_out[_SA2_NAME_COL] = names

        df_out = self._enricher.add_enrichment_columns(
            df_out, sa2_code_col=_SA2_CODE_COL
        )
        df_out = self._reorder_output_columns(df_out, original_cols)

        summary = self._build_summary(df_out, sources)
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
        enrichment_cols = [
            f"{prefix}{name}" for name in self._config.variables
        ]
        if enrichment_cols:
            is_fully_enriched = (
                df_out[enrichment_cols]
                .notna()
                .all(axis=1)
                .rename("is_fully_enriched")
            )
        else:
            is_fully_enriched = pd.Series(
                [False] * len(df_out),
                index=idx,
                dtype=bool,
                name="is_fully_enriched",
            )

        return AugmentResult(
            df=df_out,
            summary=summary,
            added_columns=added_columns,
            is_fully_enriched=is_fully_enriched,
            geocoding_failed=geocoding_failed,
            sa2_unmatched=sa2_unmatched,
        )

    # ---- internals -------------------------------------------------------

    def _validate_no_column_collisions(self) -> None:
        prefix = self._config.output.prefix
        enrichment_columns = {
            f"{prefix}{name}" for name in self._config.variables
        }
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
        return addr_col is not None or (
            lat_col is not None and lon_col is not None
        )

    def _resolve_coordinates(
        self,
        df: pd.DataFrame,
        *,
        addr_col: str | None,
        lat_col: str | None,
        lon_col: str | None,
    ) -> tuple[list[float | None], list[float | None], list[str]]:
        """For each row, decide source ('input'/'cache'/'fresh'/'failed')
        and resolve lat/lon. Spec §7.1 precedence: lat/lon > address."""
        has_latlon = lat_col is not None and lon_col is not None
        has_address = addr_col is not None

        lats: list[float | None] = []
        lons: list[float | None] = []
        sources: list[str] = []

        for _, row in df.iterrows():
            if has_latlon:
                lat_val = row[cast(str, lat_col)]
                lon_val = row[cast(str, lon_col)]
                if pd.notna(lat_val) and pd.notna(lon_val):
                    lats.append(float(lat_val))
                    lons.append(float(lon_val))
                    sources.append("input")
                    continue

            if has_address:
                addr_val = row[cast(str, addr_col)]
                if pd.notna(addr_val) and str(addr_val).strip():
                    result = self._geocoder.geocode(str(addr_val))
                    lats.append(result.lat)
                    lons.append(result.lon)
                    sources.append(result.source)
                    continue

            lats.append(None)
            lons.append(None)
            sources.append("failed")

        return lats, lons, sources

    def _reorder_output_columns(
        self, df: pd.DataFrame, original_cols: list[str]
    ) -> pd.DataFrame:
        prefix = self._config.output.prefix
        desired = (
            original_cols
            + [_GEO_LAT_COL, _GEO_LON_COL, _GEO_SOURCE_COL]
            + [_SA2_CODE_COL, _SA2_NAME_COL]
            + [f"{prefix}{name}" for name in self._config.variables]
        )
        existing = [c for c in desired if c in df.columns]
        return df[existing]

    def _build_summary(
        self, df: pd.DataFrame, sources: list[str]
    ) -> RunSummary:
        geo_input = sum(1 for s in sources if s == "input")
        geo_cache = sum(1 for s in sources if s == "cache")
        geo_fresh = sum(1 for s in sources if s == "fresh")
        geo_failed = sum(1 for s in sources if s == "failed")

        has_coords = df[_GEO_LAT_COL].notna() & df[_GEO_LON_COL].notna()
        has_sa2 = df[_SA2_CODE_COL].notna()
        sa2_unmatched = int((has_coords & ~has_sa2).sum())

        prefix = self._config.output.prefix
        enrichment_cols = [
            f"{prefix}{name}" for name in self._config.variables
        ]
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
        )
