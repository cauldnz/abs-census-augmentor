"""Tests for the ABS Building Approvals dataset fetcher (spec §20).

Synthetic XLSX fixtures mirror the live ABS Building Approvals SA2 cube
schema probed on 2026-06-01 (see ``tools/probe_new_datasets.py``):

- Sheet ``Table_1``.
- Row 4 (0-indexed) = column headers (mixed metric labels matching
  ``_EXPECTED_HEADER_PREFIXES``).
- Row 5 = units row (``no.`` / ``$'000``).
- Row 6 onwards = data, column A mixes 9-digit SA2 codes with parent-level
  aggregate codes (state, GCC, SA4, SA3).

The fetcher fans out across 8 state cubes; the tests cover landing-page
resolution, single-state parse correctness, and the multi-state combine.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._abs_ba import (
    ABS_BA_LANDING_URL,
    AbsBaDataSource,
)


def _make_state_xlsx(
    *,
    sa2_records: list[tuple[str, dict[str, int | float | str]]],
    aggregate_rows: list[tuple[str, str]] | None = None,
) -> bytes:
    """Build one per-state ABS BA-shaped XLSX.

    ``sa2_records`` is a list of (sa2_code, {field_name: value}). Use the
    output column names from ``_OUTPUT_COLUMNS`` as field keys.

    ``aggregate_rows`` injects rows with non-9-digit codes (state code,
    GCC code, SA4 code, etc.) so the parser's filter can be exercised.
    """
    wb = openpyxl.Workbook()
    ws_contents = wb.active
    ws_contents.title = "Contents"
    ws_contents.append(["Contents"])

    ws = wb.create_sheet("Table_1")
    # Row 0 - 3: preamble (mirrors the real layout's 4 preamble rows)
    ws.append([])
    ws.append(["87310DO002 Building Approvals, Australia (synthetic)"])
    ws.append(["Released (synthetic)"])
    ws.append(["Table 1. NSW, SA2 excel data cube"])
    # Row 4: column headers (exact strings from real data probed 2026-06-01)
    ws.append(
        [
            "",
            "",
            "New houses",
            "New other residential building",
            "Total dwellings",
            "Value of new houses",
            "Value of new other residential building",
            "Value of alterations & additions including conversions",
            "Value of total residential building",
            "Value of non-residential building",
            "Value of total building",
        ]
    )
    # Row 5: units
    ws.append(["", "", "no.", "no.", "no.", "$'000", "$'000", "$'000", "$'000", "$'000", "$'000"])

    # Inject aggregate rows (state / GCC / SA4 / SA3) the parser should skip
    if aggregate_rows:
        for code, name in aggregate_rows:
            ws.append(
                [
                    code,
                    name,
                    9999,
                    9999,
                    9999,
                    9_999_999,
                    9_999_999,
                    9_999_999,
                    9_999_999,
                    9_999_999,
                    9_999_999,
                ]
            )

    # SA2 data rows
    output_columns = [
        "new_houses_count",
        "new_other_residential_building_count",
        "total_dwellings_count",
        "value_new_houses",
        "value_new_other_residential_building",
        "value_alterations_additions_conversions",
        "value_total_residential_building",
        "value_non_residential_building",
        "value_total_building",
    ]
    for sa2_code, fields in sa2_records:
        row: list[object] = [sa2_code, f"Test SA2 {sa2_code}"]
        for col in output_columns:
            row.append(fields.get(col, 0))
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_landing_html(yyyymm: str) -> str:
    """Build a landing-page HTML carrying the 8 per-state SA2 cube URLs
    (both complete-FY ``do002``/... and FYTD ``do003``/... series) for
    the given month suffix.
    """
    # ABS uses "mar-2026" / "jan-2026" forms in the URL path; we don't
    # actually need the month-text in tests since the parser regex only
    # cares about the do<NN> + yyyymm trailer. Use a generic stub.
    month_path = f"mar-{yyyymm[:4]}"
    base = (
        f"/statistics/industry/building-and-construction/building-approvals-australia/{month_path}/"
    )
    products = [
        "do002",
        "do003",
        "do006",
        "do007",
        "do010",
        "do011",
        "do014",
        "do015",
        "do018",
        "do019",
        "do022",
        "do023",
        "do026",
        "do027",
        "do030",
        "do031",
    ]
    links = "\n".join(f'<a href="{base}87310{p}_{yyyymm}.xlsx">{p}</a>' for p in products)
    return f"<html><body>{links}</body></html>"


@pytest.fixture
def ba_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "abs-ba-cache"


# ---- release resolution --------------------------------------------------


@responses.activate
def test_resolve_latest_picks_most_recent_yyyymm(ba_data_dir: Path) -> None:
    """When two monthly snapshots are linked, pick the higher yyyymm
    (later release). Each monthly release covers the same FY but newer
    data — we want the newest.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaDataSource(release="latest", root=ba_data_dir)
    # March 2026 release (yyyymm=202603) covers FY 2024-25 (complete).
    assert ds.resolved_release == "2024-25"


