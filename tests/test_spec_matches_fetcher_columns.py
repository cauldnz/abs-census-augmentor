"""Lock-door test: every dataset's spec schema matches what its fetcher emits.

The augmentor's existing per-dataset tests assert the fetcher returns the
columns the fetcher is *intended* to return (against fixtures the test
author constructed). This test closes the missing loop in the other
direction: for each dataset, it parses the spec markdown's "Schema" table
and asserts every variable listed in the spec is actually present in
``fetcher.load().columns``.

The test catches the class of bug filed as issue #65 — spec markdowns
documenting columns the v1.5 fetchers never emit. Once a dataset spec
passes this test, future contributors who add a row to the spec table
without wiring up the column will see the assertion fail at PR time
rather than discovering it at downstream-blow-up time.

**Strict on missing.** A spec column that the fetcher doesn't emit is
a hard fail.

**Informational on extras.** A fetcher column not listed in the spec is
printed to test output (via ``pytest.warns(UserWarning)``) but not a
hard fail. Rationale: under-promising is fine — callers can always
introspect via ``fetcher.load().columns``. Over-promising (vapourware
in the docs) is the failure mode worth blocking.

**GCP is excluded** from this test. GCP has no single ``.load()`` entry
point — its variables are spread across per-table CSVs loaded on demand
by ``VariableCatalog``. A future variant could parametrize over GCP
variable references and assert each one resolves through the catalog;
that's a separate test concern.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import responses

from census_augment.datasets import registry
from census_augment.datasets._abs_ba import ABS_BA_LANDING_URL, AbsBaDataSource
from census_augment.datasets._abs_ba_lga import AbsBaLgaDataSource
from census_augment.datasets._abs_cab import _ABS_CAB_RELEASES, AbsBusinessCountsDataSource
from census_augment.datasets._abs_pia import ATO_LANDING_URL, AbsPiaDataSource
from census_augment.datasets._aihw_apc import (
    _AIHW_APC_URLS_BY_RELEASE,
    AihwMhAdmittedPatientsDataSource,
)
from census_augment.datasets._aihw_cmh import (
    _AIHW_CMH_URLS_BY_RELEASE,
    AihwMhCommunityDataSource,
)
from census_augment.datasets._aihw_ed import (
    _AIHW_ED_URLS_BY_RELEASE,
    AihwMhEdPresentationsDataSource,
)
from census_augment.datasets._aihw_medicare import (
    _AIHW_MEDICARE_URLS_BY_RELEASE,
    AihwMhMedicareDataSource,
)
from census_augment.datasets._aihw_mh import (
    _AIHW_RX_URLS_BY_RELEASE,
    AihwMhPrescriptionsDataSource,
)
from census_augment.datasets._aihw_social_housing import (
    _AIHW_SH_URLS_BY_RELEASE,
    AihwSocialHousingDataSource,
)
from census_augment.datasets._dss import CKAN_PACKAGE_URL, DssDataSource
from census_augment.datasets._erp import (
    ERP_AGE_SEX_LANDING_URL,
    ERP_LANDING_URL,
    ErpDataSource,
)
from census_augment.datasets._salm import _SALM_URLS_BY_RELEASE, SalmDataSource
from census_augment.datasets._seifa import DEFAULT_SEIFA_2021_URL, SeifaDataSource

# Synthetic-fixture builders live in the per-dataset test modules. Reusing
# them here keeps the lock-door test in lockstep with how each fetcher's
# own tests model its on-disk shape — if a fetcher's parser changes, its
# own test fixture is updated and this test inherits the change.
from tests.test_dataset_abs_ba import _make_landing_html as _ba_landing_html
from tests.test_dataset_abs_ba import _make_state_xlsx
from tests.test_dataset_abs_ba_lga import (
    _make_landing_html as _ba_lga_landing_html,
)
from tests.test_dataset_abs_ba_lga import (
    _make_lga_state_xlsx,
    _make_synthetic_correspondence,
)
from tests.test_dataset_abs_business_counts import _make_cab_xlsx
from tests.test_dataset_abs_pia import _make_ato_xlsx
from tests.test_dataset_salm import _make_salm_csv, _sa2_block
from tests.test_dataset_aihw_apc import _full_sa4_rows as _apc_full_sa4_rows
from tests.test_dataset_aihw_apc import _make_apc_zip
from tests.test_dataset_aihw_community import _full_sa4_rows as _cmh_full_sa4_rows
from tests.test_dataset_aihw_community import _make_cmh_zip
from tests.test_dataset_aihw_social_housing import _make_sh_xlsx
from tests.test_dataset_aihw_ed import _full_sa4_rows as _ed_full_sa4_rows
from tests.test_dataset_aihw_ed import _make_ed_zip
from tests.test_dataset_aihw_medicare import _full_sa4_rows as _medicare_full_sa4_rows
from tests.test_dataset_aihw_medicare import _make_medicare_zip
from tests.test_dataset_aihw_mh import _full_sa4_rows, _make_aihw_zip
from tests.test_dataset_abs_pia import _make_landing_html as _pia_landing_html
from tests.test_dataset_dss import _make_ckan_response, _make_dss_xlsx
from tests.test_dataset_erp import (
    _make_age_sex_landing_html,
    _make_age_sex_xlsx,
    _make_erp_xlsx,
)
from tests.test_dataset_erp import _make_landing_html as _erp_landing_html
from tests.test_dataset_seifa import _build_synthetic_seifa_xlsx


# ---- helpers ---------------------------------------------------------------


def _spec_columns(dataset_id: str) -> set[str]:
    """Return the set of column names declared in a dataset's spec markdown."""
    spec = registry.get(dataset_id)
    return {v.field for v in spec.variables}


