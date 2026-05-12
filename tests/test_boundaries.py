"""Tests for census_augment.data_sources.boundaries."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
import requests
import responses

from census_augment.config import CensusConfig
from census_augment.data_sources.boundaries import BoundariesDataSource

BASE_URL = "https://abs.test/boundaries"
EXPECTED_FILENAME = "SA2_2021_AUST_SHP_GDA2020.zip"
EXPECTED_URL = f"{BASE_URL}/{EXPECTED_FILENAME}"


def _make_data_source(tmp_path: Path, base_url: str = BASE_URL) -> BoundariesDataSource:
    return BoundariesDataSource(
        census=CensusConfig(),
        base_url=base_url,
        root=tmp_path / "data" / "boundaries",
    )


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
    assert ds.zip_path.parent == tmp_path / "data" / "boundaries"
    assert ds.extract_dir == tmp_path / "data" / "boundaries" / "SA2_2021_AUST_SHP_GDA2020"


# ---------- caching ----------


def test_not_cached_initially(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.is_cached() is False
    assert ds.shapefile_path is None


@responses.activate
def test_fetch_downloads_extracts_and_returns_shapefile(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    shp = ds.fetch()

    assert shp.exists()
    assert shp.suffix == ".shp"
    # Sidecar files should also have been extracted
    assert shp.with_suffix(".dbf").exists()
    assert shp.with_suffix(".prj").exists()
    assert shp.with_suffix(".shx").exists()
    assert ds.is_cached()
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_returns_cached_without_redownload(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    first = ds.fetch()
    second = ds.fetch()

    assert first == second
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_with_refresh_redownloads(tmp_path: Path, fake_boundary_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    ds.fetch()
    ds.fetch(refresh=True)

    assert len(responses.calls) == 2


@responses.activate
def test_refresh_replaces_old_extract_directory(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    ds.fetch()

    stale = ds.extract_dir / "stale_artefact.txt"
    stale.write_text("old", encoding="utf-8")
    assert stale.exists()

    ds.fetch(refresh=True)
    assert not stale.exists()


# ---------- loading ----------


@responses.activate
def test_load_returns_geodataframe_with_expected_shape(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    gdf = ds.load()

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 3
    assert "SA2_CODE21" in gdf.columns
    assert "SA2_NAME21" in gdf.columns
    assert gdf.crs is not None
    # Real ABS shapefiles: to_epsg() == 7844; fixture-written shapefiles lose
    # the EPSG identifier on round-trip but preserve the datum in `crs.name`.
    crs_epsg = gdf.crs.to_epsg()
    crs_name = (gdf.crs.name or "").upper()
    assert crs_epsg == 7844 or "GDA2020" in crs_name


# ---------- error paths ----------


@responses.activate
def test_download_404_raises_http_error(tmp_path: Path) -> None:
    responses.add(responses.GET, EXPECTED_URL, status=404)
    ds = _make_data_source(tmp_path)
    with pytest.raises(requests.HTTPError):
        ds.fetch()


@responses.activate
def test_zip_with_no_shapefile_raises(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile in here")
    bad_zip_bytes = buf.getvalue()

    responses.add(responses.GET, EXPECTED_URL, body=bad_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    with pytest.raises(RuntimeError, match=".shp"):
        ds.fetch()


# ---------- atomic-write hygiene ----------


@responses.activate
def test_no_tmp_file_remains_after_successful_fetch(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    ds.fetch()

    leftover = list(ds._root.glob("*.tmp"))
    assert leftover == []