@responses.activate
def test_resolve_specific_complete_fy(ba_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaDataSource(release="2024-25", root=ba_data_dir)
    assert ds.resolved_release == "2024-25"


@responses.activate
def test_resolve_specific_ytd(ba_data_dir: Path) -> None:
    """Asking for the FYTD release picks the do003-series cubes."""
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaDataSource(release="2025-26", root=ba_data_dir)
    assert ds.resolved_release == "2025-26"


@responses.activate
def test_resolve_unknown_release_raises(ba_data_dir: Path) -> None:
    """Old FYs aren't in the latest monthly release; raise loudly so
    callers know to check the ABS archive.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaDataSource(release="2019-20", root=ba_data_dir)
    with pytest.raises(RuntimeError, match="not available"):
        _ = ds.resolved_release


@responses.activate
def test_resolve_no_links_raises(ba_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body="<html><body>no abs ba links here</body></html>",
        status=200,
    )
    ds = AbsBaDataSource(release="latest", root=ba_data_dir)
    with pytest.raises(RuntimeError, match="Could not find"):
        _ = ds.resolved_release


@responses.activate
def test_resolve_handles_jul_to_jun_fy_window(ba_data_dir: Path) -> None:
    """A release published in October 2025 (yyyymm=202510) covers FY
    2024-25 — the FY that ended the previous June.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202510"),
        status=200,
    )
    ds = AbsBaDataSource(release="latest", root=ba_data_dir)
    assert ds.resolved_release == "2024-25"


@responses.activate
def test_resolve_handles_jan_to_jun_fy_window(ba_data_dir: Path) -> None:
    """A release published in February 2026 (yyyymm=202602) still covers
    the FY that ended the previous June (2024-25).
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202602"),
        status=200,
    )
    ds = AbsBaDataSource(release="latest", root=ba_data_dir)
    assert ds.resolved_release == "2024-25"


# ---- parse --------------------------------------------------------------


@responses.activate
def test_load_returns_sa2_indexed_dataframe(ba_data_dir: Path) -> None:
    """End-to-end: landing page -> 8 state downloads -> combined SA2
    DataFrame with all expected output columns.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    # Stub each of the 8 state complete-FY cube URLs with a tiny
    # synthetic XLSX. Mimic the real ABS layout: each state cube has
    # the state aggregate plus a handful of SA2 rows.
    states = [
        ("NSW", "do002", "117011326", 22202),
        ("VIC", "do006", "201011001", 18504),
        ("QLD", "do010", "301011001", 16302),
        ("SA", "do014", "401011001", 4801),
        ("WA", "do018", "501011001", 12603),
        ("TAS", "do022", "601011001", 2401),
        ("NT", "do026", "701011001", 901),
        ("ACT", "do030", "801011001", 3502),
    ]
    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    for state, product, sa2_code, new_houses in states:
        xlsx_bytes = _make_state_xlsx(
            sa2_records=[
                (
                    sa2_code,
                    {
                        "new_houses_count": new_houses,
                        "new_other_residential_building_count": 1500,
                        "total_dwellings_count": new_houses + 1500,
                        "value_new_houses": 600_000,
                        "value_new_other_residential_building": 400_000,
                        "value_alterations_additions_conversions": 200_000,
                        "value_total_residential_building": 1_200_000,
                        "value_non_residential_building": 800_000,
                        "value_total_building": 2_000_000,
                    },
                ),
            ],
            aggregate_rows=[
                # State + a couple of parent-level aggregates the parser
                # must filter out.
                ("1", state),
                ("1GSYD" if state == "NSW" else "2GMEL", "GCC aggregate"),
                ("102", "SA4 aggregate"),
                ("10201", "SA3 aggregate"),
            ],
        )
        responses.add(
            responses.GET,
            f"{base}87310{product}_202603.xlsx",
            body=xlsx_bytes,
            status=200,
        )

    ds = AbsBaDataSource(root=ba_data_dir)
    df = ds.load()

    # All 8 SA2 codes present, no aggregates leaked.
    expected_sa2s = {sa2 for _, _, sa2, _ in states}
    assert set(df.index) == expected_sa2s
    assert df.index.name == "sa2_code_2021"
    # Aggregate codes filtered out
    for bogus in ("1", "1GSYD", "2GMEL", "102", "10201"):
        assert bogus not in df.index

    # Spot-check NSW values land in the right columns.
    assert df.loc["117011326", "new_houses_count"] == 22202
    assert df.loc["117011326", "total_dwellings_count"] == 22202 + 1500
    assert df.loc["117011326", "value_total_building"] == 2_000_000

    # All 9 metric columns + reference FY present
    expected_cols = {
        "new_houses_count",
        "new_other_residential_building_count",
        "total_dwellings_count",
        "value_new_houses",
        "value_new_other_residential_building",
        "value_alterations_additions_conversions",
        "value_total_residential_building",
        "value_non_residential_building",
        "value_total_building",
        "reference_financial_year",
    }
    assert expected_cols <= set(df.columns)
    assert (df["reference_financial_year"] == "2024-25").all()