def _check_spec_matches(dataset_id: str, fetcher_columns: set[str]) -> None:
    """Assert spec ⊆ fetcher; print (don't fail) on fetcher \\ spec."""
    spec_cols = _spec_columns(dataset_id)
    missing = spec_cols - fetcher_columns
    extras = fetcher_columns - spec_cols

    assert not missing, (
        f"{dataset_id}: spec claims columns the fetcher doesn't emit: "
        f"{sorted(missing)}. Either trim the spec or wire up the columns."
    )

    if extras:
        # Visible in pytest -v output. Not a fail — the fetcher emitting
        # bonus columns is fine, callers can introspect via .columns.
        warnings.warn(
            f"{dataset_id}: fetcher emits {len(extras)} undocumented columns "
            f"not in the spec schema table: {sorted(extras)}",
            UserWarning,
            stacklevel=2,
        )


# ---- ERP -------------------------------------------------------------------


@responses.activate
def test_spec_matches_fetcher__erp(tmp_path: Path) -> None:
    """ERP spec ⊆ ``ErpDataSource.load().columns``."""
    fake_xlsx = _make_erp_xlsx(
        [
            ("117011326", "Sydney CBD", "New South Wales", {2001: 5000, 2024: 12000}),
            ("117011327", "North Sydney", "New South Wales", {2001: 4500, 2024: 9500}),
        ]
    )
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_erp_landing_html(["2024-25"]),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.abs.gov.au/statistics/people/population/"
        "regional-population/2024-25/32180DS0003_2001-25.xlsx",
        body=fake_xlsx,
        status=200,
    )
    # Age/sex enrichment: register the 3235.0 mocks so the merged load
    # surfaces population_male / population_female / population_0_14 /
    # population_15_64 / population_65_plus / median_age — all of which
    # the spec front-matter now claims (post ERP-wishlist PR).
    responses.add(
        responses.GET,
        ERP_AGE_SEX_LANDING_URL,
        body=_make_age_sex_landing_html(["2024"]),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.abs.gov.au/statistics/people/population/"
        "regional-population-age-and-sex/2024/32350DS0002_2024.xlsx",
        body=_make_age_sex_xlsx(
            [
                ("117011326", "Sydney CBD", 6000, 6000, 100.0, 35.0, 15.0, 70.0, 15.0),
                ("117011327", "North Sydney", 4800, 4700, 102.0, 38.5, 10.0, 75.0, 15.0),
            ]
        ),
        status=200,
    )

    # Attach SA2 areas so the density column appears (matches the
    # spec's recent population_density_per_km2 addition). In production
    # Pipeline.from_config does this automatically from the boundary GDF.
    erp = ErpDataSource(root=tmp_path / "erp-cache")
    erp.attach_sa2_areas(
        {
            "117011326": 5.0,
            "117011327": 100.0,
        }
    )
    df = erp.load()
    _check_spec_matches("erp_by_sa2", set(df.columns))


# ---- DSS -------------------------------------------------------------------


