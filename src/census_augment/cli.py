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

import typer
from pydantic import ValidationError

from .catalog import CatalogError, VariableCatalog
from .config import load_config
from .data_sources.boundaries import BoundariesDataSource
from .data_sources.datapacks import DataPacksDataSource
from .data_sources.gnaf import GnafDataSource
from .mb_correspondence import MbCorrespondenceDataSource
from .paths import default_data_dir
from .pipeline import Pipeline

app = typer.Typer(
    help="Augment Australian location datasets with ABS Census data at SA2 level.",
    no_args_is_help=True,
)

_log = logging.getLogger(__name__)


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Verbose logging (DEBUG level)."
    ),
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
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, readable=True
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    cache_dir: Path | None = typer.Option(
        None, "--cache-dir", help=_CACHE_DIR_HELP
    ),
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
            f"Error: the run command requires {' and '.join(missing)} "
            f"to be set in {config}.",
            err=True,
        )
        raise typer.Exit(code=1)

    pipeline = Pipeline.from_config(cfg, data_dir=data_dir, cache_dir=cache_dir)
    summary = pipeline.run()
    typer.echo(summary.format_human_readable())


@app.command()
def discover(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, readable=True
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    search: str | None = typer.Option(
        None, "--search", help="Substring to search in column codes / descriptions."
    ),
    table: str | None = typer.Option(
        None, "--table", help="Table ID (e.g. G02) to list all columns of."
    ),
) -> None:
    """Search census variables or list all columns in a table."""
    if search is None and table is None:
        typer.echo("Error: provide either --search or --table.", err=True)
        raise typer.Exit(code=2)
    if search is not None and table is not None:
        typer.echo("Error: --search and --table are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    cfg = load_config(config)
    effective_data_dir = data_dir if data_dir is not None else default_data_dir()
    datapacks = DataPacksDataSource(
        census=cfg.census,
        base_url=cfg.data_sources.datapacks_base_url,
        root=effective_data_dir / "census",
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
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, readable=True
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", help=_DATA_DIR_HELP),
    boundaries: bool = typer.Option(
        False, "--boundaries", help="Pre-fetch the SA2 boundary shapefile."
    ),
    census: bool = typer.Option(
        False, "--census", help="Pre-fetch the Census DataPack."
    ),
    gnaf: bool = typer.Option(
        False,
        "--gnaf",
        help=(
            "Pre-fetch the G-NAF Core dataset. Currently only validates "
            "the cache layout (S3 download lands in a follow-up — see "
            "spec §19.2). Also fetches the Mesh Block correspondence "
            "shapefile alongside (used for the §7.3 fast path)."
        ),
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Force re-download even if cached."
    ),
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
    if boundaries:
        bds = BoundariesDataSource(
            census=cfg.census,
            base_url=cfg.data_sources.boundaries_base_url,
            root=effective_data_dir / "boundaries",
        )
        path = bds.fetch(refresh=refresh)
        typer.echo(f"Boundaries: {path}")
    if census:
        dds = DataPacksDataSource(
            census=cfg.census,
            base_url=cfg.data_sources.datapacks_base_url,
            root=effective_data_dir / "census",
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
            official_base_url=cfg.data_sources.gnaf_official_base_url,
        )
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
            root=effective_data_dir / "mb",
        )
        mb_path = mb_ds.fetch(refresh=refresh)
        typer.echo(f"MB lookup:  {mb_path}")


@app.command(name="gnaf-info")
def gnaf_info(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, readable=True
    ),
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
        official_base_url=cfg.data_sources.gnaf_official_base_url,
    )
    typer.echo(f"Mode:           {gnaf_ds.mode}")
    typer.echo(f"Datum:          {gnaf_ds.datum}")
    typer.echo(f"Configured release: {cfg.geocoding.gnaf.release}")
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
        typer.echo(
            f"Path:           {gnaf_ds.gnaf_root}/{{YYYYMM}}/ (none yet)"
        )
        typer.echo(
            "Hint: run `census-augment fetch --gnaf` (or populate "
            f"{gnaf_ds.gnaf_root}/{{YYYYMM}}/ manually) to enable G-NAF."
        )


@app.command()
def validate(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, readable=True
    ),
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
        root=effective_data_dir / "census",
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
