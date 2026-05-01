"""Tests for census_augment.data_sources.datapacks."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests
import responses

from census_augment.config import CensusConfig
from census_augment.data_sources.datapacks import (
    ColumnMetadata,
    DataPackMetadata,
    DataPacksDataSource,
    extract_table_id,
)
from tests.conftest import build_metadata_xlsx

BASE_URL = "https://abs.test/datapacks"
EXPECTED_FILENAME = "2021_GCP_SA2_for_AUS_short-header.zip"
EXPECTED_URL = f"{BASE_URL}/{EXPECTED_FILENAME}"


def _make_data_source(
    tmp_path: Path,
    base_url: str = BASE_URL,
    *,
    descriptor: str = "short-header",
) -> DataPacksDataSource:
    census = CensusConfig(descriptor=descriptor)  # type: ignore[arg-type]
    return DataPacksDataSource(
        census=census,
        base_url=base_url,
        root=tmp_path / "data" / "census",
    )


def _build_zip_with_metadata(
    fake_g01_df: pd.DataFrame,
    metadata_xlsx_bytes: bytes,
    metadata_filename: str = "Metadata/Metadata_2021_GCP_DataPack_R1_R2.xlsx",
) -> bytes:
    """Helper for tests that need to swap in custom metadata."""
    csv_dir = "2021 Census GCP Statistical Area 2 for AUS"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{csv_dir}/2021Census_G01_AUST_SA2.csv",
            fake_g01_df.to_csv(index=False),
        )
        zf.writestr(metadata_filename, metadata_xlsx_bytes)
    return buf.getvalue()


# ---------- extract_table_id helper ----------


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("G01.csv", "G01"),
        ("G02.csv", "G02"),
        ("G09A.csv", "G09A"),
        ("G09B.csv", "G09B"),
        ("2021Census_G01_AUST_SA2.csv", "G01"),
        ("2021Census_G09A_AUST_SA2.csv", "G09A"),
        ("Metadata.xlsx", None),
        ("readme.txt", None),
        ("not_a_table.csv", None),
    ],
)
def test_extract_table_id(filename: str, expected: str | None) -> None:
    assert extract_table_id(filename) == expected


# ---------- filename / URL construction ----------


def test_filename_default_config(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.filename == EXPECTED_FILENAME


def test_url_construction(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.url == EXPECTED_URL


def test_url_strips_trailing_slash(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path, base_url=f"{BASE_URL}/")
    assert ds.url == EXPECTED_URL


def test_zip_and_extract_paths_are_under_root(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.zip_path.parent == tmp_path / "data" / "census"
    assert (
        ds.extract_dir
        == tmp_path / "data" / "census" / "2021_GCP_SA2_for_AUS_short-header"
    )


# ---------- caching ----------


def test_not_cached_initially(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.is_cached() is False


@responses.activate
def test_fetch_downloads_extracts_returns_extract_dir(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    extract_dir = ds.fetch()

    assert extract_dir == ds.extract_dir
    assert extract_dir.exists()
    assert ds.is_cached()
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_returns_cached_without_redownload(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch()

    assert len(responses.calls) == 1


@responses.activate
def test_fetch_with_refresh_redownloads(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch(refresh=True)

    assert len(responses.calls) == 2


# ---------- list_tables ----------


@responses.activate
def test_list_tables_returns_sorted_ids(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    assert ds.list_tables() == ["G01", "G02"]


# ---------- load_table ----------


@responses.activate
def test_load_table_returns_dataframe_indexed_by_sa2(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    df = ds.load_table("G01")

    assert df.index.name == "SA2_CODE_2021"
    assert "117011326" in df.index
    assert "Tot_P_P" in df.columns
    assert df.loc["117011326", "Tot_P_P"] == 10200


@responses.activate
def test_load_table_unknown_raises_keyerror(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    with pytest.raises(KeyError, match="G99"):
        ds.load_table("G99")


@responses.activate
def test_load_table_preserves_sa2_code_as_string(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    df = ds.load_table("G01")

    for code in df.index:
        assert isinstance(code, str)


# ---------- load_metadata: real ABS structure ----------


@responses.activate
def test_load_metadata_parses_full_structure(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert isinstance(metadata, DataPackMetadata)
    assert set(metadata.tables.keys()) == {"G01", "G02"}
    assert "Tot_P_M" in metadata.tables["G01"].columns
    assert "Median_rent_weekly" in metadata.tables["G02"].columns


@responses.activate
def test_metadata_describe_uses_columnheading(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """Description comes from Columnheadingdescriptioninprofile, not Long."""
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.describe("G01", "Tot_P_M") == "Males"
    assert (
        metadata.describe("G02", "Median_tot_hhd_inc_weekly")
        == "Median total household income ($/weekly)"
    )
    assert metadata.describe("G01", "missing") is None
    assert metadata.describe("G99", "Tot_P_M") is None


@responses.activate
def test_metadata_table_names_populated_from_table_sheet(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.tables["G01"].name == "Selected Person Characteristics by Sex"
    assert metadata.tables["G02"].name == "Selected Medians and Averages"


@responses.activate
def test_metadata_handles_missing_table_sheet_gracefully(
    tmp_path: Path,
    fake_g01_df: pd.DataFrame,
    fake_descriptor_rows: list[tuple[Any, ...]],
) -> None:
    """Without a Table Number sheet, table names are empty but the rest works."""
    xlsx = build_metadata_xlsx(fake_descriptor_rows, table_rows=None)
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.tables["G01"].name == ""
    assert "Tot_P_M" in metadata.tables["G01"].columns


@responses.activate
def test_metadata_has_table_and_column(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.has_table("G01") is True
    assert metadata.has_table("G99") is False
    assert metadata.has_column("G01", "Tot_P_M") is True
    assert metadata.has_column("G01", "missing") is False


@responses.activate
def test_metadata_all_columns_iterates(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    cols = list(metadata.all_columns())
    assert len(cols) == 6
    assert all(isinstance(c, ColumnMetadata) for c in cols)


# ---------- descriptor mode resolves the right code column ----------


@pytest.mark.parametrize(
    "descriptor,expected_code",
    [
        ("short-header", "Median_tot_hhd_inc_weekly"),
        ("long-header", "Median_total_household_income_weekly"),
        ("sequential", "G115"),
    ],
)
@responses.activate
def test_descriptor_mode_chooses_code_column(
    tmp_path: Path,
    fake_datapack_zip_bytes: bytes,
    descriptor: str,
    expected_code: str,
) -> None:
    # Descriptor mode changes the *download URL*, not just the metadata column.
    ds = _make_data_source(tmp_path, descriptor=descriptor)
    responses.add(responses.GET, ds.url, body=fake_datapack_zip_bytes, status=200)

    metadata = ds.load_metadata()

    g02 = metadata.tables["G02"]
    assert expected_code in g02.columns
    assert (
        metadata.describe("G02", expected_code)
        == "Median total household income ($/weekly)"
    )


# ---------- metadata file selection ----------


@responses.activate
def test_only_descriptor_xlsx_is_parsed_when_multiple_present(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """The fixture ZIP includes geog_desc + Sequential_Template noise xlsxes;
    only Metadata_*GCP*DataPack*.xlsx should be parsed.
    """
    responses.add(
        responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200
    )

    ds = _make_data_source(tmp_path)
    # If the parser picked one of the noise files instead, this would fail
    # to find the descriptor sheet or columns.
    metadata = ds.load_metadata()
    assert "Tot_P_M" in metadata.tables["G01"].columns


@responses.activate
def test_metadata_with_no_matching_xlsx_raises(
    tmp_path: Path, fake_g01_df: pd.DataFrame
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("G01.csv", fake_g01_df.to_csv(index=False))
        zf.writestr("Metadata/2021Census_geog_desc_unrelated.xlsx", b"")
    bytes_no_descriptor = buf.getvalue()

    responses.add(responses.GET, EXPECTED_URL, body=bytes_no_descriptor, status=200)
    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match="metadata"):
        ds.load_metadata()


# ---------- header auto-detect / title-row tolerance ----------


@responses.activate
def test_metadata_tolerates_extra_title_rows(
    tmp_path: Path,
    fake_g01_df: pd.DataFrame,
    fake_descriptor_rows: list[tuple[Any, ...]],
    fake_table_rows: list[tuple[Any, ...]],
) -> None:
    """Real ABS has 10 rows of title above the descriptor header. If they
    add more in a future release, the parser should still find the header."""
    xlsx = build_metadata_xlsx(
        fake_descriptor_rows, fake_table_rows, title_row_count=20
    )
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()
    assert metadata.has_column("G01", "Tot_P_M")


@responses.activate
def test_metadata_no_header_at_all_raises(
    tmp_path: Path, fake_g01_df: pd.DataFrame
) -> None:
    """A descriptor sheet that never reveals a header row is an error."""
    import openpyxl as _openpyxl

    wb = _openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cell Descriptors Information"
    for _ in range(10):
        ws.append(["random", "junk", "rows"])
    buf = io.BytesIO()
    wb.save(buf)
    zip_bytes = _build_zip_with_metadata(fake_g01_df, buf.getvalue())
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match="header"):
        ds.load_metadata()


@responses.activate
def test_metadata_wrong_descriptor_sheet_name_raises(
    tmp_path: Path,
    fake_g01_df: pd.DataFrame,
    fake_descriptor_rows: list[tuple[Any, ...]],
) -> None:
    xlsx = build_metadata_xlsx(
        fake_descriptor_rows,
        table_rows=None,
        descriptor_sheet_name="WrongSheet",
    )
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match="descriptor sheet"):
        ds.load_metadata()


@responses.activate
def test_metadata_missing_required_column_raises(
    tmp_path: Path,
    fake_g01_df: pd.DataFrame,
    fake_descriptor_rows: list[tuple[Any, ...]],
) -> None:
    """If Columnheadingdescriptioninprofile is missing, parser fails loudly."""
    xlsx = build_metadata_xlsx(
        fake_descriptor_rows,
        table_rows=None,
        descriptor_columns=[
            "Sequential", "Short", "Long", "DataPackfile", "Profiletable",
            # Note: missing Columnheadingdescriptioninprofile
        ],
    )
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match="Columnheadingdescriptioninprofile"):
        ds.load_metadata()


# ---------- error paths ----------


@responses.activate
def test_download_404_raises_http_error(tmp_path: Path) -> None:
    responses.add(responses.GET, EXPECTED_URL, status=404)
    ds = _make_data_source(tmp_path)
    with pytest.raises(requests.HTTPError):
        ds.fetch()


@responses.activate
def test_zip_with_no_csvs_raises(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no tables in here")
    bad_zip_bytes = buf.getvalue()

    responses.add(responses.GET, EXPECTED_URL, body=bad_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match="No table CSVs"):
        ds.fetch()