@responses.activate
def test_spec_matches_fetcher__dss(tmp_path: Path) -> None:
    """DSS spec ⊆ ``DssDataSource.load().columns``."""
    fake_url = "https://example.com/dss-sep-2025.xlsx"
    # The fetcher snake-cases each XLSX column header verbatim + adds
    # ``_recipients`` suffix. To verify every spec'd column is emitted,
    # include each one's human-readable form in the fixture XLSX.
    payment_columns_for_fixture: dict[str, int | str] = {
        "Age Pension": 545,
        "JobSeeker Payment": 120,
        "Disability Support Pension": 80,
        "Parenting Payment Single": 60,
        "Parenting Payment Partnered": 35,
        "Carer Payment": 95,
        "Youth Allowance Other": 25,
        "Youth Allowance Student And Apprentice": 110,
        "Commonwealth Rent Assistance": 480,
    }
    fake_xlsx = _make_dss_xlsx(
        [
            ("117011326", payment_columns_for_fixture),
            ("117011327", payment_columns_for_fixture),
        ]
    )
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - September 2025",
                "format": "excel (.xlsx)",
                "url": fake_url,
                "last_modified": "2025-12-01",
            }
        ),
        status=200,
        content_type="application/json",
    )
    responses.add(responses.GET, fake_url, body=fake_xlsx, status=200)

    df = DssDataSource(root=tmp_path / "dss-cache").load()
    _check_spec_matches("dss_payments", set(df.columns))


# ---- ABS_PIA ---------------------------------------------------------------


@responses.activate
def test_spec_matches_fetcher__abs_pia(tmp_path: Path) -> None:
    """ABS_PIA spec ⊆ ``AbsPiaDataSource.load().columns``."""
    fake_xlsx = _make_ato_xlsx(
        [
            (
                "117011326",
                {
                    "income_earners_count": 8500,
                    "median_age_of_earners": 38,
                    "sum_total_income": 720_000_000,
                    "median_total_income": 78_000,
                    "mean_total_income": 84_700,
                },
            ),
        ]
    )
    responses.add(
        responses.GET,
        ATO_LANDING_URL,
        body=_pia_landing_html(["2022-23"]),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
        "personal-income-australia/2022-23/Table%201%20-%20Total%20income.xlsx",
        body=fake_xlsx,
        status=200,
    )

    df = AbsPiaDataSource(root=tmp_path / "pia-cache").load()
    _check_spec_matches("abs_personal_income", set(df.columns))


# ---- SEIFA -----------------------------------------------------------------


@responses.activate
def test_spec_matches_fetcher__seifa(tmp_path: Path) -> None:
    """SEIFA spec ⊆ ``SeifaDataSource.load().columns``."""
    responses.add(
        responses.GET,
        DEFAULT_SEIFA_2021_URL,
        body=_build_synthetic_seifa_xlsx(),
        status=200,
    )

    df = SeifaDataSource(root=tmp_path / "seifa-cache").load()
    _check_spec_matches("seifa", set(df.columns))


# ---- ABS Building Approvals ------------------------------------------------


