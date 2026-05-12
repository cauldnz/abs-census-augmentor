"""Typer-based CLI for census-augment (spec §11).

Commands:
    run        Execute the augmentation pipeline.
    discover   Search census variables by keyword or list a table's columns.
    fetch      Pre-fetch ABS data (boundaries, DataPacks, G-NAF, or all).
    validate   Structurally validate a config file (with ``--full`` also
               validates variable refs against the loaded DataPack).
    gnaf-info  Show the resolved G-NAF release, mode, on-disk path, size.

Global options:
    --verbose / -v   Switch logging from INFO to DEBUG.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests
import typer
from pydantic import ValidationError

from .catalog import CatalogError, VariableCatalog
from .config import load_config
from .data_sources.boundaries import BoundariesDataSource
from .data_sources.datapacks import DataPacksDataSource
from .data_sources.gnaf import GnafDataSource
from .data_sources.mb_correspondence import MbCorrespondenceDataSource
from .paths import default_data_dir
from .pipeline import Pipeline

app = typer.Typer(
    help="Augment Australian location datasets with ABS Census data at SA2 level.",
    no_args_is_help=True,
)

_log = logging.getLogger(__name__)


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging (DEBUG level)."),
) -> None:
    """Configure logging once for the whole command."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(name)s: %(message)s")


_DATA_DIR_HELP = (
    "Where to cache ABS downloads. Defaults to the platform user cache "
    "(e.g. ~/.cache/census-augment/data on Linux). Override via the "
    "CENSUS_AUGMENT_DATA_DIR env var or this flag."
)
_CACHE_DIR_HELP = (
    "Where to keep the geocoding cache. Defaults to the platform user "
    "cache. Override via the CENSUS_AUGMENT_CACHE_DIR env var or this flag."
)

