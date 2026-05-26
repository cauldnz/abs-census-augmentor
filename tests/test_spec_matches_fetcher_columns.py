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
from census_augment.datasets._abs_pia import ATO_LANDING_URL, AbsPiaDataSource
from census_augment.datasets._dss import CKAN_PACKAGE_URL, DssDataSource
from census_augment.datasets._erp import (
    ERP_AGE_SEX_LANDING_URL,
    ERP_LANDING_URL,
    ErpDataSource,
)
from census_augment.datasets._seifa import DEFAULT_SEIFA_2021_URL, SeifaDataSource

# Synthetic-fixture builders live in the per-dataset test modules. Reusing
# them here keeps the lock-door test in lockstep with how each fetcher's
# own tests model its on-disk shape — if a fetcher's parser changes, its
# own test fixture is updated and this test inherits the change.
from tests.test_dataset_abs_pia import _make_ato_xlsx
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

    df = ErpDataSource(root=tmp_path / "erp-cache").load()
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
