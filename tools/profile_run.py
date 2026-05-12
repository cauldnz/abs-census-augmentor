"""Per-phase profiler for `census-augment run`.

Splits `Pipeline.from_config` + `Pipeline.augment` + the file-I/O wrappers
into discrete phases and prints a wall-clock breakdown, so we can see
which subsystem owns the time on a warm cache.

Use this to triangulate the 35s/16%-CPU symptom from issue #43:

    uv run python tools/profile_run.py --config tools/demo/config.yaml

Phases timed:

- `import`              — module-level imports for the CLI entry point
- `config_load`         — YAML parse + Pydantic validation
- `boundaries.load`     — SA2 shapefile read (~50 MB SHP+DBF+SHX)
- `SpatialIndex`        — STR-tree build over the polygons
- `datapacks.metadata`  — VariableCatalog.from_data_source (119-table parse)
- `mb_correspondence`   — MB .dbf load (only if 'gnaf' in providers)
- `geocoders`           — NominatimGeocoder / GnafGeocoder construction
- `enricher.init`       — CensusEnricher constructor (lazy or eager?)
- `read_csv`            — pd.read_csv(config.input.path)
- `augment`             — Pipeline.augment(df) — the actual work
- `to_csv`              — result.df.to_csv(config.output.path)
- `total`               — overall wall-clock (sanity check)

Designed to be import-fast itself (no module-level census_augment
imports) so the `import` phase reflects a cold-CLI cost.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any


def _phase(label: str, start: float, results: list[tuple[str, float]]) -> float:
    """Append `(label, elapsed)` and return new t-start for the next phase."""
    now = time.perf_counter()
    results.append((label, now - start))
    return now


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to a config.yaml (same as `census-augment run --config`)",
    )
    args = parser.parse_args()

    total_t0 = time.perf_counter()
    results: list[tuple[str, float]] = []
    t = total_t0

    # --- imports -------------------------------------------------------
    from census_augment.catalog import VariableCatalog  # noqa: F401
    from census_augment.config import load_config
    from census_augment.data_sources.boundaries import BoundariesDataSource
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.enrich import CensusEnricher
    from census_augment.geocoding.cache import GeocodeCache, NullCache
    from census_augment.geocoding.nominatim import NominatimGeocoder
    from census_augment.data_sources.mb_correspondence import MbCorrespondenceDataSource
    from census_augment.paths import default_cache_dir, default_data_dir
    from census_augment.pipeline import Pipeline, _is_gcp_variable_ref
    from census_augment.spatial import SpatialIndex

    import pandas as pd

    t = _phase("import", t, results)

    # --- config load ---------------------------------------------------
    config = load_config(args.config)
    t = _phase("config_load", t, results)

    data_dir = default_data_dir()
    cache_dir = default_cache_dir()

    # --- boundaries + spatial index ------------------------------------
    boundaries_ds = BoundariesDataSource(
        census=config.census,
        base_url=config.data_sources.boundaries_base_url,
        root=data_dir / "boundaries",
    )
    gdf = boundaries_ds.load()
    t = _phase("boundaries.load", t, results)

    spatial = SpatialIndex(gdf)
    t = _phase("SpatialIndex", t, results)

    # --- datapacks metadata + catalog ----------------------------------
    datapacks_ds = DataPacksDataSource(
        census=config.census,
        base_url=config.data_sources.datapacks_base_url,
        root=data_dir / "census",
    )
    catalog = VariableCatalog.from_data_source(datapacks_ds)
    gcp_variables = {
        friendly: ref for friendly, ref in config.variables.items() if _is_gcp_variable_ref(ref)
    }
    catalog.validate_variables(gcp_variables)
    t = _phase("datapacks.metadata", t, results)

    # --- mb correspondence (only if gnaf configured) -------------------
    if "gnaf" in config.geocoding.providers:
        mb_ds = MbCorrespondenceDataSource(
            year=config.census.year,
            datum=config.geocoding.gnaf.datum,
            base_url=config.data_sources.boundaries_base_url,
            root=data_dir / "mb",
        )
        mb_lookup: Any = mb_ds.load_correspondence()
    else:
        mb_lookup = None
    t = _phase("mb_correspondence", t, results)

    # --- geocoders -----------------------------------------------------
    cache: Any
    if config.geocoding.cache_enabled:
        cache = GeocodeCache(cache_dir / "geocoding")
    else:
        cache = NullCache()

    geocoders: list[Any] = []
    for provider in config.geocoding.providers:
        if provider == "nominatim":
            ncfg = config.geocoding.nominatim
            geocoders.append(
                NominatimGeocoder(
                    user_agent=ncfg.user_agent or "profiler/0.0",
                    cache=cache,
                    rate_limit_per_second=ncfg.rate_limit_per_second,
                )
            )
    t = _phase("geocoders", t, results)

    # --- enricher ------------------------------------------------------
    enricher = CensusEnricher(
        datapacks=datapacks_ds,
        catalog=catalog,
        variables=config.variables,
        output_prefix=config.output.prefix,
        data_dir=data_dir,
    )
    t = _phase("enricher.init", t, results)

    pipeline = Pipeline(
        config=config,
        geocoders=geocoders,
        spatial=spatial,
        enricher=enricher,
        mb_lookup=mb_lookup,
    )
    t = _phase("Pipeline.__init__", t, results)

    # --- read_csv ------------------------------------------------------
    assert config.input.path is not None
    df = pd.read_csv(config.input.path)
    t = _phase("read_csv", t, results)

    # --- augment -------------------------------------------------------
    result = pipeline.augment(df)
    t = _phase("augment", t, results)

    # --- to_csv --------------------------------------------------------
    assert config.output.path is not None
    config.output.path.parent.mkdir(parents=True, exist_ok=True)
    result.df.to_csv(config.output.path, index=False)
    t = _phase("to_csv", t, results)

    total = time.perf_counter() - total_t0

    # --- report --------------------------------------------------------
    print()
    print(f"{'phase':<24} {'seconds':>9}  {'%':>5}")
    print("-" * 42)
    for label, elapsed in results:
        pct = 100.0 * elapsed / total
        print(f"{label:<24} {elapsed:>9.3f}  {pct:>4.1f}%")
    print("-" * 42)
    print(f"{'TOTAL':<24} {total:>9.3f}  100.0%")
    print()
    print(f"Rows processed:           {len(result.df)}")
    print(f"Fully enriched:           {result.summary.fully_enriched}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
