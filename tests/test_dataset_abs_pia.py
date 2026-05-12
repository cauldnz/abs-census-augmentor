"""Tests for the ATO Personal Income dataset fetcher (spec §20)."""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._abs_pia import (
    ATO_LANDING_URL,
    AbsPiaDataSource,
)


def _make_landing_html(periods: list[str]) -> str:
    body = ["<html><body>"]
    for period in periods:
        body.append(
            f'<a href="/statistics/labour/earnings-and-working-conditions/'
            f"personal-income-australia/{period}/"
            f'Table%201%20-%20Total%20income.xlsx">Table 1</a>'
        )
    body.append("</body></html>")
    return "\n".join(body)


def _make_ato_xlsx(
    sa2_records: list[tuple[str, dict[str, int | float]]],
    *,
    years: list[str] | None = None,
) -> bytes:
    """Build an ATO Table 1.4-shaped XLSX.

    sa2_records: list of (sa2_code, {output_field: latest-year value})
    years: list of financial-year strings (e.g. ["2018-19", ...,
        "2022-23"]). The last year is the "latest"; values for that
        year come from sa2_records.
    """
    if years is None:
        years = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contents"
    ws.append(["Contents (skipped)"])

    sa2 = wb.create_sheet("Table 1.4")
    sa2.append(["Australian Bureau of Statistics"])
    sa2.append(["Personal Income in Australia: Table 1"])
    sa2.append(["Released..."])
    sa2.append(["Table 1.4"])
    sa2.append([])
    # Row 5 — group headers at fixed cols (matches the real layout).
    group_row: list[object] = [None] * (2 + 5 * 5)
    group_row[2] = "Earners (persons)"
    group_row[7] = "Median age of earners (years)"
    group_row[12] = "Sum ($)"
    group_row[17] = "Median ($)"
    group_row[22] = "Mean ($)"
    sa2.append(group_row)

    # Row 6 — "SA2", "SA2 NAME", years × 5 (per group).
    year_row: list[object] = ["SA2", "SA2 NAME"]
    for _ in range(5):  # 5 groups
        year_row.extend(years)
    sa2.append(year_row)

    # Australia aggregate (skipped).
    aggregate_row: list[object] = ["Australia", ""]
    for _ in range(5):
        for _ in years:
            aggregate_row.append(99999999)
    sa2.append(aggregate_row)

    for sa2_code, fields in sa2_records:
        # For each group, we put zeros for non-latest years and the
        # actual value for the latest year (matching the test's intent
        # that the parser picks the latest year per group).
        row: list[object] = [sa2_code, f"Test {sa2_code}"]
        # Earners group: latest = income_earners_count
        for y in years:
            if y == years[-1]:
                row.append(fields.get("income_earners_count", 0))
            else:
                row.append(0)
        # Median age
        for y in years:
            row.append(fields.get("median_age_of_earners", 0) if y == years[-1] else 0)
        # Sum ($)
        for y in years:
            row.append(fields.get("sum_total_income", 0) if y == years[-1] else 0)
        # Median ($)
        for y in years:
            row.append(fields.get("median_total_income", 0) if y == years[-1] else 0)
        # Mean ($)
        for y in years:
            row.append(fields.get("mean_total_income", 0) if y == years[-1] else 0)
        sa2.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def ato_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "ato-cache"


@responses.activate
def test_resolve_latest_picks_highest_period(ato_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ATO_LANDING_URL,
        body=_make_landing_html(["2021-22", "2022-23"]),
        status=200,
    )
    ds = AbsPiaDataSource(release="latest", root=ato_data_dir)
    assert ds.resolved_release == "2022-23"


@responses.activate
def test_resolve_no_links_raises(ato_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ATO_LANDING_URL,
        body="<html></html>",
        status=200,
    )
    ds = AbsPiaDataSource(release="latest", root=ato_data_dir)
    with pytest.raises(RuntimeError, match="Could not find"):
        _ = ds.resolved_release


@responses.activate
def test_load_returns_sa2_indexed_dataframe(ato_data_dir: Path) -> None:
    fake_xlsx = _make_ato_xlsx(
        [
            (
                "117011326",
                {
                    "income_earners_count": 8500,
                    "median_age_of_earners": 38,
                    "sum_total_income": 850_000_000,
                    "median_total_income": 75_000,
                    "mean_total_income": 100_000,
                },
            ),
            (
                "117011327",
                {
                    "income_earners_count": 6200,
                    "median_age_of_earners": 42,
                    "sum_total_income": 520_000_000,
                    "median_total_income": 65_000,
                    "mean_total_income": 84_000,
                },
            ),
        ]
    )
    download_url = (
        "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
        "personal-income-australia/2022-23/Table%201%20-%20Total%20income.xlsx"
    )
    responses.add(
        responses.GET,
        ATO_LANDING_URL,
        body=_make_landing_html(["2022-23"]),
        status=200,
    )
    responses.add(responses.GET, download_url, body=fake_xlsx, status=200)

    ds = AbsPiaDataSource(root=ato_data_dir)
    df = ds.load()

    assert df.index.name == "sa2_code_2021"
    assert "117011326" in df.index
    assert "117011327" in df.index
    # Aggregate row (col 0 = "Australia") is filtered.
    assert "Australia" not in df.index

    # Latest financial year's values land in the right output columns.
    assert df.loc["117011326", "income_earners_count"] == 8500
    assert df.loc["117011326", "median_age_of_earners"] == 38
    assert df.loc["117011326", "sum_total_income"] == 850_000_000
    assert df.loc["117011326", "median_total_income"] == 75_000
    assert df.loc["117011326", "mean_total_income"] == 100_000

    # Reference financial year attached.
    assert "reference_financial_year" in df.columns
    assert df["reference_financial_year"].iloc[0] == "2022-23"


@responses.activate
def test_load_handles_suppressed_cells(ato_data_dir: Path) -> None:
    fake_xlsx = _make_ato_xlsx(
        [
            (
                "117011326",
                {
                    "income_earners_count": 8500,
                    "median_age_of_earners": "np",
                    "sum_total_income": "np",
                    "median_total_income": "np",
                    "mean_total_income": "np",
                },
            ),
        ]
    )
    responses.add(
        responses.GET,
        ATO_LANDING_URL,
        body=_make_landing_html(["2022-23"]),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
        "personal-income-australia/2022-23/Table%201%20-%20Total%20income.xlsx",
        body=fake_xlsx,
        status=200,
    )

    ds = AbsPiaDataSource(root=ato_data_dir)
    df = ds.load()
    assert df.loc["117011326", "income_earners_count"] == 8500
    assert pd.isna(df.loc["117011326", "median_total_income"])
    assert pd.isna(df.loc["117011326", "mean_total_income"])
