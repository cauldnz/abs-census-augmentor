"""Tests for the DSS Payments dataset fetcher (spec §20, dataset id ``dss_payments``).

Hermetic tests mock both the CKAN ``package_show`` API response and
the XLSX download. Real-network smoke is in
``tools/verify_real_parsers.py``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._dss import (
    CKAN_PACKAGE_URL,
    DssDataSource,
    _payment_column_name,
    _release_id_from_name,
)


def _make_dss_xlsx(payment_data: list[tuple[str, dict[str, int | str]]]) -> bytes:
    """Build a DSS-shaped XLSX with the known sheet layout.

    ``payment_data`` is a list of (sa2_code, {payment_name: count, ...})
    tuples.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contents Page"
    ws.append(["Contents (skipped)"])

    # Add the SA2 sheet with the right structure.
    sa2 = wb.create_sheet("SA2")
    sa2.append(["Payment recipients by Statistical Area Level 2"])
    sa2.append([])

    # Pull all payment names (ordered from the first record).
    if not payment_data:
        payment_names: list[str] = []
    else:
        payment_names = list(payment_data[0][1].keys())

    sa2.append(["SA2", "SA2 name", *payment_names])

    for sa2_code, counts in payment_data:
        row: list[object] = [sa2_code, f"Test Area {sa2_code}"]
        for name in payment_names:
            row.append(counts.get(name, 0))
        sa2.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_ckan_response(
    *resources: dict[str, str | int],
) -> str:
    return json.dumps(
        {
            "success": True,
            "result": {
                "title": "DSS Payment Demographic Data",
                "resources": list(resources),
            },
        }
    )


@pytest.fixture
def dss_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "dss-cache"


# ---- name parsing ---------------------------------------------------------


def test_release_id_from_name_quarterly() -> None:
    assert _release_id_from_name(
        "Expanded DSS - December 2025"
    ) == "2025-Q4"
    assert _release_id_from_name(
        "Expanded DSS - September 2025"
    ) == "2025-Q3"
    assert _release_id_from_name("DSS - June 2024") == "2024-Q2"
    assert _release_id_from_name("DSS - March 2023") == "2023-Q1"


def test_release_id_from_name_no_match() -> None:
    assert _release_id_from_name("DSS without a date") is None
    assert _release_id_from_name("DSS - July 2025") is None  # not Q-end


def test_payment_column_name() -> None:
    assert _payment_column_name("Age Pension") == "age_pension_recipients"
    assert (
        _payment_column_name("ABSTUDY (Living allowance)")
        == "abstudy_living_allowance_recipients"
    )
    assert (
        _payment_column_name("Carer Allowance (Child Health Care Card only)")
        == "carer_allowance_child_health_care_card_only_recipients"
    )


# ---- release resolution --------------------------------------------------


@responses.activate
def test_resolve_latest_picks_highest_quarter(dss_data_dir: Path) -> None:
    fake_url_dec = "https://example.com/dss-dec-2025.xlsx"
    fake_url_sep = "https://example.com/dss-sep-2025.xlsx"
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": fake_url_dec,
                "last_modified": "2026-02-12",
            },
            {
                "name": "Expanded DSS - September 2025",
                "format": "excel (.xlsx)",
                "url": fake_url_sep,
                "last_modified": "2025-11-21",
            },
        ),
        status=200,
        content_type="application/json",
    )

    ds = DssDataSource(release="latest", root=dss_data_dir)
    assert ds.resolved_release == "2025-Q4"


@responses.activate
def test_resolve_specific_release(dss_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": "https://example.com/dss-dec-2025.xlsx",
                "last_modified": "2026-02-12",
            },
            {
                "name": "Expanded DSS - September 2025",
                "format": "excel (.xlsx)",
                "url": "https://example.com/dss-sep-2025.xlsx",
                "last_modified": "2025-11-21",
            },
        ),
        status=200,
        content_type="application/json",
    )

    ds = DssDataSource(release="2025-Q3", root=dss_data_dir)
    assert ds.resolved_release == "2025-Q3"


@responses.activate
def test_resolve_unknown_release_raises(dss_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": "https://example.com/dss-dec-2025.xlsx",
                "last_modified": "2026-02-12",
            }
        ),
        status=200,
        content_type="application/json",
    )

    ds = DssDataSource(release="2030-Q4", root=dss_data_dir)
    with pytest.raises(RuntimeError, match="not found"):
        _ = ds.resolved_release


