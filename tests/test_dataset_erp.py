"""Tests for the ERP dataset fetcher (spec §20, dataset id ``erp_by_sa2``)."""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._erp import (
    ERP_LANDING_URL,
    ErpDataSource,
    _state_to_abbreviation,
)


def _make_landing_html(release_periods: list[str]) -> str:
    """Synthetic landing-page HTML with DS0003 links for the given periods.

    Each entry is a "YYYY-YY" or "YYYY-YYYY" string like "2024-25".
    """
    body_parts = ["<html><body>"]
    for period in release_periods:
        body_parts.append(
            f'<a href="/statistics/people/population/regional-population/'
            f'{period}/32180DS0003_2001-25.xlsx">DS0003</a>'
        )
    body_parts.append("</body></html>")
    return "\n".join(body_parts)


def _make_erp_xlsx(
    sa2_records: list[tuple[str, str, str, dict[int, int]]],
) -> bytes:
    """Build an ERP-shaped XLSX with Table 1 mirroring the real layout.

    sa2_records: list of (sa2_code, sa2_name, state_name, year→population dict)
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contents"
    ws.append(["Contents (skipped)"])

    t1 = wb.create_sheet("Table 1")
    t1.append(["This tab has one table"])
    t1.append(["Table 1. ERP estimates"])
    t1.append([])
    t1.append([])
    t1.append([])
    # Row 5 (header) — ABS uses cols 0-7 for the geography hierarchy
    # (state code/name, GCCSA, SA4, SA3) and cols 8-9 for SA2.
    years_in_data = sorted({y for *_, year_dict in sa2_records for y in year_dict})
    header = [
        "S/T code",
        "S/T name",
        "GCCSA code",
        "GCCSA name",
        "SA4 code",
        "SA4 name",
        "SA3 code",
        "SA3 name",
        "SA2 code",
        "SA2 name",
    ] + list(years_in_data)
    t1.append(header)

    for sa2_code, sa2_name, state_name, populations in sa2_records:
        row: list[object] = [
            "1",
            state_name,
            "1RNSW",
            "Rest of NSW",
            "101",
            "Capital Region",
            "10102",
            "Queanbeyan",
            sa2_code,
            sa2_name,
        ]
        for year in years_in_data:
            row.append(populations.get(year, None))
        t1.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def erp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "erp-cache"


# ---- helpers -------------------------------------------------------------


def test_state_to_abbreviation_known() -> None:
    assert _state_to_abbreviation("New South Wales") == "NSW"
    assert _state_to_abbreviation("victoria") == "VIC"
    assert _state_to_abbreviation("Australian Capital Territory") == "ACT"


def test_state_to_abbreviation_unknown_falls_back() -> None:
    # Unknown name: take the first 3 chars uppercase.
    assert _state_to_abbreviation("Vibing Wonderland") == "VIB"


# ---- release resolution --------------------------------------------------


@responses.activate
def test_resolve_latest_picks_highest_period(erp_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2023-24", "2024-25"]),
        status=200,
    )
    ds = ErpDataSource(release="latest", root=erp_data_dir)
    assert ds.resolved_release == "2024"


@responses.activate
def test_resolve_specific_release(erp_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2023-24", "2024-25"]),
        status=200,
    )
    ds = ErpDataSource(release="2023", root=erp_data_dir)
    assert ds.resolved_release == "2023"


@responses.activate
def test_resolve_unknown_release_raises(erp_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2024-25"]),
        status=200,
    )
    ds = ErpDataSource(release="2030", root=erp_data_dir)
    with pytest.raises(RuntimeError, match="not found"):
        _ = ds.resolved_release


@responses.activate
def test_resolve_no_links_raises(erp_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body="<html><body>Nothing to see</body></html>",
        status=200,
    )
    ds = ErpDataSource(release="latest", root=erp_data_dir)
    with pytest.raises(RuntimeError, match="Could not find"):
        _ = ds.resolved_release


# ---- fetch + parse -------------------------------------------------------


@responses.activate
def test_load_returns_sa2_indexed_dataframe(erp_data_dir: Path) -> None:
    fake_xlsx = _make_erp_xlsx(
        [
            ("117011326", "Sydney CBD", "New South Wales", {2001: 5000, 2010: 7500, 2024: 12000}),
            ("117011327", "North Sydney", "New South Wales", {2001: 4500, 2010: 6800, 2024: 9500}),
            # Aggregate row that should be skipped.
            ("Australia", "Australia", "Australia", {2001: 19000000, 2024: 27000000}),
        ]
    )
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2024-25"]),
        status=200,
    )
    download_url = (
        "https://www.abs.gov.au/statistics/people/population/"
        "regional-population/2024-25/32180DS0003_2001-25.xlsx"
    )
    responses.add(responses.GET, download_url, body=fake_xlsx, status=200)

    ds = ErpDataSource(root=erp_data_dir)
    df = ds.load()

    assert df.index.name == "sa2_code_2021"
    assert "117011326" in df.index
    assert "117011327" in df.index
    assert "Australia" not in df.index
    assert "population_total" in df.columns
    assert "reference_year" in df.columns
    assert df.loc["117011326", "population_total"] == 12000
    assert df.loc["117011326", "reference_year"] == 2024
    assert df.loc["117011326", "population_history_2001"] == 5000
    assert df.loc["117011326", "state_abbreviation"] == "NSW"


@responses.activate
def test_load_handles_suppressed_cells(erp_data_dir: Path) -> None:
    fake_xlsx = _make_erp_xlsx(
        [
            ("117011326", "Sydney CBD", "New South Wales", {2001: "np", 2024: 12000}),
        ]
    )
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2024-25"]),
        status=200,
    )
    download_url = (
        "https://www.abs.gov.au/statistics/people/population/"
        "regional-population/2024-25/32180DS0003_2001-25.xlsx"
    )
    responses.add(responses.GET, download_url, body=fake_xlsx, status=200)

    ds = ErpDataSource(root=erp_data_dir)
    df = ds.load()
    assert pd.isna(df.loc["117011326", "population_history_2001"])
    assert df.loc["117011326", "population_history_2024"] == 12000


@responses.activate
def test_load_caches_parquet(erp_data_dir: Path) -> None:
    fake_xlsx = _make_erp_xlsx(
        [
            ("117011326", "Sydney CBD", "New South Wales", {2024: 12000}),
        ]
    )
    responses.add(
        responses.GET,
        ERP_LANDING_URL,
        body=_make_landing_html(["2024-25"]),
        status=200,
    )
    download_url = (
        "https://www.abs.gov.au/statistics/people/population/"
        "regional-population/2024-25/32180DS0003_2001-25.xlsx"
    )
    responses.add(responses.GET, download_url, body=fake_xlsx, status=200)

    ds = ErpDataSource(root=erp_data_dir)
    df1 = ds.load()
    df2 = ds.load()
    pd.testing.assert_frame_equal(df1, df2)
