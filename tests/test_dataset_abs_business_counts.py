"""Tests for the ABS Counts of Australian Businesses fetcher (spec §20).

Synthetic XLSX fixtures mirror the live DC8 cube probed firsthand on
2026-06-10 (Real Data First): a 2-row header band over long-format
industry-division × SA2 rows, a trailing national "Total All Industries"
row with a blank SA2 code, and footnote rows — all of which the parser
must handle. The signature behaviour: sum the industry-division rows per
SA2 (no per-SA2 total row exists), read the per-SA2 total from the source
Total column, and drop the national-total + footnote rows via a strict
9-digit SA2-code filter.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._abs_cab import (
    _ABS_CAB_RELEASES,
    AbsBusinessCountsDataSource,
)

_2025_URL = _ABS_CAB_RELEASES["2025"]["url"]

# (industry_code, industry_label, sa2_code, sa2_label,
#  non_employing, 1-4, 5-19, 20-199, 200+, total)
_Row = tuple[str, str, str, str, int, int, int, int, int, int]


def _make_cab_xlsx(
    *,
    rows: list[_Row],
    sheet: str = "Table 1",
    title: str = (
        "Businesses by Industry Division by Statistical Area Level 2 by "
        "Annualised Employment Size Ranges, June 2025 (a) (b)"
    ),
    include_national_total: bool = True,
    include_footnotes: bool = True,
) -> bytes:
    """Build a synthetic DC8-shaped workbook mirroring the real layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["            Australian Bureau of Statistics"])
    ws.append(["8165.0 Counts of Australian Businesses, ... June 2021 to June 2025"])
    ws.append(["Released at 11.30 am (Canberra time) 16 December 2025"])
    ws.append([title])
    ws.append(
        [
            "Industry",
            "Industry",
            "SA2",
            "SA2",
            "Non employing",
            "1-4 Employees",
            "5-19 Employees",
            "20-199 Employees",
            "200+ Employees",
            "Total",
        ]
    )
    ws.append(["Code", "Label", "Code", "Label", "no.", "no.", "no.", "no.", "no.", "no."])
    for r in rows:
        ws.append(list(r))
    if include_national_total:
        # National total: blank SA2 code -> must be excluded by the parser.
        ws.append([None, "Total All Industries", None, None, 100, 50, 20, 5, 1, 176])
    if include_footnotes:
        ws.append(["(a) Multi location businesses are only classified to a single geography ..."])
        ws.append(["(b) All values in this table may subject to perturbation ..."])
        ws.append(["© Commonwealth of Australia "])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def cab_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "abs-cab-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(cab_data_dir: Path) -> None:
    ds = AbsBusinessCountsDataSource(release="latest", root=cab_data_dir)
    assert ds.resolved_release == "2025"


def test_resolve_unknown_release_raises(cab_data_dir: Path) -> None:
    ds = AbsBusinessCountsDataSource(release="2099", root=cab_data_dir)
    with pytest.raises(RuntimeError, match="not in the registry"):
        _ = ds.resolved_release


# ---- end-to-end load -----------------------------------------------------


@responses.activate
def test_load_sums_industry_rows_per_sa2(cab_data_dir: Path) -> None:
    """The signature behaviour: sum the industry-division rows per SA2,
    exclude the national-total + footnote rows, stamp the reference year.
    """
    rows: list[_Row] = [
        # SA2 101021007: two industry rows summing to non=13, total=21.
        ("A", "Agriculture, Forestry and Fishing", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18),
        ("B", "Mining", "101021007", "Braidwood", 3, 0, 0, 0, 0, 3),
        # SA2 206011117: single industry row.
        ("G", "Retail Trade", "206011117", "Brunswick", 40, 20, 8, 2, 1, 71),
    ]
    responses.add(responses.GET, _2025_URL, body=_make_cab_xlsx(rows=rows), status=200)

    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    df = ds.load()

    assert set(df.index) == {"101021007", "206011117"}
    assert df.loc["101021007", "business_count_non_employing"] == 13
    assert df.loc["101021007", "business_count_1_4_employees"] == 5
    assert df.loc["101021007", "business_count_total"] == 21
    assert df.loc["206011117", "business_count_total"] == 71
    assert df.loc["206011117", "business_count_200_plus_employees"] == 1
    assert (df["reference_period"] == "2025").all()