@responses.activate
def test_spec_matches_fetcher__abs_building_approvals(tmp_path: Path) -> None:
    """ABS Building Approvals spec ⊆ ``AbsBaDataSource.load().columns``.

    Eight per-state cubes; the fixture builder is the same as
    ``test_dataset_abs_ba``'s. We only need one SA2 row to land in the
    parsed DataFrame for the spec-column check.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_ba_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    # All 9 ABS BA metric columns populated in the fixture so any spec
    # column the fetcher claims to emit ends up in the DataFrame.
    full_record = {
        "new_houses_count": 100,
        "new_other_residential_building_count": 50,
        "total_dwellings_count": 150,
        "value_new_houses": 600_000,
        "value_new_other_residential_building": 250_000,
        "value_alterations_additions_conversions": 80_000,
        "value_total_residential_building": 930_000,
        "value_non_residential_building": 200_000,
        "value_total_building": 1_130_000,
    }
    for product in ("do002", "do006", "do010", "do014", "do018", "do022", "do026", "do030"):
        # Stick one SA2 into NSW; other states empty.
        records = [("117011326", full_record)] if product == "do002" else []
        responses.add(
            responses.GET,
            f"{base}87310{product}_202603.xlsx",
            body=_make_state_xlsx(sa2_records=records),
            status=200,
        )

    df = AbsBaDataSource(root=tmp_path / "abs-ba-cache").load()
    _check_spec_matches("abs_building_approvals", set(df.columns))


# ---- ABS Building Approvals (LGA → SA2 downscale) --------------------------


@responses.activate
def test_spec_matches_fetcher__abs_building_approvals_lga(tmp_path: Path) -> None:
    """ABS BA LGA spec ⊆ ``AbsBaLgaDataSource.load().columns``.

    The dataset is LGA-keyed; we attach a synthetic
    :class:`LgaSa2Correspondence` so the fetcher's load() can downscale
    to SA2-keyed output matching the rest of the registry contract.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_ba_lga_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    # Full record so every spec'd column lands in the output
    full_record = {
        "new_houses_count": 100,
        "new_other_residential_building_count": 50,
        "total_dwellings_count": 150,
        "value_new_houses": 60_000,
        "value_new_other_residential_building": 25_000,
        "value_alterations_additions_conversions": 10_000,
        "value_total_residential_building": 95_000,
        "value_non_residential_building": 30_000,
        "value_total_building": 125_000,
    }
    for product in ("do004", "do008", "do012", "do016", "do020", "do024", "do028", "do032"):
        records = [("10500", full_record)] if product == "do004" else []
        responses.add(
            responses.GET,
            f"{base}87310{product}_202603.xlsx",
            body=_make_lga_state_xlsx(lga_records=records),
            status=200,
        )

    ds = AbsBaLgaDataSource(root=tmp_path / "abs-ba-lga-cache")
    ds.attach_correspondence(_make_synthetic_correspondence({"10500": [("206011001", 1.0)]}))
    df = ds.load()
    _check_spec_matches("abs_building_approvals_lga", set(df.columns))


# ---- ABS Counts of Australian Businesses -----------------------------------


