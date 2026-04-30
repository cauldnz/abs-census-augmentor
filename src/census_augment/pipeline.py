"""Pipeline orchestration: input -> geocode -> spatial -> enrich -> output (spec §3, §7)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd

from .catalog import VariableCatalog
from .config import Config
from .data_sources.boundaries import BoundariesDataSource
from .data_sources.datapacks import DataPacksDataSource
from .enrich import CensusEnricher
from .geocoding.base import Geocoder
from .geocoding.cache import GeocodeCache
from .geocoding.nominatim import NominatimGeocoder
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


class Pipeline:
    """Orchestrates input -> geocode -> spatial -> enrich -> output.

    Constructed either directly (with pre-built collaborators — useful
    for tests) or via :meth:`from_config` (which wires everything from a
    :class:`~census_augment.config.Config`, including downloading
    boundaries and DataPacks).
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
        data_dir: Path = Path("data"),
        cache_dir: Path = Path("cache"),
    ) -> Pipeline:
        """Build a fully-wired pipeline from config.

        Triggers HTTP downloads for boundaries and DataPacks if not already
        cached locally. Validates the configured ``variables`` references
        against the loaded DataPack metadata (spec §6.2 semantic check).
        """
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

    def run(self) -> RunSummary:
        """Execute the pipeline end-to-end.

        Loads the input CSV, resolves per-row coordinates (input lat/lon
        when present, else geocode), looks up SA2, enriches, writes the
        output CSV per spec §8 column order, and returns the run summary
        per spec §7.5.
        """
        df = pd.read_csv(self._config.input.path)
        original_cols = list(df.columns)
        self._validate_input_columns(df)

        lats, lons, sources = self._resolve_coordinates(df)
        df[_GEO_LAT_COL] = lats
        df[_GEO_LON_COL] = lons
        df[_GEO_SOURCE_COL] = sources

        codes, names = self._spatial.lookup_many(lats, lons)
        df[_SA2_CODE_COL] = codes
        df[_SA2_NAME_COL] = names

        df = self._enricher.add_enrichment_columns(df, sa2_code_col=_SA2_CODE_COL)
        df = self._reorder_output_columns(df, original_cols)

        summary = self._build_summary(df, sources)

        output_path = self._config.output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        _log.info("Wrote %d rows to %s", len(df), output_path)

        return summary

    # ---- internals -------------------------------------------------------

    def _validate_input_columns(self, df: pd.DataFrame) -> None:
        cols_to_check: list[str] = []
        if self._config.input.address_column is not None:
            cols_to_check.append(self._config.input.address_column)
        if self._config.input.latitude_column is not None:
            cols_to_check.append(self._config.input.latitude_column)
        if self._config.input.longitude_column is not None:
            cols_to_check.append(self._config.input.longitude_column)

        missing = [c for c in cols_to_check if c not in df.columns]
        if missing:
            raise ValueError(
                f"Input CSV at {self._config.input.path} is missing "
                f"configured columns: {missing}; got: {list(df.columns)}"
            )

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

    def _resolve_coordinates(
        self, df: pd.DataFrame
    ) -> tuple[list[float | None], list[float | None], list[str]]:
        """For each row, decide source ('input'/'cache'/'fresh'/'failed')
        and resolve lat/lon. Spec §7.1 precedence: lat/lon > address."""
        lat_col = self._config.input.latitude_column
        lon_col = self._config.input.longitude_column
        addr_col = self._config.input.address_column
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

            # No usable input on this row
            lats.append(None)
            lons.append(None)
            sources.append("failed")

        return lats, lons, sources

    def _reorder_output_columns(
        self, df: pd.DataFrame, original_cols: list[str]
    ) -> pd.DataFrame:
        """Output column order per spec §8."""
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
