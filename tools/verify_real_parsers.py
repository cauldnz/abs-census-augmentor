"""Run our parsers against the real ABS data downloaded by ``fetch_real_data.py``.

Prints a tick/cross summary; exits non-zero if any check fails.

Not part of the pytest suite (see ``tools/README.md`` for rationale).

Run with::

    uv run python tools/verify_real_parsers.py

Plain ``python tools/verify_real_parsers.py`` invokes whatever Python
is on ``PATH`` — usually the system Python without ``census-augment``
installed, producing a ``ModuleNotFoundError``. ``uv run`` picks up
the project's ``.venv`` automatically.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

try:
    from census_augment.config import (
        DEFAULT_BOUNDARIES_URL,
        DEFAULT_DATAPACKS_URL,
        CensusConfig,
    )
    from census_augment.data_sources.boundaries import BoundariesDataSource
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.data_sources.gnaf import GnafDataSource
    from census_augment.geocoding.gnaf import GnafGeocoder
    from census_augment.data_sources.mb_correspondence import MbCorrespondenceDataSource
    from census_augment.paths import default_data_dir
except ModuleNotFoundError as e:
    sys.stderr.write(
        "ERROR: census_augment is not importable from the active Python "
        f"({sys.executable}).\n\n"
        f"Underlying error: {e}\n\n"
        "Most likely cause: you ran `python tools/...` instead of\n"
        "`uv run python tools/...`. Plain `python` uses your system\n"
        "PATH, not the project's .venv where the package lives.\n\n"
        "Fix:\n"
        "    uv run python tools/verify_real_parsers.py\n\n"
        "Or activate the venv first:\n"
        "    Windows : .venv\\Scripts\\activate\n"
        "    macOS/Linux: source .venv/bin/activate\n"
        "    Then    : python tools/verify_real_parsers.py\n"
    )
    sys.exit(2)


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
        print(f"{data_dir} does not exist. Run `python tools/fetch_real_data.py` first.")
        return 1

    census = CensusConfig()
    failures: list[str] = []

    # ------ Boundaries ------
    print("=== Boundaries ===")
    boundaries = BoundariesDataSource(
        census=census,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "boundaries" / str(census.year),
    )
    if not boundaries.is_cached():
        print("  [FAIL] No cached boundary. Run fetch_real_data.py first.")
        return 1

    def _load_boundary() -> None:
        gdf = boundaries.load()
        assert len(gdf) > 1000, f"only {len(gdf)} rows (expected ~2473)"
        assert "SA2_CODE21" in gdf.columns, f"missing SA2_CODE21; got: {list(gdf.columns)[:5]}"
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

    # ------ Edition 2 (2016) boundaries (Phase F.1) ------
    #
    # Cross-checks the BoundaryEditionSpec-driven URL / filename /
    # column-name claims against the live ABS Edition-2 SA2 download.
    # Skipped if the 2016 boundary hasn't been fetched yet — keep the
    # probe self-skipping so a partial cache doesn't bork the whole run.
    e2_root = data_dir / "boundaries" / "2016"
    if e2_root.exists():
        print("=== Boundaries (ASGS Edition 2, 2016) ===")
        e2_census = CensusConfig(year=2016, asgs_edition=2, datum="GDA94")
        e2_boundaries = BoundariesDataSource(
            census=e2_census,
            base_url=DEFAULT_BOUNDARIES_URL,  # Edition 2 ignores this
            root=e2_root,
        )
        if not e2_boundaries.is_cached():
            print(
                "  (skipped; no cached Edition 2 boundary. "
                "Run `uv run python tools/fetch_real_data.py --edition 2` to populate it.)"
            )
        else:

            def _load_edition_2_boundary() -> None:
                gdf = e2_boundaries.load()
                # 2016 had ~2,310 SA2s — fewer than 2021's ~2,473.
                assert len(gdf) > 1000, f"only {len(gdf)} rows (expected ~2,310)"
                spec = e2_boundaries.edition
                assert spec.sa2_code_column in gdf.columns, (
                    f"missing {spec.sa2_code_column!r}; got: {list(gdf.columns)[:5]}"
                )
                assert spec.sa2_name_column in gdf.columns, (
                    f"missing {spec.sa2_name_column!r}; got: {list(gdf.columns)[:5]}"
                )
                # Edition 2 should be GDA94 (EPSG:4283).
                assert gdf.crs is not None, "CRS is None"
                crs_epsg = gdf.crs.to_epsg()
                crs_name = (gdf.crs.name or "").upper()
                assert crs_epsg == 4283 or "GDA94" in crs_name, (
                    f"unexpected CRS for Edition 2: epsg={crs_epsg}, name={gdf.crs.name!r}"
                )
                print(
                    f"         -> {len(gdf)} polygons, "
                    f"columns include {sorted(gdf.columns.tolist())[:5]}..."
                )

            if not _check(
                "Edition 2 load + schema (SA2_MAIN16/SA2_NAME16) + CRS (GDA94)",
                _load_edition_2_boundary,
            ):
                failures.append("boundaries_edition_2")

    # ------ DataPacks ------
    print("=== DataPacks ===")
    datapacks = DataPacksDataSource(
        census=census,
        base_url=DEFAULT_DATAPACKS_URL,
        root=data_dir / "census" / str(census.year),
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

    # ------ Mesh Block correspondence ------
    print("=== Mesh Block correspondence ===")
    mb_ds = MbCorrespondenceDataSource(
        year=census.year,
        datum=census.datum,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "mb" / str(census.year),
    )
    if not mb_ds.is_cached():
        print("  (skipped; no MB shapefile cached. Run fetch_real_data.py to populate it.)")
    else:

        def _load_mb_correspondence() -> None:
            lookup = mb_ds.load_correspondence()
            # Australia has ~360k mesh blocks across all states.
            assert len(lookup) > 100_000, f"only {len(lookup)} mesh blocks (expected ~360k)"
            # Spot-check: pick a known Sydney CBD mesh block.
            sample_mb = next(iter(lookup))
            info = lookup[sample_mb]
            assert info.sa2_code, f"empty sa2_code for {sample_mb}"
            assert info.sa2_name, f"empty sa2_name for {sample_mb}"
            print(
                f"         -> {len(lookup):,} mesh blocks; "
                f"sample: {sample_mb} -> {info.sa2_code} ({info.sa2_name})"
            )

        if not _check("Load MB->SA2 lookup (>= 100k entries)", _load_mb_correspondence):
            failures.append("mb_correspondence")

    # ------ G-NAF ------
    print("=== G-NAF ===")
    gnaf_ds = GnafDataSource(
        release="latest",
        datum=census.datum,
        mode="cache",
        data_dir=data_dir,
    )
    if not gnaf_ds.is_cached():
        print(
            "  (skipped; no G-NAF cache populated. "
            "Run `python tools/fetch_real_data.py` to download from S3.)"
        )
    else:

        def _open_gnaf() -> None:
            con = gnaf_ds.open_connection()
            row = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
            assert row is not None, "COUNT(*) returned no row?"
            (count,) = row
            assert count > 10_000_000, f"only {count:,} addresses (expected ~15.86M)"
            print(f"         -> {count:,} addresses; release {gnaf_ds.resolved_release}")

        def _gnaf_tier1_hit() -> None:
            geocoder = GnafGeocoder(data_source=gnaf_ds, fuzzy_threshold=0.85)
            # A real Sydney address that should round-trip cleanly.
            result = geocoder.geocode("1 Macquarie Street Sydney NSW 2000")
            assert result.is_success, (
                f"Tier 1 missed for a verbatim address; got source={result.source}"
            )
            assert result.mb_code, "G-NAF row had no MB_CODE"
            print(
                f"         -> {result.source} hit at "
                f"({result.lat}, {result.lon}); mb_code={result.mb_code}"
            )

        if not _check("Open DuckDB connection (>= 10M rows)", _open_gnaf):
            failures.append("gnaf.open_connection")
        if not _check("Tier 1 exact match", _gnaf_tier1_hit):
            failures.append("gnaf.tier1")

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
            print(f"         ->({entry['lat']}, {entry['lon']}) for {data['address']!r}")

        if not _check("Sample response shape", _check_nominatim):
            failures.append("nominatim")

    # ------ v1.3 datasets (SEIFA, ERP, DSS, ABS PIA) ------
    print("=== v1.3 registered datasets ===")
    from census_augment.datasets._abs_pia import AbsPiaDataSource
    from census_augment.datasets._dss import DssDataSource
    from census_augment.datasets._erp import ErpDataSource
    from census_augment.datasets._seifa import SeifaDataSource

    def _check_seifa() -> None:
        ds = SeifaDataSource(root=data_dir / "seifa_2021")
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s (expected ~2,366)"
        assert "irsd_score" in df.columns
        assert "ieo_aus_decile" in df.columns
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}, "
            f"{len(df.columns)} columns"
        )

    def _check_erp() -> None:
        ds = ErpDataSource(root=data_dir / "erp_by_sa2")
        df = ds.load()
        assert len(df) >= 2000
        assert "population_total" in df.columns
        assert "reference_year" in df.columns
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}, "
            f"reference year {df['reference_year'].iloc[0]}"
        )

    def _check_dss() -> None:
        ds = DssDataSource(root=data_dir / "dss_payments")
        df = ds.load()
        assert len(df) >= 2000
        assert "release_quarter" in df.columns
        recipient_cols = [c for c in df.columns if c.endswith("_recipients")]
        assert len(recipient_cols) >= 10, f"only {len(recipient_cols)} payment columns"
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}, "
            f"{len(recipient_cols)} payment-type columns"
        )

    def _check_abs_pia() -> None:
        ds = AbsPiaDataSource(root=data_dir / "abs_personal_income")
        df = ds.load()
        assert len(df) >= 2000
        assert "median_total_income" in df.columns
        assert "income_earners_count" in df.columns
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {df.index[0]}: median "
            f"${df['median_total_income'].iloc[0]:.0f}"
        )

    if not _check("SEIFA 2021 (~2,366 SA2s, 4 indexes)", _check_seifa):
        failures.append("seifa_2021")
    if not _check("ERP by SA2 (~2,454 SA2s, 25-year history)", _check_erp):
        failures.append("erp_by_sa2")
    if not _check("DSS payments (~2,454 SA2s, 22 payment types)", _check_dss):
        failures.append("dss_payments")
    if not _check("ABS Personal Income (~2,450 SA2s)", _check_abs_pia):
        failures.append("abs_personal_income")

    # ------ PRESET source resolution against real GCP DataPack ------
    # Acid test for the "Real Data First" rule (see CLAUDE.md): every
    # PRESET feature's `source_fields()` must resolve cleanly against
    # the live GCP catalog. This is the gate that should have caught
    # #23 — its absence is what let v1.3 ship six PRESETs that all
    # referenced columns the real DataPack didn't have.
    print("=== PRESET source-column resolution ===")
    from census_augment.catalog import VariableCatalog
    from census_augment.features import features

    if metadata is None:
        print("  (skipped; DataPack metadata didn't load above.)")
    else:
        catalog = VariableCatalog(metadata)
        preset_specs = features.list_features()
        if not preset_specs:
            print("  (skipped; no PRESETs registered.)")
        else:

            def _make_preset_check(
                spec: object,
            ) -> Callable[[], None]:
                def _check_preset() -> None:
                    # `spec` is a FeatureSpec; collect every source ref
                    # it'd ask the catalog for and resolve each one.
                    # If the GCP catalog can't find it, the spec is
                    # broken — same surface that #23 originally hit.
                    refs = spec.source_fields()  # type: ignore[attr-defined]
                    unresolved: list[tuple[str, str]] = []
                    for ref in sorted(refs):
                        try:
                            catalog.resolve(ref)
                        except Exception as e:  # noqa: BLE001
                            unresolved.append((ref, str(e).splitlines()[0]))
                    assert not unresolved, (
                        f"PRESET {spec.id!r} refs columns not in "  # type: ignore[attr-defined]
                        f"the real GCP DataPack: {unresolved}"
                    )
                    print(
                        f"         -> {spec.id}: {len(refs)} source refs "  # type: ignore[attr-defined]
                        "all resolve."
                    )

                return _check_preset

            for spec in preset_specs:
                label = f"PRESET {spec.id} source refs resolve"
                if not _check(label, _make_preset_check(spec)):
                    failures.append(f"preset.{spec.id}")

    print()
    if not failures:
        print("All checks passed.")
        return 0
    print(f"{len(failures)} failure(s): {failures}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