#: G-NAF attribution string per Geoscape's Open G-NAF EULA (spec §19.5).
#: Printed on every G-NAF fetch.
_GNAF_ATTRIBUTION = (
    "Incorporates or developed using G-NAF © Geoscape Australia licensed "
    "by the Commonwealth of Australia under the Open Geo-coded National "
    "Address File (G-NAF) End User Licence Agreement."
)


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    cache_dir: Path | None = typer.Option(None, "--cache-dir", help=_CACHE_DIR_HELP),
) -> None:
    """Run the augmentation pipeline (input CSV -> enriched output CSV)."""
    cfg = load_config(config)
    if cfg.input.path is None or cfg.output.path is None:
        missing = []
        if cfg.input.path is None:
            missing.append("input.path")
        if cfg.output.path is None:
            missing.append("output.path")
        typer.echo(
            f"Error: the run command requires {' and '.join(missing)} to be set in {config}.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Catch the obvious failure modes and surface a one-line "Error:
    # ..." message + exit 1, so users get a usable diagnostic instead
    # of a raw Python traceback. Bare tracebacks are still available
    # via `-v` / `--verbose` (logging picks up DEBUG-level info).
    try:
        pipeline = Pipeline.from_config(cfg, data_dir=data_dir, cache_dir=cache_dir)
        summary = pipeline.run()
    except CatalogError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except ValidationError as e:
        typer.echo(f"Error: config validation failed:\n{e}", err=True)
        raise typer.Exit(code=1) from e
    except requests.HTTPError as e:
        # Reached from ABS / data.gov.au after retries are exhausted
        # (see census_augment._http_retry). Surface the URL +
        # status code, not a full traceback.
        status = e.response.status_code if e.response is not None else "?"
        url = e.response.url if e.response is not None else "<unknown URL>"
        typer.echo(
            f"Error: ABS data fetch failed (HTTP {status} from {url}). "
            f"Check connectivity, the configured base URL, and whether "
            f"the upstream resource still exists.",
            err=True,
        )
        raise typer.Exit(code=1) from e
    except requests.ConnectionError as e:
        typer.echo(
            f"Error: could not reach ABS / data.gov.au "
            f"({e.__class__.__name__}: {e}). Check connectivity.",
            err=True,
        )
        raise typer.Exit(code=1) from e
    except ValueError as e:
        # Pipeline column-collision / locator-resolution errors land here.
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    except RuntimeError as e:
        # Parser / extract failures (e.g. "No table CSVs found in ZIP")
        # land here.
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    typer.echo(summary.format_human_readable())


@app.command()
def discover(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    search: str | None = typer.Option(
        None, "--search", help="Substring to search in column codes / descriptions."
    ),
    table: str | None = typer.Option(
        None, "--table", help="Table ID (e.g. G02) to list all columns of."
    ),
    datasets: bool = typer.Option(
        False, "--datasets", help="List all registered datasets (spec §20)."
    ),
    dataset: str | None = typer.Option(
        None, "--dataset", help="Show schema of one registered dataset by id."
    ),
    features_only: bool = typer.Option(
        False, "--features", help="List PRESET features (spec §21)."
    ),
) -> None:
    """Search census variables, list datasets, or show feature catalogue."""
    # ---- v1.3: dataset listing (spec §20) ----
    if datasets:
        from .datasets import registry as dataset_registry  # noqa: PLC0415

        specs = dataset_registry.list_datasets()
        if not specs:
            typer.echo("No datasets registered.")
            return
        for spec in specs:
            typer.echo(
                f"{spec.id}\tnamespace={spec.namespace}\t"
                f"status={spec.status}\tcadence={spec.update_cadence}\t"
                f"licence={spec.licence}"
            )
        return

    if dataset is not None:
        from .datasets import registry as dataset_registry  # noqa: PLC0415
        from .datasets._registry import RegistryError  # noqa: PLC0415

        try:
            spec = dataset_registry.get(dataset)
        except RegistryError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"{spec.id}: {spec.name}")
        typer.echo(f"  Status: {spec.status}")
        typer.echo(f"  Custodian: {spec.custodian}")
        typer.echo(f"  Licence: {spec.licence}")
        typer.echo(f"  Update cadence: {spec.update_cadence}")
        typer.echo(f"  Geography: {spec.geography_level} ({spec.geography_edition})")
        typer.echo(f"  Namespace: {spec.namespace}")
        if spec.variables:
            typer.echo(f"  Variables ({len(spec.variables)}):")
            for v in spec.variables:
                typer.echo(f"    {spec.namespace}.{v.field}\t{v.type}\t{v.description}")
        return

    # ---- v1.3: feature listing (spec §21) ----
    if features_only:
        from .features import features as feature_registry  # noqa: PLC0415

        feature_specs = feature_registry.list_features()
        if not feature_specs:
            typer.echo("No features registered.")
            return
        for fspec in feature_specs:
            datasets_used = (
                fspec.dataset if isinstance(fspec.dataset, str) else "+".join(fspec.dataset)
            )
            typer.echo(
                f"PRESET.{fspec.id}\tkind={fspec.output_kind}\t"
                f"dataset={datasets_used}\ttags={fspec.tags}"
            )
        return

    if search is None and table is None:
        typer.echo(
            "Error: provide --search, --table, --datasets, --dataset, or --features.",
            err=True,
        )
        raise typer.Exit(code=2)
    if search is not None and table is not None:
        typer.echo("Error: --search and --table are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    cfg = load_config(config)
    effective_data_dir = data_dir if data_dir is not None else default_data_dir()
    datapacks = DataPacksDataSource(
        census=cfg.census,
        base_url=cfg.data_sources.datapacks_base_url,
        root=effective_data_dir / "census" / str(cfg.census.year),
    )
    catalog = VariableCatalog.from_data_source(datapacks)

    if search is not None:
        results = catalog.search(search)
        if not results:
            typer.echo(f"No matches for {search!r}.")
            return
        for col in results:
            typer.echo(f"{col.table_id}.{col.code}\t{col.description}")
        return

    assert table is not None  # narrowed by the checks above
    try:
        cols = catalog.list_table(table)
    except CatalogError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e
    table_meta = catalog.metadata.tables[table]
    typer.echo(f"Table {table}: {table_meta.name}")
    for col in cols:
        typer.echo(f"  {col.code}\t{col.description}")


@app.command()
def fetch(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    boundaries: bool = typer.Option(
        False, "--boundaries", help="Pre-fetch the SA2 boundary shapefile."
    ),
    census: bool = typer.Option(False, "--census", help="Pre-fetch the Census DataPack."),
    gnaf: bool = typer.Option(
        False,
        "--gnaf",
        help=(
            "Pre-fetch the G-NAF Core dataset (anonymous S3 download "
            "from the configured gnaf-loader bucket; ~10 GB across ~50 "
            "parquet files). Also fetches the Mesh Block correspondence "
            "shapefile alongside (used for the §7.3 fast path). "
            "Idempotent — re-running is cheap when the cache is warm."
        ),
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force re-download even if cached."),
) -> None:
    """Pre-fetch ABS data (saves the first --run from doing the download)."""
    if not (boundaries or census or gnaf):
        typer.echo(
            "Error: specify at least one of --boundaries, --census, or --gnaf.",
            err=True,
        )
        raise typer.Exit(code=2)

    cfg = load_config(config)
    effective_data_dir = data_dir if data_dir is not None else default_data_dir()
    boundary_year = str(cfg.census.year)
    if boundaries:
        bds = BoundariesDataSource(
            census=cfg.census,
            base_url=cfg.data_sources.boundaries_base_url,
            root=effective_data_dir / "boundaries" / boundary_year,
        )
        path = bds.fetch(refresh=refresh)
        typer.echo(f"Boundaries: {path}")
    if census:
        dds = DataPacksDataSource(
            census=cfg.census,
            base_url=cfg.data_sources.datapacks_base_url,
            root=effective_data_dir / "census" / boundary_year,
        )
        path = dds.fetch(refresh=refresh)
        typer.echo(f"DataPacks:  {path}")
    if gnaf:
        # G-NAF attribution is required by Geoscape's Open EULA (spec §19.5).
        # Print it on every fetch so first-time CLI users see it.
        typer.echo(_GNAF_ATTRIBUTION)
        gnaf_ds = GnafDataSource(
            release=cfg.geocoding.gnaf.release,
            datum=cfg.geocoding.gnaf.datum,
            mode=cfg.geocoding.gnaf.mode,
            data_dir=effective_data_dir,
            s3_base_url=cfg.data_sources.gnaf_s3_base_url,
            s3_https_endpoint=cfg.data_sources.gnaf_s3_https_endpoint,
            parquet_filter=cfg.data_sources.gnaf_parquet_filter,
            census_year=cfg.census.year,
            official_base_url=cfg.data_sources.gnaf_official_base_url,
        )
        if cfg.geocoding.gnaf.mode == "remote":
            typer.echo(
                "G-NAF mode='remote' streams parquet directly from S3 — "
                "nothing to fetch. Run `census-augment gnaf-info` to confirm "
                "remote-mode connectivity, or switch to mode='cache' for a "
                "local download.",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            gnaf_path = gnaf_ds.fetch(refresh=refresh)
        except (RuntimeError, NotImplementedError) as e:
            typer.echo(f"G-NAF:      not available — {e}", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"G-NAF:      {gnaf_path}")
        # MB correspondence: download alongside, since the §7.3 fast path
        # depends on it. The .dbf is read lazily, so we just ensure the
        # shapefile is on disk.
        mb_ds = MbCorrespondenceDataSource(
            year=cfg.census.year,
            datum=cfg.geocoding.gnaf.datum,
            base_url=cfg.data_sources.boundaries_base_url,
            root=effective_data_dir / "mb" / boundary_year,
        )
        mb_path = mb_ds.fetch(refresh=refresh)
        typer.echo(f"MB lookup:  {mb_path}")


@app.command(name="gnaf-info")
def gnaf_info(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """Show the resolved G-NAF release, mode, on-disk path, and size."""
    cfg = load_config(config)
    if "gnaf" not in cfg.geocoding.providers:
        typer.echo(
            "G-NAF is not in geocoding.providers; nothing to report.\n"
            "Set providers: [gnaf, nominatim] in your config to enable G-NAF.",
            err=True,
        )
        raise typer.Exit(code=1)

    effective_data_dir = data_dir if data_dir is not None else default_data_dir()
    gnaf_ds = GnafDataSource(
        release=cfg.geocoding.gnaf.release,
        datum=cfg.geocoding.gnaf.datum,
        mode=cfg.geocoding.gnaf.mode,
        data_dir=effective_data_dir,
        s3_base_url=cfg.data_sources.gnaf_s3_base_url,
        s3_https_endpoint=cfg.data_sources.gnaf_s3_https_endpoint,
        parquet_filter=cfg.data_sources.gnaf_parquet_filter,
        census_year=cfg.census.year,
        official_base_url=cfg.data_sources.gnaf_official_base_url,
    )
    typer.echo(f"Mode:           {gnaf_ds.mode}")
    typer.echo(f"Datum:          {gnaf_ds.datum}")
    typer.echo(f"Configured release: {cfg.geocoding.gnaf.release}")

    if gnaf_ds.mode == "remote":
        # Remote mode: don't talk about local cache; report what we'd
        # actually query when an open_connection() lands.
        try:
            resolved = gnaf_ds.resolved_release
        except RuntimeError as e:
            typer.echo(f"Resolved release: <unresolved> ({e})", err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"Resolved release: {resolved} (S3)")
        endpoint = cfg.data_sources.gnaf_s3_https_endpoint or "https://{bucket}.s3.amazonaws.com"
        typer.echo(f"Endpoint:       {endpoint}")
        typer.echo(f"S3 base:        {cfg.data_sources.gnaf_s3_base_url}")
        typer.echo(
            "(Streaming mode — no local cache. Each query pulls bytes via "
            "DuckDB's httpfs extension.)"
        )
        return

    if gnaf_ds.is_cached():
        try:
            resolved = gnaf_ds.resolved_release
        except RuntimeError as e:  # pragma: no cover — guarded by is_cached
            typer.echo(f"Resolved release: <unresolved> ({e})")
            return
        typer.echo(f"Resolved release: {resolved}")
        rel_dir = gnaf_ds.release_dir
        typer.echo(f"Path:           {rel_dir}")
        size_bytes = sum(f.stat().st_size for f in rel_dir.glob("*.parquet"))
        typer.echo(
            f"Cached size:    {size_bytes / (1024 * 1024):.1f} MB "
            f"({len(list(rel_dir.glob('*.parquet')))} parquet file(s))"
        )
    else:
        typer.echo("Resolved release: <not cached>")
        typer.echo(f"Path:           {gnaf_ds.gnaf_root}/{{YYYYMM}}/ (none yet)")
        typer.echo(
            "Hint: run `census-augment fetch --gnaf` (or populate "
            f"{gnaf_ds.gnaf_root}/{{YYYYMM}}/ manually) to enable G-NAF."
        )


@app.command()
def validate(
    config: Path = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True),
    full: bool = typer.Option(
        False,
        "--full",
        help=(
            "Also validate variable references against DataPack metadata "
            "(downloads the DataPack if not cached)."
        ),
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
) -> None:
    """Validate config; structurally always, semantically with ``--full``."""
    try:
        cfg = load_config(config)
    except (ValidationError, ValueError) as e:
        typer.echo(f"Config validation failed:\n{e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo("Config structurally valid.")

    if not full:
        return

    effective_data_dir = data_dir if data_dir is not None else default_data_dir()
    datapacks = DataPacksDataSource(
        census=cfg.census,
        base_url=cfg.data_sources.datapacks_base_url,
        root=effective_data_dir / "census" / str(cfg.census.year),
    )
    catalog = VariableCatalog.from_data_source(datapacks)
    try:
        catalog.validate_variables(cfg.variables)
    except CatalogError as e:
        typer.echo(f"Variable validation failed:\n{e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo("Variable references valid against DataPack metadata.")


if __name__ == "__main__":
    app()
