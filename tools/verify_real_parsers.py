"""Run our parsers against the real ABS data downloaded by ``fetch_real_data.py``.

Prints a tick/cross summary; exits non-zero if any check fails.

Not part of the pytest suite (see ``tools/README.md`` for rationale).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from census_augment.config import (
    DEFAULT_BOUNDARIES_URL,
    DEFAULT_DATAPACKS_URL,
    CensusConfig,
)
from census_augment.data_sources.boundaries import BoundariesDataSource
from census_augment.data_sources.datapacks import DataPacksDataSource
from census_augment.paths import default_data_dir


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _check(label: str, fn: Callable[[], None]) -> bool:
    try:
        fn()
    except Exception as e:
        print(f"  [FAIL] {label}: {type(e).__name__}: {e}")
        return False
    print(f"  [ OK ] {label}")
    return True


def main() -> int:
    data_dir = default_data_dir()
    if not data_dir.exists():
        print(
            f"{data_dir} does not exist. "
            "Run `python tools/fetch_real_data.py` first."
        )
        return 1

    census = CensusConfig()
    failures: list[str] = []

    # ------ Boundaries ------
    print("=== Boundaries ===")
    boundaries = BoundariesDataSource(
        census=census,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "boundaries",
    )
    if not boundaries.is_cached():
        print("  [FAIL] No cached boundary. Run fetch_real_data.py first.")
        return 1

    def _load_boundary() -> None:
        gdf = boundaries.load()
        assert len(gdf) > 1000, f"only {len(gdf)} rows (expected ~2473)"
        assert "SA2_CODE21" in gdf.columns, (
            f"missing SA2_CODE21; got: {list(gdf.columns)[:5]}"
        )
        assert "SA2_NAME21" in gdf.columns, "missing SA2_NAME21"
        assert gdf.crs is not None, "CRS is None"
        crs_epsg = gdf.crs.to_epsg()
        crs_name = (gdf.crs.name or "").upper()
        assert crs_epsg == 7844 or "GDA2020" in crs_name, (
            f"unexpected CRS: epsg={crs_epsg}, name={gdf.crs.name!r}"
        )
        print(
            f"         -> {len(gdf)} polygons, "
            f"columns include {sorted(gdf.columns.tolist())[:5]}..."
        )

    if not _check("Load + schema + CRS", _load_boundary):
        failures.append("boundaries")

    # ------ DataPacks ------
    print("=== DataPacks ===")
    datapacks = DataPacksDataSource(
        census=census,
        base_url=DEFAULT_DATAPACKS_URL,
        root=data_dir / "census",
    )
    if not datapacks.is_cached():
        print("  [FAIL] No cached DataPack. Run fetch_real_data.py first.")
        return 1

    metadata = None

    def _list_tables() -> None:
        tables = datapacks.list_tables()
        assert len(tables) >= 50, f"only {len(tables)} tables (expected ~59)"
        print(f"         -> {len(tables)} tables: {tables[:5]}...")

    def _parse_metadata() -> None:
        nonlocal metadata
        metadata = datapacks.load_metadata()
        cols = list(metadata.all_columns())
        assert len(cols) >= 1000, f"only {len(cols)} columns parsed"
        print(f"         ->{len(cols)} columns across {len(metadata.tables)} tables")

    def _spot_check_metadata() -> None:
        assert metadata is not None
        assert metadata.has_column("G02", "Median_tot_hhd_inc_weekly")
        desc = metadata.describe("G02", "Median_tot_hhd_inc_weekly")
        assert desc and "income" in desc.lower(), f"unexpected description: {desc!r}"
        print(f"         ->G02.Median_tot_hhd_inc_weekly: {desc!r}")
        g02_name = metadata.tables.get("G02")
        assert g02_name is not None and g02_name.name, "G02 missing table name"
        print(f"         ->G02 table name: {g02_name.name!r}")

    def _load_g01() -> None:
        df = datapacks.load_table("G01")
        assert len(df) > 100, f"only {len(df)} rows in G01"
        assert "Tot_P_P" in df.columns, f"missing Tot_P_P; got {list(df.columns)[:5]}"
        print(f"         ->G01: {len(df)} rows, {len(df.columns)} columns")

    if not _check("List tables (>= 50)", _list_tables):
        failures.append("datapacks.list_tables")
    if not _check("Parse metadata (>= 1000 columns)", _parse_metadata):
        failures.append("datapacks.load_metadata")
    if metadata is not None and not _check(
        "Metadata spot-check (G02 known column + table name)", _spot_check_metadata
    ):
        failures.append("datapacks.metadata.spot_check")
    if not _check("Load G01 table", _load_g01):
        failures.append("datapacks.load_table")

    # ------ Nominatim ------
    print("=== Nominatim ===")
    sample_path = data_dir / "nominatim_sample.json"
    if not sample_path.exists():
        print("  (skipped; no sample. Run fetch_real_data.py without --skip-nominatim.)")
    else:
        def _check_nominatim() -> None:
            data = json.loads(sample_path.read_text(encoding="utf-8"))
            results = data["results"]
            assert isinstance(results, list)
            assert len(results) >= 1
            entry = results[0]
            assert isinstance(entry["lat"], str), f"lat not str: {type(entry['lat'])}"
            assert isinstance(entry["lon"], str), f"lon not str: {type(entry['lon'])}"
            float(entry["lat"])  # parses
            float(entry["lon"])
            print(
                f"         ->({entry['lat']}, {entry['lon']}) "
                f"for {data['address']!r}"
            )

        if not _check("Sample response shape", _check_nominatim):
            failures.append("nominatim")

    print()
    if not failures:
        print("All checks passed.")
        return 0
    print(f"{len(failures)} failure(s): {failures}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
