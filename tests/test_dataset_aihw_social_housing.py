"""Tests for the AIHW Social Housing dwellings fetcher (spec §20).

Synthetic XLSX fixtures mirror the live DWELLINGS.4 sheet probed
firsthand on 2026-06-10 (Real Data First): banner + title rows, a
header at row 4, bare 3-digit SA4 ``Region Code``s, the ``". ."``
suppression sentinel in the SOMIH column, and trailing footnote rows
(blank Region Code) that the parser must drop. The dataset is SA4-native
and downscaled to SA2 via an attached mapping.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_social_housing import (
    _AIHW_SH_URLS_BY_RELEASE,
    AihwSocialHousingDataSource,
)

_2023_URL = _AIHW_SH_URLS_BY_RELEASE["2023"]["url"]

# (state, region_code, region_name, public, somih, community, total)
_Row = tuple[str, str, str, object, object, object, object]


def _make_sh_xlsx(
    *,
    rows: list[_Row],
    sheet: str = "DWELLINGS.4",
    title: str = (
        "Table DWELLINGS.4: Dwellings, by Statistical level 4 (SA4) for "
        "public housing, SOMIH and community housing, 2023"
    ),
    include_footnotes: bool = True,
) -> bytes:
    """Build a synthetic DWELLINGS.4-shaped workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["Table of contents"])
    ws.append([title])
    ws.append([])
    ws.append(
        [
            "State/territory",
            "Region Code",
            "Region Name",
            "Public housing",
            "SOMIH(a)",
            "Community housing",
            "Total",
        ]
    )
    for r in rows:
        ws.append(list(r))
    if include_footnotes:
        ws.append([])
        ws.append(["(a) Victoria, Western Australia and the ACT do not have a SOMIH program."])
        ws.append(["Notes"])
        ws.append(["1. Data correspond to the 2021 ASGS edition of the SA4 structure."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def sh_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-sh-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(sh_data_dir: Path) -> None:
    ds = AihwSocialHousingDataSource(release="latest", root=sh_data_dir)
    assert ds.resolved_release == "2023"


def test_resolve_unknown_release_raises(sh_data_dir: Path) -> None:
    ds = AihwSocialHousingDataSource(release="2099", root=sh_data_dir)
    with pytest.raises(RuntimeError, match="not in the registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises(sh_data_dir: Path) -> None:
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_downscales_sa4_to_sa2(sh_data_dir: Path) -> None:
    """Downscale each SA4 to its SA2s; the ``". ."`` SOMIH sentinel ->
    null; footnote rows (blank code) excluded.
    """
    rows: list[_Row] = [
        ("NSW", "101", "Capital Region", 1980, 62, 1022, 3065),
        # ACT-style row: no SOMIH program -> ". ." sentinel.
        ("ACT", "801", "Australian Capital Territory", 10827, ". .", 1685, 12512),
    ]
    responses.add(responses.GET, _2023_URL, body=_make_sh_xlsx(rows=rows), status=200)

    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "101011002": "101", "801011001": "801"})
    df = ds.load()

    assert set(df.index) == {"101011001", "101011002", "801011001"}
    assert df.loc["101011001", "social_housing_public_count"] == 1980
    assert df.loc["101011002", "social_housing_public_count"] == 1980
    assert df.loc["101011001", "social_housing_somih_count"] == 62
    assert df.loc["101011001", "social_housing_total_count"] == 3065
    # ACT SOMIH is the ". ." sentinel -> null.
    assert pd.isna(df.loc["801011001", "social_housing_somih_count"])
    assert df.loc["801011001", "social_housing_public_count"] == 10827
    assert (df["reference_period"] == "2023").all()


@responses.activate
def test_load_missing_sa4_emits_null(sh_data_dir: Path) -> None:
    rows: list[_Row] = [("NSW", "101", "Capital Region", 1980, 62, 1022, 3065)]
    responses.add(responses.GET, _2023_URL, body=_make_sh_xlsx(rows=rows), status=200)
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "997999999": "997"})
    df = ds.load()
    assert df.loc["101011001", "social_housing_total_count"] == 3065
    assert pd.isna(df.loc["997999999", "social_housing_total_count"])


@responses.activate
def test_load_raises_on_wrong_year(sh_data_dir: Path) -> None:
    rows: list[_Row] = [("NSW", "101", "Capital Region", 1980, 62, 1022, 3065)]
    title = "Table DWELLINGS.4: Dwellings, by Statistical level 4 (SA4) ..., 2099"
    responses.add(responses.GET, _2023_URL, body=_make_sh_xlsx(rows=rows, title=title), status=200)
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="2023"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_missing_sheet(sh_data_dir: Path) -> None:
    rows: list[_Row] = [("NSW", "101", "Capital Region", 1980, 62, 1022, 3065)]
    body = _make_sh_xlsx(rows=rows, sheet="WRONG.4")
    responses.add(responses.GET, _2023_URL, body=body, status=200)
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="no 'DWELLINGS.4' sheet"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_shifted_header(sh_data_dir: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DWELLINGS.4"
    ws.append(["Table of contents"])
    ws.append(["Table DWELLINGS.4: ... Statistical level 4 (SA4) ..., 2023"])
    ws.append([])
    # 'Region Code' present but the value headers are wrong.
    ws.append(["State/territory", "Region Code", "Region Name", "Widgets", "X", "Y", "Z"])
    ws.append(["NSW", "101", "Capital Region", 1, 2, 3, 4])
    buf = io.BytesIO()
    wb.save(buf)
    responses.add(responses.GET, _2023_URL, body=buf.getvalue(), status=200)
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="value header"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(sh_data_dir: Path) -> None:
    rows: list[_Row] = [
        ("NSW", "101", "Capital Region", 1980, 62, 1022, 3065),
        ("NSW", "102", "Central Coast", 3619, 71, 1990, 5680),
    ]
    responses.add(responses.GET, _2023_URL, body=_make_sh_xlsx(rows=rows), status=200)
    mapping = {"101011001": "101", "102011028": "102"}
    ds = AihwSocialHousingDataSource(root=sh_data_dir)
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()
    ds2 = AihwSocialHousingDataSource(root=sh_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