@responses.activate
def test_resolve_no_resources_raises(dss_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(),
        status=200,
        content_type="application/json",
    )
    ds = DssDataSource(root=dss_data_dir)
    with pytest.raises(RuntimeError, match="No resources"):
        _ = ds.resolved_release


# ---- fetch ---------------------------------------------------------------


@responses.activate
def test_fetch_downloads_xlsx(dss_data_dir: Path) -> None:
    fake_url = "https://example.com/dss-dec-2025.xlsx"
    fake_xlsx = _make_dss_xlsx(
        [("117011326", {"Age Pension": 545, "JobSeeker Payment": 120})]
    )
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": fake_url,
                "last_modified": "2026-02-12",
            }
        ),
        status=200,
        content_type="application/json",
    )
    responses.add(responses.GET, fake_url, body=fake_xlsx, status=200)

    ds = DssDataSource(root=dss_data_dir)
    path = ds.fetch()
    assert path.exists()
    assert path.suffix == ".xlsx"


# ---- parse --------------------------------------------------------------


@responses.activate
def test_load_returns_sa2_indexed_dataframe(dss_data_dir: Path) -> None:
    fake_url = "https://example.com/dss-dec-2025.xlsx"
    fake_xlsx = _make_dss_xlsx([
        ("117011326", {
            "Age Pension": 545,
            "JobSeeker Payment": 120,
            "Disability Support Pension": 80,
        }),
        ("117011327", {
            "Age Pension": 380,
            "JobSeeker Payment": 75,
            "Disability Support Pension": 45,
        }),
        # An aggregate row with non-9-digit code that should be filtered.
        ("Australia", {
            "Age Pension": 9999999,
            "JobSeeker Payment": 9999999,
            "Disability Support Pension": 9999999,
        }),
    ])
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": fake_url,
                "last_modified": "2026-02-12",
            }
        ),
        status=200,
        content_type="application/json",
    )
    responses.add(responses.GET, fake_url, body=fake_xlsx, status=200)

    ds = DssDataSource(root=dss_data_dir)
    df = ds.load()

    assert df.index.name == "sa2_code_2021"
    assert "117011326" in df.index
    assert "117011327" in df.index
    assert "Australia" not in df.index

    # snake_case + _recipients suffix
    assert "age_pension_recipients" in df.columns
    assert "jobseeker_payment_recipients" in df.columns
    assert df.loc["117011326", "age_pension_recipients"] == 545
    assert df.loc["117011327", "disability_support_pension_recipients"] == 45

    # release_quarter attached
    assert "release_quarter" in df.columns
    assert df["release_quarter"].iloc[0] == "2025-Q4"


@responses.activate
def test_load_handles_suppressed_cells(dss_data_dir: Path) -> None:
    fake_url = "https://example.com/dss-dec-2025.xlsx"
    fake_xlsx = _make_dss_xlsx([
        ("117011326", {"Age Pension": "np", "JobSeeker Payment": 120}),
    ])
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": fake_url,
                "last_modified": "2026-02-12",
            }
        ),
        status=200,
        content_type="application/json",
    )
    responses.add(responses.GET, fake_url, body=fake_xlsx, status=200)

    ds = DssDataSource(root=dss_data_dir)
    df = ds.load()
    assert pd.isna(df.loc["117011326", "age_pension_recipients"])
    assert df.loc["117011326", "jobseeker_payment_recipients"] == 120


@responses.activate
def test_load_caches_parquet(dss_data_dir: Path) -> None:
    fake_url = "https://example.com/dss-dec-2025.xlsx"
    fake_xlsx = _make_dss_xlsx(
        [("117011326", {"Age Pension": 545})]
    )
    responses.add(
        responses.GET,
        CKAN_PACKAGE_URL,
        body=_make_ckan_response(
            {
                "name": "Expanded DSS - December 2025",
                "format": "excel (.xlsx)",
                "url": fake_url,
                "last_modified": "2026-02-12",
            }
        ),
        status=200,
        content_type="application/json",
    )
    responses.add(responses.GET, fake_url, body=fake_xlsx, status=200)

    ds = DssDataSource(root=dss_data_dir)
    df1 = ds.load()
    df2 = ds.load()
    pd.testing.assert_frame_equal(df1, df2)
