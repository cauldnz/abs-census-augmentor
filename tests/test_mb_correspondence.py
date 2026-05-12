"""Tests for census_augment.mb_correspondence (Phase 5).

Covers the §15.1 resolution: MB→SA2 lookup is built from the .dbf
attribute table of the Mesh Block shapefile (``MB_2021_AUST_SHP_GDA2020.zip``),
not a standalone correspondence file.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import requests
import responses

from census_augment.data_sources.mb_correspondence import (
    MbCorrespondenceDataSource,
    MbInfo,
)

BASE_URL = "https://abs.test/boundaries"
EXPECTED_FILENAME = "MB_2021_AUST_SHP_GDA2020.zip"
EXPECTED_URL = f"{BASE_URL}/{EXPECTED_FILENAME}"


def _make_data_source(
    tmp_path: Path,
    *,
    base_url: str = BASE_URL,
    year: int = 2021,
    datum: str = "GDA2020",
) -> MbCorrespondenceDataSource:
    return MbCorrespondenceDataSource(
        year=year,
        datum=datum,
        base_url=base_url,
        root=tmp_path / "data" / "mb",
    )


# ---------- constructor validation ----------


def test_invalid_datum_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="datum"):
        MbCorrespondenceDataSource(
            datum="WGS84",
            base_url=BASE_URL,
            root=tmp_path,
        )


# ---------- filename / URL construction ----------


def test_filename_default(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.filename == EXPECTED_FILENAME


def test_filename_other_year(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path, year=2026)
    assert ds.filename == "MB_2026_AUST_SHP_GDA2020.zip"


def test_filename_other_datum(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path, datum="GDA94")
    assert ds.filename == "MB_2021_AUST_SHP_GDA94.zip"


def test_url_construction(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.url == EXPECTED_URL


# ---------- caching ----------


def test_not_cached_initially(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.is_cached() is False
    assert ds.shapefile_path is None


# ---------- fetch ----------


@responses.activate
def test_fetch_downloads_and_extracts(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    shp = ds.fetch()

    assert shp.exists()
    assert shp.suffix == ".shp"
    assert shp.with_suffix(".dbf").exists()
    assert ds.is_cached()


@responses.activate
def test_fetch_returns_cached_without_redownload(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    first = ds.fetch()
    second = ds.fetch()

    assert first == second
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_with_refresh_redownloads(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch(refresh=True)
    assert len(responses.calls) == 2


@responses.activate
def test_fetch_404_raises_http_error(tmp_path: Path) -> None:
    responses.add(responses.GET, EXPECTED_URL, status=404)
    ds = _make_data_source(tmp_path)
    with pytest.raises(requests.HTTPError):
        ds.fetch()


@responses.activate
def test_zip_with_no_shapefile_raises(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile in here")

    responses.add(responses.GET, EXPECTED_URL, body=buf.getvalue(), status=200)
    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match=".shp"):
        ds.fetch()


# ---------- load_correspondence: the main thing ----------


@responses.activate
def test_load_correspondence_returns_mbinfo_dict(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    lookup = ds.load_correspondence()

    assert isinstance(lookup, dict)
    assert len(lookup) == 5
    assert "11701132601" in lookup


@responses.activate
def test_load_correspondence_values_are_mbinfo(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    lookup = ds.load_correspondence()

    info = lookup["11701132601"]
    assert isinstance(info, MbInfo)
    assert info.mb_code == "11701132601"
    assert info.sa2_code == "117011326"
    assert info.sa2_name == "Sydney CBD"


@responses.activate
def test_load_correspondence_multiple_mbs_share_sa2(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    """Three MBs all map to the same Sydney CBD SA2 — verify each gets
    its own entry pointing at the shared SA2."""
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    lookup = ds.load_correspondence()

    for mb in ("11701132601", "11701132602", "11701132603"):
        assert lookup[mb].sa2_code == "117011326"
        assert lookup[mb].sa2_name == "Sydney CBD"


@responses.activate
def test_load_correspondence_distinct_sa2s(
    tmp_path: Path, fake_mb_correspondence_zip_bytes: bytes
) -> None:
    """Three SA2s appear across the 5 MBs in the fixture."""
    responses.add(
        responses.GET,
        EXPECTED_URL,
        body=fake_mb_correspondence_zip_bytes,
        status=200,
    )

    ds = _make_data_source(tmp_path)
    lookup = ds.load_correspondence()

    sa2_codes = {info.sa2_code for info in lookup.values()}
    assert sa2_codes == {"117011326", "117011327", "117011328"}


# ---------- column detection / extensibility ----------


def test_detect_column_picks_highest_year_shapefile_form() -> None:
    """When 2021 and 2026 columns coexist (shapefile form), 2026 wins (spec §13)."""
    df = pd.DataFrame(
        {
            "MB_CODE21": ["a"],
            "MB_CODE26": ["b"],
            "SA2_CODE21": ["c"],
            "SA2_CODE26": ["d"],
            "SA2_NAME21": ["e"],
            "SA2_NAME26": ["f"],
        }
    )
    lookup = MbCorrespondenceDataSource._build_lookup(df, source="<test>")
    # MB code "b" came from the 2026 column; that's what should appear.
    assert "b" in lookup
    assert "a" not in lookup
    assert lookup["b"].sa2_code == "d"
    assert lookup["b"].sa2_name == "f"


def test_detect_column_picks_highest_year_csv_form() -> None:
    """Same year-precedence rule for the CSV form (``_2021`` / ``_2026`` suffixes)."""
    df = pd.DataFrame(
        {
            "MB_CODE_2021": ["a"],
            "MB_CODE_2026": ["b"],
            "SA2_MAINCODE_2021": ["c"],
            "SA2_MAINCODE_2026": ["d"],
            "SA2_NAME_2021": ["e"],
            "SA2_NAME_2026": ["f"],
        }
    )
    lookup = MbCorrespondenceDataSource._build_lookup(df, source="<test>")
    assert "b" in lookup
    assert "a" not in lookup
    assert lookup["b"].sa2_code == "d"
    assert lookup["b"].sa2_name == "f"


def test_detect_column_accepts_sa2_maincode_alias() -> None:
    """``SA2_MAINCODE_2021`` (CSV form) is accepted as an alias for
    ``SA2_CODE_2021`` (ABS has been inconsistent across vintages)."""
    df = pd.DataFrame(
        {
            "MB_CODE_2021": ["a"],
            "SA2_MAINCODE_2021": ["x"],  # the alias form
            "SA2_NAME_2021": ["y"],
        }
    )
    lookup = MbCorrespondenceDataSource._build_lookup(df, source="<test>")
    assert lookup["a"].sa2_code == "x"
    assert lookup["a"].sa2_name == "y"


def test_detect_column_missing_pattern_raises() -> None:
    """If a required column is missing, we raise loudly (spec §convention:
    'errors should be loud and helpful')."""
    df = pd.DataFrame(
        {
            "MB_CODE21": ["a"],
            # no SA2_*_CODE column
            "SA2_NAME21": ["y"],
        }
    )
    with pytest.raises(RuntimeError, match="SA2"):
        MbCorrespondenceDataSource._build_lookup(df, source="<test>")