@responses.activate
def test_load_filters_non_9digit_sa2_codes(ba_data_dir: Path) -> None:
    """Aggregate rows with state / GCC / SA4 / SA3 codes (non-9-digit)
    must be skipped. The parser's "len == 9 and isdigit" guard is the
    only thing keeping aggregates out of the SA2 join.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    # Build NSW with aggregates; the other 7 states are empty (no SA2 records).
    nsw_bytes = _make_state_xlsx(
        sa2_records=[
            ("117011326", {"new_houses_count": 100, "total_dwellings_count": 150}),
        ],
        aggregate_rows=[
            ("1", "New South Wales"),
            ("1GSYD", "Greater Sydney"),
            ("102", "Central Coast"),
            ("10201", "Gosford"),
        ],
    )
    responses.add(responses.GET, f"{base}87310do002_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do006", "do010", "do014", "do018", "do022", "do026", "do030"):
        empty_bytes = _make_state_xlsx(sa2_records=[], aggregate_rows=[("1", "State stub")])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaDataSource(root=ba_data_dir)
    df = ds.load()
    assert list(df.index) == ["117011326"]


@responses.activate
def test_load_handles_blank_and_dash_cells(ba_data_dir: Path) -> None:
    """ABS BA at SA2 level publishes raw counts with no suppression,
    but the coercer still handles incidental blank / dash cells as None.
    Defensive — mirrors the other ABS datasets' null handling.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    nsw_bytes = _make_state_xlsx(
        sa2_records=[
            (
                "117011326",
                {
                    "new_houses_count": 100,
                    "new_other_residential_building_count": "-",
                    "total_dwellings_count": 100,
                    "value_new_houses": "",
                    "value_total_building": 1_000_000,
                },
            ),
        ],
    )
    responses.add(responses.GET, f"{base}87310do002_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do006", "do010", "do014", "do018", "do022", "do026", "do030"):
        empty_bytes = _make_state_xlsx(sa2_records=[])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaDataSource(root=ba_data_dir)
    df = ds.load()
    assert df.loc["117011326", "new_houses_count"] == 100
    assert pd.isna(df.loc["117011326", "new_other_residential_building_count"])
    assert pd.isna(df.loc["117011326", "value_new_houses"])
    assert df.loc["117011326", "value_total_building"] == 1_000_000


@responses.activate
def test_load_raises_on_completely_empty_release(ba_data_dir: Path) -> None:
    """If every state cube parses to zero SA2s, raise loudly — that's
    almost certainly a publication problem upstream, not a tool problem.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    for product in ("do002", "do006", "do010", "do014", "do018", "do022", "do026", "do030"):
        empty_bytes = _make_state_xlsx(sa2_records=[])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaDataSource(root=ba_data_dir)
    with pytest.raises(RuntimeError, match="parsed to empty"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(ba_data_dir: Path) -> None:
    """Second ``load()`` reads from the parquet sidecar without re-hitting
    the network. Identical DataFrame, cheap.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    for product in ("do002", "do006", "do010", "do014", "do018", "do022", "do026", "do030"):
        records: list[tuple[str, dict[str, int | float | str]]] = (
            [("117011326", {"new_houses_count": 100, "total_dwellings_count": 100})]
            if product == "do002"
            else []
        )
        xlsx_bytes = _make_state_xlsx(sa2_records=records)
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=xlsx_bytes, status=200
        )

    ds = AbsBaDataSource(root=ba_data_dir)
    df1 = ds.load()
    # Mid-test: reset the source instance and re-load — should hit
    # parquet, not re-trigger any network calls.
    ds2 = AbsBaDataSource(root=ba_data_dir)
    # Need to satisfy the lazy release resolution; second instance does
    # one CKAN/landing GET then sees parquet exists. Add an extra
    # landing-page response for the second resolve.
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
