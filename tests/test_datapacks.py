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
    census = CensusConfig(descriptor=descriptor)
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
    assert ds.extract_dir == tmp_path / "data" / "census" / "2021_GCP_SA2_for_AUS_short-header"


# ---------- caching ----------


def test_not_cached_initially(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.is_cached() is False


@responses.activate
def test_fetch_downloads_extracts_returns_extract_dir(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

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
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch()

    assert len(responses.calls) == 1


@responses.activate
def test_fetch_with_refresh_redownloads(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch(refresh=True)

    assert len(responses.calls) == 2


# ---------- list_tables ----------


@responses.activate
def test_list_tables_returns_sorted_ids(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    assert ds.list_tables() == ["G01", "G02"]


# ---------- load_table ----------


@responses.activate
def test_load_table_returns_dataframe_indexed_by_sa2(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    df = ds.load_table("G01")

    assert df.index.name == "SA2_CODE_2021"
    assert "117011326" in df.index
    assert "Tot_P_P" in df.columns
    assert df.loc["117011326", "Tot_P_P"] == 10200


@pytest.mark.parametrize(
    "sa2_column_name",
    [
        "SA2_CODE_2021",  # 2021 CSV header (existing)
        "SA2_CODE21",  # 2021 boundary-file style header
        "SA2_MAINCODE_2021",  # 2021 alternative (some releases)
        "SA2_CODE_2016",  # F.4: 2016 CSV header style 1
        "SA2_CODE16",  # F.4: 2016 boundary-style header
        "SA2_MAINCODE_2016",  # F.4: 2016 actual CSV header
    ],
)
def test_detect_sa2_column_recognises_each_release_variant(
    sa2_column_name: str,
) -> None:
    """F.4: the SA2-code candidate list must accept 2016 and 2021 forms.

    ABS uses ``SA2_MAINCODE_2016`` in the 2016 GCP DataPack CSVs and
    ``SA2_MAINCODE_2021`` / ``SA2_CODE_2021`` in 2021. Without the F.4
    candidate-list extension, 2016 CSVs error out at load with
    "No SA2 code column found".

    This is a static-method check rather than an end-to-end ZIP test
    because the detection logic is independent of the ZIP / metadata
    pipeline above.
    """
    df = pd.DataFrame(
        {
            sa2_column_name: ["101021007", "101021008"],
            "Median_tot_hhd_inc_weekly": [1083, 1692],
        }
    )
    detected = DataPacksDataSource._detect_sa2_column(df)
    assert detected == sa2_column_name


@responses.activate
def test_load_table_unknown_raises_keyerror(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    with pytest.raises(KeyError, match="G99"):
        ds.load_table("G99")


@responses.activate
def test_load_table_preserves_sa2_code_as_string(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    df = ds.load_table("G01")

    for code in df.index:
        assert isinstance(code, str)


# ---------- load_metadata: real ABS structure ----------


@responses.activate
def test_load_metadata_parses_full_structure(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

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
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

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
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.tables["G01"].name == "Selected Person Characteristics by Sex"
    assert metadata.tables["G02"].name == "Selected Medians and Averages"


# ---------- F.4: 2016 metadata-sheet sentence-case variants ----------
#
# The 2016 GCP DataPack metadata XLSX uses sentence case for both sheet
# names ("Cell descriptors information" / "Table number, name,
# population") where 2021 uses Title Case. The candidate-list extension
# in F.4 makes the parser tolerate both shapes against one synthetic
# fixture per variant. Live verification of the actual 2016 ZIP lives
# in tools/verify_real_parsers.py (the F.4 _list_tables_2016 / _parse_metadata_2016
# probes there) — these tests lock down the candidate-list semantics so
# the live probe is the only thing that ever needs to flap on schema
# drift.


@responses.activate
def test_metadata_handles_2016_sentence_case_sheet_names(
    tmp_path: Path,
    fake_g01_df: pd.DataFrame,
    fake_descriptor_rows: list[tuple[Any, ...]],
    fake_table_rows: list[tuple[Any, ...]],
) -> None:
    """2016 XLSX uses sentence case for both sheet names.

    The descriptor sheet name ("Cell descriptors information") was
    already in the candidate list for the v1.5 work; the table sheet
    name ("Table number, name, population") is the F.4 addition. Both
    must resolve cleanly through the same parser as 2021.
    """
    xlsx = build_metadata_xlsx(
        fake_descriptor_rows,
        fake_table_rows,
        descriptor_sheet_name="Cell descriptors information",
        table_sheet_name="Table number, name, population",
    )
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    # Descriptor sheet resolved → columns parsed.
    assert "Tot_P_M" in metadata.tables["G01"].columns
    # Table sheet resolved → human-readable table names attached.
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
def test_metadata_has_table_and_column(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()

    assert metadata.has_table("G01") is True
    assert metadata.has_table("G99") is False
    assert metadata.has_column("G01", "Tot_P_M") is True
    assert metadata.has_column("G01", "missing") is False


@responses.activate
def test_metadata_all_columns_iterates(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

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
    assert metadata.describe("G02", expected_code) == "Median total household income ($/weekly)"


# ---------- metadata file selection ----------


@responses.activate
def test_only_descriptor_xlsx_is_parsed_when_multiple_present(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """The fixture ZIP includes geog_desc + Sequential_Template noise xlsxes;
    only Metadata_*GCP*DataPack*.xlsx should be parsed.
    """
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    # If the parser picked one of the noise files instead, this would fail
    # to find the descriptor sheet or columns.
    metadata = ds.load_metadata()
    assert "Tot_P_M" in metadata.tables["G01"].columns


@responses.activate
def test_metadata_with_no_matching_xlsx_raises(tmp_path: Path, fake_g01_df: pd.DataFrame) -> None:
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
    xlsx = build_metadata_xlsx(fake_descriptor_rows, fake_table_rows, title_row_count=20)
    zip_bytes = _build_zip_with_metadata(fake_g01_df, xlsx)
    responses.add(responses.GET, EXPECTED_URL, body=zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    metadata = ds.load_metadata()
    assert metadata.has_column("G01", "Tot_P_M")


@responses.activate
def test_metadata_no_header_at_all_raises(tmp_path: Path, fake_g01_df: pd.DataFrame) -> None:
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
            "Sequential",
            "Short",
            "Long",
            "DataPackfile",
            "Profiletable",
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


# ---------- metadata cache (issue #43) ----------


@responses.activate
def test_load_metadata_writes_pickle_cache(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    """First load_metadata writes a `<xlsx>.<mode>.parsed.pkl` sidecar."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    metadata = ds.load_metadata()

    xlsx = ds._metadata_xlsx()
    assert xlsx is not None
    cache_path = xlsx.with_name(xlsx.name + ".short-header.parsed.pkl")
    assert cache_path.exists(), "Cache sidecar should have been written"
    # Cache should round-trip to the same DataPackMetadata.
    import pickle

    with cache_path.open("rb") as fh:
        cached = pickle.load(fh)
    assert isinstance(cached, DataPackMetadata)
    assert set(cached.tables.keys()) == set(metadata.tables.keys())


@responses.activate
def test_load_metadata_uses_cache_on_second_call(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """Second load_metadata reads from the pickle, not the xlsx."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    first = ds.load_metadata()

    # Sentinel: replace the xlsx with garbage. If the cache is actually
    # used, the second call still succeeds. If we fell through to the
    # xlsx parser, this would raise.
    xlsx = ds._metadata_xlsx()
    assert xlsx is not None
    xlsx.write_bytes(b"not a real xlsx")
    # Restore the cache's mtime so it stays newer than the (just-touched)
    # xlsx — emulates the natural case where the cache was written after
    # the xlsx was extracted.
    cache_path = xlsx.with_name(xlsx.name + ".short-header.parsed.pkl")
    import os

    os.utime(cache_path, None)  # bump to now, which is >= xlsx's mtime

    second = ds.load_metadata()
    assert set(second.tables.keys()) == set(first.tables.keys())


@responses.activate
def test_metadata_cache_invalidated_when_xlsx_newer(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """If the xlsx mtime is newer than the cache, the cache is ignored."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    ds.load_metadata()  # populates cache

    xlsx = ds._metadata_xlsx()
    assert xlsx is not None
    cache_path = xlsx.with_name(xlsx.name + ".short-header.parsed.pkl")

    # Backdate the cache so the xlsx is "newer".
    import os

    old_mtime = xlsx.stat().st_mtime - 60
    os.utime(cache_path, (old_mtime, old_mtime))

    # Corrupt the cache file content too — it should never be read.
    cache_path.write_bytes(b"corrupt")

    # Should not raise; re-parses from xlsx.
    metadata = ds.load_metadata()
    assert "G01" in metadata.tables


@responses.activate
def test_metadata_cache_corrupt_pickle_falls_back_to_xlsx(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """A garbage pickle is silently ignored and the xlsx is re-parsed."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    ds.load_metadata()  # populates cache
    xlsx = ds._metadata_xlsx()
    assert xlsx is not None
    cache_path = xlsx.with_name(xlsx.name + ".short-header.parsed.pkl")

    # Corrupt the cache but keep it newer than the xlsx — the only
    # signal that should invalidate it is the unpickle failure.
    cache_path.write_bytes(b"\x80\x04not-real-pickle-bytes")

    metadata = ds.load_metadata()
    assert "G01" in metadata.tables  # re-parsed from xlsx, didn't raise


@responses.activate
def test_metadata_cache_keyed_by_descriptor_mode(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """Switching descriptor mode uses a separate cache file."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)

    short_ds = _make_data_source(tmp_path, descriptor="short-header")
    short_ds.load_metadata()

    xlsx = short_ds._metadata_xlsx()
    assert xlsx is not None
    short_cache = xlsx.with_name(xlsx.name + ".short-header.parsed.pkl")
    long_cache = xlsx.with_name(xlsx.name + ".long-header.parsed.pkl")
    assert short_cache.exists()
    assert not long_cache.exists(), "long-header cache shouldn't exist yet"
