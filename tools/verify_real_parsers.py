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
    from census_augment.data_sources.lga_boundaries import (
        KNOWN_LGA_YEARS,
        LgaBoundariesDataSource,
    )
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

    # ------ Edition 1 (2011) boundaries (Phase F.6) ------
    #
    # Same probe pattern as Edition 2: cross-check the
    # BoundaryEditionSpec-driven URL / filename / column-name claims
    # against the live ABS Edition-1 SA2 download.
    e1_root = data_dir / "boundaries" / "2011"
    if e1_root.exists():
        print("=== Boundaries (ASGS Edition 1, 2011) ===")
        # CensusConfig.year is Literal[2016, 2021]; we satisfy it with
        # 2016 + GDA94 (valid combo) and override the edition spec
        # explicitly. The probe is exercising the boundary file, not
        # the cross-sectional config layer.
        from census_augment.data_sources._edition import edition_1_spec  # noqa: PLC0415

        e1_census = CensusConfig(year=2016, asgs_edition=2, datum="GDA94")
        e1_boundaries = BoundariesDataSource(
            census=e1_census,
            base_url=DEFAULT_BOUNDARIES_URL,  # Edition 1 ignores this
            root=e1_root,
            edition_spec=edition_1_spec(),
        )
        if not e1_boundaries.is_cached():
            print(
                "  (skipped; no cached Edition 1 boundary. "
                "Run `uv run python tools/fetch_real_data.py --edition 1` "
                "to populate it once that flag is wired.)"
            )
        else:

            def _load_edition_1_boundary() -> None:
                gdf = e1_boundaries.load()
                # 2011 had ~2,214 SA2s — fewer than 2016's ~2,310 and
                # 2021's ~2,473.
                assert len(gdf) > 1000, f"only {len(gdf)} rows (expected ~2,214)"
                spec = e1_boundaries.edition
                assert spec.sa2_code_column in gdf.columns, (
                    f"missing {spec.sa2_code_column!r}; got: {list(gdf.columns)[:5]}"
                )
                assert spec.sa2_name_column in gdf.columns, (
                    f"missing {spec.sa2_name_column!r}; got: {list(gdf.columns)[:5]}"
                )
                # Edition 1 should be GDA94 (EPSG:4283), same as Ed 2.
                assert gdf.crs is not None, "CRS is None"
                crs_epsg = gdf.crs.to_epsg()
                crs_name = (gdf.crs.name or "").upper()
                assert crs_epsg == 4283 or "GDA94" in crs_name, (
                    f"unexpected CRS for Edition 1: epsg={crs_epsg}, name={gdf.crs.name!r}"
                )
                print(
                    f"         -> {len(gdf)} polygons, "
                    f"columns include {sorted(gdf.columns.tolist())[:5]}..."
                )

            if not _check(
                "Edition 1 load + schema (SA2_MAIN11/SA2_NAME11) + CRS (GDA94)",
                _load_edition_1_boundary,
            ):
                failures.append("boundaries_edition_1")

    # ------ LGA boundary (v2.2.0 / PR #107) ------
    #
    # Cross-checks LgaBoundariesDataSource's URL + filename + DBF
    # column-name claims against the live ABS LGA download. LGA
    # boundaries update annually; the smoke targets the latest known
    # year. Skipped if the LGA cache hasn't been populated yet — same
    # self-skipping pattern as the Edition 1 / 2 sections above. Run
    # ``uv run python tools/fetch_real_data.py`` (with `--skip-lga`
    # absent) to populate.
    latest_lga_year = max(KNOWN_LGA_YEARS)
    lga_root = data_dir / "boundaries" / "lga" / str(latest_lga_year)
    if lga_root.exists():
        print(f"=== LGA boundary ({latest_lga_year}) ===")
        lga_boundaries = LgaBoundariesDataSource(
            year=latest_lga_year,
            root=lga_root,
        )
        if not lga_boundaries.is_cached():
            print(
                f"  (skipped; no cached LGA {latest_lga_year} boundary. "
                "Run `uv run python tools/fetch_real_data.py` (without "
                "--skip-lga) to populate it.)"
            )
        else:

            def _load_lga_boundary() -> None:
                gdf = lga_boundaries.load()
                # ABS publishes ~537-567 LGAs depending on the year;
                # 2025 has 567 per the 2026-06-01 live probe.
                assert len(gdf) > 500, f"only {len(gdf)} LGAs (expected ~530-570)"
                code_col = lga_boundaries.code_column
                name_col = lga_boundaries.name_column
                assert code_col in gdf.columns, (
                    f"missing {code_col!r}; got: {list(gdf.columns)[:5]}"
                )
                assert name_col in gdf.columns, (
                    f"missing {name_col!r}; got: {list(gdf.columns)[:5]}"
                )
                # LGA boundary is GDA2020 (EPSG:7844) — same as the SA2
                # Edition 3 boundary. Round-trip through .shp doesn't
                # always preserve EPSG; check the datum name as backup.
                assert gdf.crs is not None, "CRS is None"
                crs_epsg = gdf.crs.to_epsg()
                crs_name = (gdf.crs.name or "").upper()
                assert crs_epsg == 7844 or "GDA2020" in crs_name, (
                    f"unexpected CRS for LGA boundary: epsg={crs_epsg}, name={gdf.crs.name!r}"
                )
                # Spot-check non-null geometry on a sample LGA. ABS LGA
                # files have very few pseudo-rows (unlike SA2 which has
                # off-shore / migratory rows), but it's still worth
                # confirming at least one row has real geometry.
                non_null = gdf[gdf.geometry.notna()]
                assert len(non_null) > 500, (
                    f"only {len(non_null)} LGAs have geometry; expected ~530-570"
                )
                print(
                    f"         -> {len(gdf)} LGAs ({len(non_null)} with "
                    f"geometry), sample {non_null[code_col].iloc[0]} "
                    f"({non_null[name_col].iloc[0]})"
                )

            if not _check(
                f"LGA {latest_lga_year} load + schema "
                f"(LGA_CODE{str(latest_lga_year)[-2:]} / "
                f"LGA_NAME{str(latest_lga_year)[-2:]}) + CRS (GDA2020)",
                _load_lga_boundary,
            ):
                failures.append("boundaries_lga")

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

    # ------ DataPacks 2016 (F.4) -------------------------------------
    # The 2016 GCP DataPack lives at the same URL pattern as 2021
    # (just the year prefix changes). Only short-header is hosted.
    # Treat the check as optional: if the 2016 ZIP isn't in the cache
    # (the default ``fetch_real_data.py`` invocation only fetches the
    # configured year), skip silently. When ``fetch_real_data.py
    # --census-year 2016`` is run, this becomes a live drift probe.
    print("=== DataPacks (2016, F.4) ===")
    census_2016 = CensusConfig(year=2016, asgs_edition=2, datum="GDA94", descriptor="short-header")
    datapacks_2016 = DataPacksDataSource(
        census=census_2016,
        base_url=DEFAULT_DATAPACKS_URL,
        root=data_dir / "census" / "2016",
    )
    if not datapacks_2016.is_cached():
        print(
            "  (skipped; no 2016 DataPack cached. "
            "Run `uv run python tools/fetch_real_data.py --census-year 2016` "
            "to populate it.)"
        )
    else:

        def _list_tables_2016() -> None:
            tables = datapacks_2016.list_tables()
            # 2016 GCP has G01..G59 (no G60-G62 which 2021 added).
            assert len(tables) >= 50, f"only {len(tables)} tables (expected ~59)"
            assert "G62" not in tables, "G62 should not exist in 2016 GCP"
            print(f"         -> {len(tables)} tables (G62 correctly absent)")

        def _parse_metadata_2016() -> None:
            md = datapacks_2016.load_metadata()
            cols = list(md.all_columns())
            assert len(cols) >= 1000, f"only {len(cols)} columns in 2016 metadata"
            # 2016 metadata XLSX has sentence-case sheet names — confirms
            # the candidate-list extension introduced in F.4.
            assert md.has_table("G02"), "G02 missing from 2016 metadata"
            assert md.has_column("G02", "Median_tot_hhd_inc_weekly"), (
                "G02.Median_tot_hhd_inc_weekly missing from 2016 metadata"
            )
            assert not md.has_table("G62"), "G62 should be absent from 2016"
            print(
                f"         -> {len(cols)} columns across {len(md.tables)} tables; "
                f"G62 correctly absent"
            )

        def _load_g02_2016() -> None:
            df = datapacks_2016.load_table("G02")
            # 2016 had ~2,310 SA2s (fewer than 2021's ~2,473).
            assert len(df) >= 1000, f"only {len(df)} rows in G02 2016"
            # SA2 code column for 2016 is SA2_MAINCODE_2016 — the F.4
            # candidate-list extension.
            assert df.index.name == "SA2_MAINCODE_2016", (
                f"unexpected SA2 column for 2016: {df.index.name!r}"
            )
            assert "Median_tot_hhd_inc_weekly" in df.columns, (
                "Median_tot_hhd_inc_weekly missing from 2016 G02"
            )
            sample = df["Median_tot_hhd_inc_weekly"].dropna().iloc[0]
            print(
                f"         -> G02: {len(df)} rows, index={df.index.name}, "
                f"sample median income ${sample}"
            )

        if not _check("List tables (2016, ~59, no G62)", _list_tables_2016):
            failures.append("datapacks_2016.list_tables")
        if not _check("Parse metadata (2016, sentence-case sheets)", _parse_metadata_2016):
            failures.append("datapacks_2016.load_metadata")
        if not _check("Load G02 table (2016, SA2_MAINCODE_2016)", _load_g02_2016):
            failures.append("datapacks_2016.load_g02")

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
    from census_augment.datasets._abs_ba import AbsBaDataSource
    from census_augment.datasets._abs_cab import AbsBusinessCountsDataSource
    from census_augment.datasets._abs_pia import AbsPiaDataSource
    from census_augment.datasets._aihw_apc import AihwMhAdmittedPatientsDataSource
    from census_augment.datasets._aihw_cmh import AihwMhCommunityDataSource
    from census_augment.datasets._aihw_ed import AihwMhEdPresentationsDataSource
    from census_augment.datasets._aihw_medicare import AihwMhMedicareDataSource
    from census_augment.datasets._aihw_mh import AihwMhPrescriptionsDataSource
    from census_augment.datasets._aihw_social_housing import AihwSocialHousingDataSource
    from census_augment.datasets._dss import DssDataSource
    from census_augment.datasets._erp import ErpDataSource
    from census_augment.datasets._seifa import SeifaDataSource

    def _check_seifa() -> None:
        # 2021 release (XLSX).
        ds = SeifaDataSource(release="2021", root=data_dir / "seifa")
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s in 2021 (expected ~2,366)"
        assert "irsd_score" in df.columns
        assert "ieo_aus_decile" in df.columns
        assert df.index.name == "sa2_code_2021"
        print(
            f"         -> 2021: {len(df):,} SA2s, {len(df.columns)} columns, index={df.index.name}"
        )
        # 2016 release (XLS).
        ds16 = SeifaDataSource(release="2016", root=data_dir / "seifa")
        df16 = ds16.load()
        assert len(df16) >= 2000, f"only {len(df16)} SA2s in 2016 (expected ~2,196)"
        assert "irsd_score" in df16.columns
        assert df16.index.name == "sa2_code_2016"
        print(
            f"         -> 2016: {len(df16):,} SA2s, {len(df16.columns)} columns, "
            f"index={df16.index.name}"
        )
        # 2011 release (XLS — F.6). Same .xls parser as 2016, different
        # SA2 geography (ASGS Edition 1, ~2,100 SA2s with scores).
        ds11 = SeifaDataSource(release="2011", root=data_dir / "seifa")
        df11 = ds11.load()
        assert len(df11) >= 1800, f"only {len(df11)} SA2s in 2011 (expected ~2,100)"
        assert "irsd_score" in df11.columns
        assert df11.index.name == "sa2_code_2011"
        # IRSD scores normalise to mean 1000, sd 100 by ABS convention.
        irsd_min = df11["irsd_score"].dropna().min()
        irsd_max = df11["irsd_score"].dropna().max()
        assert 400 < irsd_min < 700, f"2011 IRSD min looks wrong: {irsd_min} (expected ~554)"
        assert 1100 < irsd_max < 1300, f"2011 IRSD max looks wrong: {irsd_max} (expected ~1196)"
        print(
            f"         -> 2011: {len(df11):,} SA2s, {len(df11.columns)} columns, "
            f"index={df11.index.name}; IRSD {irsd_min:.0f}-{irsd_max:.0f}"
        )

    def _check_erp() -> None:
        ds = ErpDataSource(root=data_dir / "erp_by_sa2")
        df = ds.load()
        assert len(df) >= 2000
        assert "population_total" in df.columns
        assert "reference_year" in df.columns
        msg = (
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}, "
            f"reference year {df['reference_year'].iloc[0]}"
        )
        # Age/sex enrichment columns (wishlist PR): present iff the
        # 3235.0 DS0002 fetch succeeded. The fetcher logs a warning
        # and omits them on failure, so absence is informative rather
        # than fatal.
        age_sex_cols = (
            "population_male",
            "population_female",
            "population_0_14",
            "population_15_64",
            "population_65_plus",
            "median_age",
        )
        present = [c for c in age_sex_cols if c in df.columns]
        if len(present) == len(age_sex_cols):
            # Pick the latest-non-null SA2 to sanity-check the values.
            sample = df[df["median_age"].notna()].iloc[0]
            msg += (
                f"\n         -> age/sex (3235.0 release "
                f"{ds._resolved_age_sex_release}): {sample.name} "
                f"M={int(sample['population_male'])}, "
                f"F={int(sample['population_female'])}, "
                f"median={sample['median_age']:.1f}y, "
                f"65+={int(sample['population_65_plus'])}"
            )
        elif present:
            msg += (
                f"\n         -> age/sex columns PARTIAL: "
                f"{sorted(present)} (expected {len(age_sex_cols)}, got {len(present)})"
            )
        else:
            msg += "\n         -> age/sex columns absent (DS0002 not fetched)"
        print(msg)

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

    def _check_abs_ba() -> None:
        # ABS Building Approvals (catalogue 8731.0). v2.2.0 added this
        # SA2-native dataset; the real-data smoke fetches all 8 per-state
        # cubes for the latest monthly release. Confirm the 9 metric
        # columns + reference FY all populate.
        ds = AbsBaDataSource(root=data_dir / "abs_building_approvals")
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "new_houses_count",
            "new_other_residential_building_count",
            "total_dwellings_count",
            "value_new_houses",
            "value_total_building",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected ABS BA columns: {sorted(missing)}"
        # Spot-check one SA2's values are non-null + non-negative.
        sample = df[df["total_dwellings_count"].notna()].iloc[0]
        assert int(sample["total_dwellings_count"]) >= 0
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: {int(sample['total_dwellings_count'])} "
            f"total dwelling approvals, "
            f"${float(sample['value_total_building']):,.0f}k total building value"
        )

    def _check_abs_cab() -> None:
        # ABS Counts of Australian Businesses (catalogue 8165.0). SA2-native
        # DC8 cube; the smoke parses the latest year's sheet and sums the
        # industry-division rows per SA2. Confirm the 6 size-band columns +
        # reference period populate for ~2,400 SA2s.
        ds = AbsBusinessCountsDataSource(root=data_dir / "abs_business_counts")
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "business_count_non_employing",
            "business_count_1_4_employees",
            "business_count_5_19_employees",
            "business_count_20_199_employees",
            "business_count_200_plus_employees",
            "business_count_total",
            "reference_period",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected ABS CAB columns: {sorted(missing)}"
        non_null = df[df["business_count_total"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have a non-null business total; "
            f"the industry-row summation may be broken"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['business_count_total']):,} businesses "
            f"({int(sample['business_count_non_employing']):,} non-employing)"
        )

    def _check_aihw_mh() -> None:
        # AIHW Mental Health Prescriptions (NMHSPF). SA4-keyed source —
        # downscaled to SA2 via the boundary file's SA4_CODE21 attribute.
        # Requires the SA2 boundary to derive the parent-code mapping
        # (compute_sa2_parent_codes from PR #104).
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        # Pick up the already-fetched SA2 boundary from earlier in this
        # verify run (the Boundaries section above fetched + cached it).
        # If that section failed, this dataset will too — which is the
        # right cascade since there's no point downscaling without
        # parent codes.
        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_parents = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )
        sa2_to_sa4 = sa2_parents["SA4"]

        ds = AihwMhPrescriptionsDataSource(root=data_dir / "aihw_mh_prescriptions")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "mh_patients_count",
            "mh_patient_rate_per_1000",
            "mh_prescriptions_count",
            "mh_prescription_rate_per_1000",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW MH columns: {sorted(missing)}"
        # Spot-check: at least one SA2 has a populated patients_count;
        # if every value is null the downscale mapping is wrong.
        non_null = df[df["mh_patients_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null MH patient counts; "
            f"downscale mapping may be broken"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: {int(sample['mh_patients_count']):,} "
            f"MH patients, rate {sample['mh_patient_rate_per_1000']}/1,000"
        )

    def _check_aihw_apc() -> None:
        # AIHW Mental Health Admitted Patient Care (NMHSPF). SA4-keyed,
        # downscaled to SA2 via SA4_CODE21 — same pattern as MH Rx.
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_to_sa4 = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )["SA4"]

        ds = AihwMhAdmittedPatientsDataSource(root=data_dir / "aihw_mh_admitted_patients")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "mh_hospitalisations_count",
            "mh_patient_days_count",
            "mh_psychiatric_care_days_count",
            "mh_procedures_count",
            "mh_hospitalisations_per_10000",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW APC columns: {sorted(missing)}"
        non_null = df[df["mh_hospitalisations_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null hospitalisation counts; "
            f"downscale mapping may be broken"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['mh_hospitalisations_count']):,} MH hospitalisations, "
            f"{sample['mh_hospitalisations_per_10000']}/10,000"
        )

    def _check_aihw_ed() -> None:
        # AIHW Mental Health ED presentations (NMHSPF). SA4-keyed,
        # downscaled to SA2 via SA4_CODE21 — same pattern as MH Rx/APC.
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_to_sa4 = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )["SA4"]

        ds = AihwMhEdPresentationsDataSource(root=data_dir / "aihw_mh_ed_presentations")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "mh_ed_presentations_count",
            "mh_ed_presentations_per_10000",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW ED columns: {sorted(missing)}"
        non_null = df[df["mh_ed_presentations_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null ED presentation counts; "
            f"downscale mapping may be broken"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['mh_ed_presentations_count']):,} MH ED presentations, "
            f"{sample['mh_ed_presentations_per_10000']}/10,000"
        )

    def _check_aihw_medicare() -> None:
        # AIHW Medicare-subsidised MH services (NMHSPF). SA4-keyed,
        # downscaled to SA2 via SA4_CODE21. Note: hyphenated SA4-101
        # codes + NBSP in ProviderType (handled by the parser).
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_to_sa4 = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )["SA4"]

        ds = AihwMhMedicareDataSource(root=data_dir / "aihw_mh_medicare")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "mh_medicare_patients_count",
            "mh_medicare_patient_rate_per_1000",
            "mh_medicare_services_count",
            "mh_medicare_service_rate_per_1000",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW Medicare columns: {sorted(missing)}"
        non_null = df[df["mh_medicare_patients_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null Medicare patient counts; "
            f"downscale mapping may be broken (check the SA4-hyphen strip)"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['mh_medicare_patients_count']):,} Medicare MH patients, "
            f"{int(sample['mh_medicare_services_count']):,} services"
        )

    def _check_aihw_community() -> None:
        # AIHW Community Mental Health care (NMHSPF). SA4-keyed, downscaled
        # to SA2 via SA4_CODE21. Bare 3-digit SA4 codes; the GeospatialType
        # == SA4 filter is load-bearing (the code column is a name for
        # GCSSA/PHN rows).
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_to_sa4 = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )["SA4"]

        ds = AihwMhCommunityDataSource(root=data_dir / "aihw_mh_community")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "mh_community_patients_count",
            "mh_community_patients_per_10000",
            "mh_community_contacts_count",
            "mh_community_contacts_per_10000",
            "mh_community_treatment_days_per_3mo",
            "mh_community_avg_treatment_length_days",
            "mh_community_population",
            "reference_financial_year",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW Community MH columns: {sorted(missing)}"
        non_null = df[df["mh_community_patients_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null community-MH patient counts; "
            f"downscale mapping may be broken (check the GeospatialType==SA4 filter)"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['mh_community_patients_count']):,} community-MH patients, "
            f"{int(sample['mh_community_contacts_count']):,} contacts"
        )

    def _check_aihw_social_housing() -> None:
        # AIHW Social Housing dwellings (Housing Assistance). SA4-native
        # XLSX (DWELLINGS.4), downscaled to SA2 via SA4_CODE21. Bare 3-digit
        # SA4 codes; ". ." SOMIH sentinel -> null for non-SOMIH states.
        import geopandas as gpd  # noqa: PLC0415

        from census_augment.spatial import (  # noqa: PLC0415
            compute_sa2_parent_codes,
        )

        boundary_path = (
            data_dir
            / "boundaries"
            / "2021"
            / "SA2_2021_AUST_SHP_GDA2020"
            / "SA2_2021_AUST_GDA2020.shp"
        )
        assert boundary_path.exists(), (
            f"SA2 boundary shapefile not at {boundary_path} — "
            f"earlier Boundaries section must have failed."
        )
        boundary = gpd.read_file(boundary_path)
        sa2_to_sa4 = compute_sa2_parent_codes(
            boundary,
            sa2_code_column="SA2_CODE21",
            parent_code_columns={"SA4": "SA4_CODE21"},
        )["SA4"]

        ds = AihwSocialHousingDataSource(root=data_dir / "aihw_social_housing")
        ds.attach_sa2_to_sa4_mapping(sa2_to_sa4)
        df = ds.load()
        assert len(df) >= 2000, f"only {len(df)} SA2s parsed; expected ~2,400+"
        expected_cols = {
            "social_housing_public_count",
            "social_housing_somih_count",
            "social_housing_community_count",
            "social_housing_total_count",
            "reference_period",
        }
        missing = expected_cols - set(df.columns)
        assert not missing, f"missing expected AIHW Social Housing columns: {sorted(missing)}"
        non_null = df[df["social_housing_total_count"].notna()]
        assert len(non_null) >= 1000, (
            f"only {len(non_null)} SA2s have non-null social-housing totals; "
            f"the SA4 downscale may be broken"
        )
        sample = non_null.iloc[0]
        print(
            f"         -> {len(df):,} SA2s; release {ds.resolved_release}; "
            f"sample SA2 {sample.name}: "
            f"{int(sample['social_housing_total_count']):,} social-housing dwellings "
            f"({int(sample['social_housing_public_count']):,} public)"
        )

    if not _check("SEIFA 2016+2021 (~2,196/2,366 SA2s, 4 indexes)", _check_seifa):
        failures.append("seifa")
    if not _check("ERP by SA2 (~2,454 SA2s, 25-year history)", _check_erp):
        failures.append("erp_by_sa2")
    if not _check("DSS payments (~2,454 SA2s, 22 payment types)", _check_dss):
        failures.append("dss_payments")
    if not _check("ABS Personal Income (~2,450 SA2s)", _check_abs_pia):
        failures.append("abs_personal_income")
    if not _check(
        "ABS Building Approvals (~2,450 SA2s, 9 metrics, 8 per-state cubes)",
        _check_abs_ba,
    ):
        failures.append("abs_building_approvals")
    if not _check(
        "ABS Counts of Businesses (~2,460 SA2s, 6 size bands, DC8 cube)",
        _check_abs_cab,
    ):
        failures.append("abs_business_counts")
    if not _check(
        "AIHW MH Prescriptions (~2,450 SA2s, 4 metrics, SA4 -> SA2 downscale)",
        _check_aihw_mh,
    ):
        failures.append("aihw_mh_prescriptions")
    if not _check(
        "AIHW MH Admitted Patient Care (~2,450 SA2s, 8 metrics, SA4 -> SA2 downscale)",
        _check_aihw_apc,
    ):
        failures.append("aihw_mh_admitted_patients")
    if not _check(
        "AIHW MH ED Presentations (~2,450 SA2s, 2 metrics, SA4 -> SA2 downscale)",
        _check_aihw_ed,
    ):
        failures.append("aihw_mh_ed_presentations")
    if not _check(
        "AIHW MH Medicare services (~2,450 SA2s, 4 metrics, SA4 -> SA2 downscale)",
        _check_aihw_medicare,
    ):
        failures.append("aihw_mh_medicare")
    if not _check(
        "AIHW Community MH care (~2,450 SA2s, 7 metrics, SA4 -> SA2 downscale)",
        _check_aihw_community,
    ):
        failures.append("aihw_mh_community")
    if not _check(
        "AIHW Social Housing (~2,450 SA2s, 4 programs, SA4 -> SA2 downscale)",
        _check_aihw_social_housing,
    ):
        failures.append("aihw_social_housing")

    # ------ PRESET source resolution against real GCP DataPack ------
    # Acid test for the "Real Data First" rule (see CLAUDE.md): every
    # PRESET feature's `source_fields()` must resolve cleanly. For GCP
    # refs (G01.*, G02.* etc) we use the live GCP VariableCatalog — same
    # surface that should have caught #23. For non-GCP refs (DSS.*,
    # ERP.*, ABS_PIA.*, etc — cross-dataset PRESETs introduced in v2.0+)
    # we use the registry's namespace-aware ``resolve_variable``, which
    # validates that the namespace + field map to a registered dataset.
    # The per-column lock-down for non-GCP datasets lives in
    # ``tests/test_spec_matches_fetcher_columns.py`` — checking it here
    # too would require fetching every dataset just to introspect
    # ``.load().columns``, which is overkill for a weekly drift check.
    # Closes #108.
    print("=== PRESET source-column resolution ===")
    from census_augment.catalog import VariableCatalog
    from census_augment.datasets import registry as dataset_registry
    from census_augment.features import features

    if metadata is None:
        print("  (skipped; DataPack metadata didn't load above.)")
    else:
        catalog = VariableCatalog(metadata)
        preset_specs = features.list_features()
        if not preset_specs:
            print("  (skipped; no PRESETs registered.)")
        else:

            def _is_gcp_ref(ref: str) -> bool:
                # GCP refs are "G<digits>.<field>" — matches the catalog's
                # table-id convention. Non-GCP refs use namespace prefixes
                # like "DSS.", "ERP.", "ABS_PIA.", etc.
                namespace = ref.partition(".")[0]
                return namespace.startswith("G") and namespace[1:].isdigit()

            def _make_preset_check(
                spec: object,
            ) -> Callable[[], None]:
                def _check_preset() -> None:
                    refs = spec.source_fields()  # type: ignore[attr-defined]
                    unresolved: list[tuple[str, str]] = []
                    gcp_count = 0
                    cross_count = 0
                    for ref in sorted(refs):
                        try:
                            if _is_gcp_ref(ref):
                                # Real GCP catalog lookup — the #23 gate.
                                catalog.resolve(ref)
                                gcp_count += 1
                            else:
                                # Registry namespace lookup — confirms the
                                # PRESET's cross-dataset ref points at a
                                # registered namespace + field. Per-column
                                # lock-down is in test_spec_matches_fetcher.
                                dataset_registry.resolve_variable(ref)
                                cross_count += 1
                        except Exception as e:  # noqa: BLE001
                            unresolved.append((ref, str(e).splitlines()[0]))
                    assert not unresolved, (
                        f"PRESET {spec.id!r} source refs unresolved: "  # type: ignore[attr-defined]
                        f"{unresolved}"
                    )
                    suffix = ""
                    if gcp_count and cross_count:
                        suffix = f" ({gcp_count} GCP + {cross_count} cross-dataset)"
                    elif cross_count:
                        suffix = f" ({cross_count} cross-dataset)"
                    print(
                        f"         -> {spec.id}: {len(refs)} source refs "  # type: ignore[attr-defined]
                        f"all resolve.{suffix}"
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