@responses.activate
def test_spec_matches_fetcher__abs_business_counts(tmp_path: Path) -> None:
    """ABS CAB spec ⊆ ``AbsBusinessCountsDataSource.load().columns``."""
    rows = [
        ("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18),
        ("G", "Retail Trade", "101021007", "Braidwood", 7, 3, 1, 0, 0, 11),
    ]
    responses.add(
        responses.GET,
        _ABS_CAB_RELEASES["2025"]["url"],
        body=_make_cab_xlsx(rows=rows),
        status=200,
    )
    df = AbsBusinessCountsDataSource(root=tmp_path / "abs-cab-cache").load()
    _check_spec_matches("abs_business_counts", set(df.columns))


@responses.activate
def test_spec_matches_fetcher__salm_labour_force(tmp_path: Path) -> None:
    """SALM spec ⊆ ``SalmDataSource.load().columns``."""
    rows = _sa2_block(
        "Braidwood",
        "101021007",
        unemployment=[50, 57],
        labour_force=["2,200", "2,318"],
        rate=[2.3, 2.5],
    )
    responses.add(
        responses.GET,
        _SALM_URLS_BY_RELEASE["2025-Q4"],
        body=_make_salm_csv(rows=rows),
        status=200,
    )
    df = SalmDataSource(root=tmp_path / "salm-cache").load()
    _check_spec_matches("salm_labour_force", set(df.columns))


# ---- AIHW Mental Health Prescriptions --------------------------------------


@responses.activate
def test_spec_matches_fetcher__aihw_mh_prescriptions(tmp_path: Path) -> None:
    """AIHW MH Prescriptions spec ⊆ ``AihwMhPrescriptionsDataSource.load().columns``.

    The dataset is SA4-keyed; we attach a synthetic SA2 -> SA4 mapping
    so the fetcher's load() can downscale to a SA2-keyed DataFrame
    matching the rest of the registry contract.
    """
    rows = _full_sa4_rows("SA4101", "Central Coast")
    responses.add(
        responses.GET,
        _AIHW_RX_URLS_BY_RELEASE["2024-25"],
        body=_make_aihw_zip(rows=rows),
        status=200,
    )

    ds = AihwMhPrescriptionsDataSource(root=tmp_path / "aihw-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_mh_prescriptions", set(df.columns))


# ---- AIHW Mental Health Admitted Patient Care ------------------------------


@responses.activate
def test_spec_matches_fetcher__aihw_mh_admitted_patients(tmp_path: Path) -> None:
    """AIHW APC spec ⊆ ``AihwMhAdmittedPatientsDataSource.load().columns``.

    SA4-keyed; attach a synthetic SA2 -> SA4 mapping so load() downscales
    to a SA2-keyed DataFrame matching the registry contract.
    """
    rows = _apc_full_sa4_rows("SA4101")
    responses.add(
        responses.GET,
        _AIHW_APC_URLS_BY_RELEASE["2023-24"],
        body=_make_apc_zip(rows=rows),
        status=200,
    )

    ds = AihwMhAdmittedPatientsDataSource(root=tmp_path / "aihw-apc-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_mh_admitted_patients", set(df.columns))


# ---- AIHW Mental Health ED Presentations -----------------------------------


@responses.activate
def test_spec_matches_fetcher__aihw_mh_ed_presentations(tmp_path: Path) -> None:
    """AIHW ED spec ⊆ ``AihwMhEdPresentationsDataSource.load().columns``."""
    rows = _ed_full_sa4_rows("SA4101")
    responses.add(
        responses.GET,
        _AIHW_ED_URLS_BY_RELEASE["2023-24"],
        body=_make_ed_zip(rows=rows),
        status=200,
    )

    ds = AihwMhEdPresentationsDataSource(root=tmp_path / "aihw-ed-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_mh_ed_presentations", set(df.columns))


# ---- AIHW Medicare-subsidised MH services ----------------------------------


@responses.activate
def test_spec_matches_fetcher__aihw_mh_medicare(tmp_path: Path) -> None:
    """AIHW Medicare spec ⊆ ``AihwMhMedicareDataSource.load().columns``."""
    rows = _medicare_full_sa4_rows("SA4-101")
    responses.add(
        responses.GET,
        _AIHW_MEDICARE_URLS_BY_RELEASE["2024-25"],
        body=_make_medicare_zip(rows=rows),
        status=200,
    )

    ds = AihwMhMedicareDataSource(root=tmp_path / "aihw-medicare-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_mh_medicare", set(df.columns))


@responses.activate
def test_spec_matches_fetcher__aihw_mh_community(tmp_path: Path) -> None:
    """AIHW Community MH spec ⊆ ``AihwMhCommunityDataSource.load().columns``."""
    rows = _cmh_full_sa4_rows("101")
    responses.add(
        responses.GET,
        _AIHW_CMH_URLS_BY_RELEASE["2023-24"],
        body=_make_cmh_zip(rows=rows),
        status=200,
    )

    ds = AihwMhCommunityDataSource(root=tmp_path / "aihw-cmh-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_mh_community", set(df.columns))


@responses.activate
def test_spec_matches_fetcher__aihw_social_housing(tmp_path: Path) -> None:
    """AIHW Social Housing spec ⊆ ``AihwSocialHousingDataSource.load().columns``."""
    rows = [("NSW", "101", "Capital Region", 1980, 62, 1022, 3065)]
    responses.add(
        responses.GET,
        _AIHW_SH_URLS_BY_RELEASE["2023"]["url"],
        body=_make_sh_xlsx(rows=rows),
        status=200,
    )
    ds = AihwSocialHousingDataSource(root=tmp_path / "aihw-sh-cache")
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    _check_spec_matches("aihw_social_housing", set(df.columns))


# ---- guardrail: every registered dataset (except GCP) has a lock-door test ---


def test_every_registered_dataset_has_a_lock_door_test() -> None:
    """Fails if someone adds a new dataset to the registry without adding a
    corresponding ``test_spec_matches_fetcher__<name>`` test in this module.

    GCP is intentionally excluded (it has no single ``.load()`` entry point).
    """
    covered = {
        "erp_by_sa2",
        "dss_payments",
        "abs_personal_income",
        "seifa",
        "abs_building_approvals",
        "abs_building_approvals_lga",
        "abs_business_counts",
        "salm_labour_force",
        "aihw_mh_prescriptions",
        "aihw_mh_admitted_patients",
        "aihw_mh_ed_presentations",
        "aihw_mh_medicare",
        "aihw_mh_community",
        "aihw_social_housing",
    }
    intentionally_skipped = {
        "gcp",  # multi-table loader; covered via VariableCatalog tests
    }
    registered = {s.id for s in registry.list_datasets()}

    expected_covered = registered - intentionally_skipped
    missing_tests = expected_covered - covered
    assert not missing_tests, (
        f"New dataset(s) {sorted(missing_tests)} registered without a "
        f"lock-door test. Add `test_spec_matches_fetcher__<name>` to "
        f"tests/test_spec_matches_fetcher_columns.py (or add to "
        f"`intentionally_skipped` with justification)."
    )