@responses.activate
def test_load_total_comes_from_total_column_not_band_sum(cab_data_dir: Path) -> None:
    """ABS perturbs cells, so the published Total need not equal the sum
    of the size bands. The parser must surface the source Total column,
    not a recomputed band-sum.
    """
    # Bands sum to 18, but the (perturbed) Total column says 20.
    rows: list[_Row] = [
        ("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 20),
    ]
    responses.add(responses.GET, _2025_URL, body=_make_cab_xlsx(rows=rows), status=200)
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    df = ds.load()
    assert df.loc["101021007", "business_count_total"] == 20  # from the Total column
    assert df.loc["101021007", "business_count_non_employing"] == 10


@responses.activate
def test_load_excludes_national_total_and_footnotes(cab_data_dir: Path) -> None:
    rows: list[_Row] = [
        ("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18),
    ]
    responses.add(
        responses.GET,
        _2025_URL,
        body=_make_cab_xlsx(rows=rows, include_national_total=True, include_footnotes=True),
        status=200,
    )
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    df = ds.load()
    # Only the one real SA2 — the blank-SA2 national total + footnotes dropped.
    assert list(df.index) == ["101021007"]


@responses.activate
def test_load_raises_on_wrong_year_marker(cab_data_dir: Path) -> None:
    # Sheet title names 2099, not the 2025 the release maps to -> loud.
    # The title must keep its "Statistical Area Level 2" signature so the
    # drift guard locates the title row (vs the workbook subtitle).
    rows: list[_Row] = [("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18)]
    body = _make_cab_xlsx(
        rows=rows,
        title=(
            "Businesses by Industry Division by Statistical Area Level 2 by "
            "Annualised Employment Size Ranges, June 2099 (a) (b)"
        ),
    )
    responses.add(responses.GET, _2025_URL, body=body, status=200)
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    with pytest.raises(RuntimeError, match="June 2025"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_missing_sheet(cab_data_dir: Path) -> None:
    rows: list[_Row] = [("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18)]
    # Workbook's only sheet is misnamed, so the resolved 'Table 1' is absent.
    body = _make_cab_xlsx(rows=rows, sheet="Wrong Sheet")
    responses.add(responses.GET, _2025_URL, body=body, status=200)
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    with pytest.raises(RuntimeError, match="no 'Table 1' sheet"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_shifted_size_header(cab_data_dir: Path) -> None:
    # Build a workbook whose size-band header band is mangled.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Table 1"
    ws.append(["ABS"])
    ws.append(["8165.0"])
    ws.append(["Released"])
    ws.append(
        [
            "Businesses by Industry Division by Statistical Area Level 2 by "
            "Annualised Employment Size Ranges, June 2025 (a) (b)"
        ]
    )
    # 'SA2' header present but value columns are wrong (no 'Non employing').
    ws.append(["Industry", "Industry", "SA2", "SA2", "Widgets", "Gadgets", "X", "Y", "Z", "Total"])
    ws.append(["Code", "Label", "Code", "Label", "no.", "no.", "no.", "no.", "no.", "no."])
    ws.append(["A", "Agriculture", "101021007", "Braidwood", 1, 2, 3, 4, 5, 15])
    buf = io.BytesIO()
    wb.save(buf)
    responses.add(responses.GET, _2025_URL, body=buf.getvalue(), status=200)
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    with pytest.raises(RuntimeError, match="header row"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(cab_data_dir: Path) -> None:
    rows: list[_Row] = [
        ("A", "Agriculture", "101021007", "Braidwood", 10, 5, 2, 1, 0, 18),
        ("G", "Retail Trade", "101021007", "Braidwood", 7, 3, 1, 0, 0, 11),
    ]
    responses.add(responses.GET, _2025_URL, body=_make_cab_xlsx(rows=rows), status=200)
    ds = AbsBusinessCountsDataSource(root=cab_data_dir)
    df1 = ds.load()
    ds2 = AbsBusinessCountsDataSource(root=cab_data_dir)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
