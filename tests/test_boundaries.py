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


# ---------- feather cache (issue #43) ----------


@responses.activate
def test_load_writes_feather_cache(tmp_path: Path, fake_boundary_zip_bytes: bytes) -> None:
    """First load writes a `<shp>.feather` sidecar next to the .shp."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    ds.load()

    shp = ds.shapefile_path
    assert shp is not None
    feather = shp.with_suffix(".feather")
    assert feather.exists(), "Feather sidecar should have been written"
    # Sanity: feather round-trips to a usable GeoDataFrame.
    cached = gpd.read_feather(feather)
    assert len(cached) == 3
    assert "SA2_CODE21" in cached.columns


@responses.activate
def test_load_uses_feather_cache_on_second_call(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    """Second load reads from the feather, not the .shp."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    first = ds.load()
    shp = ds.shapefile_path
    assert shp is not None

    # Corrupt the .shp. If the second load went back to the shapefile,
    # it would fail; using the feather cache keeps it working.
    shp.write_bytes(b"not a real shp")
    # Make sure the feather is still newer than the (just-touched) .shp.
    feather = shp.with_suffix(".feather")
    import os

    os.utime(feather, None)

    second = ds.load()
    assert len(second) == len(first)
    assert list(second.columns) == list(first.columns)


@responses.activate
def test_feather_cache_invalidated_when_shp_newer(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    """When the .shp is newer than the cache, we ignore the cache."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    ds.load()  # populates cache

    shp = ds.shapefile_path
    assert shp is not None
    feather = shp.with_suffix(".feather")

    # Backdate the feather so the .shp is "newer".
    import os

    old_mtime = shp.stat().st_mtime - 60
    os.utime(feather, (old_mtime, old_mtime))

    # Corrupt the cache content too — it should never be read.
    feather.write_bytes(b"corrupt")
    os.utime(feather, (old_mtime, old_mtime))

    gdf = ds.load()  # re-reads from .shp, doesn't raise
    assert len(gdf) == 3
    assert "SA2_CODE21" in gdf.columns


@responses.activate
def test_feather_cache_corrupt_falls_back_to_shp(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    """Garbage feather is silently ignored; we fall back to the .shp."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_boundary_zip_bytes, status=200)
    ds = _make_data_source(tmp_path)

    ds.load()  # populates cache
    shp = ds.shapefile_path
    assert shp is not None
    feather = shp.with_suffix(".feather")

    # Corrupt the feather but keep it newer than the .shp. The only
    # invalidation signal should be the read failure.
    feather.write_bytes(b"\x00not-real-feather-bytes")
    import os

    os.utime(feather, None)

    gdf = ds.load()  # falls back to read_file, doesn't raise
    assert len(gdf) == 3


# ---------- Edition 2 (2016) boundary support (Phase F.1) -----------------
#
# These tests exercise the Edition 2 code path: the URL is the ABS Lotus
# Notes openagent form (not base_url + filename), the filename is the
# `1270055001_sa2_2016_aust_shape.zip` form, and the DBF columns are
# ``SA2_MAIN16`` / ``SA2_NAME16``. The fixture lives in conftest as
# ``fake_boundary_zip_bytes_edition_2``.


EDITION_2_URL_PREFIX = "https://www.ausstats.abs.gov.au/ausstats/subscriber.nsf/log?openagent"
EDITION_2_FILENAME = "1270055001_sa2_2016_aust_shape.zip"


def _make_edition_2_data_source(tmp_path: Path) -> BoundariesDataSource:
    return BoundariesDataSource(
        census=CensusConfig(year=2016, asgs_edition=2, datum="GDA94"),
        base_url="https://unused.test/boundaries",  # Edition 2 ignores base_url
        root=tmp_path / "data" / "boundaries" / "2016",
    )


def test_edition_2_filename_and_url(tmp_path: Path) -> None:
    ds = _make_edition_2_data_source(tmp_path)
    assert ds.filename == EDITION_2_FILENAME
    assert ds.url.startswith(EDITION_2_URL_PREFIX)
    assert EDITION_2_FILENAME in ds.url


def test_edition_2_edition_property_reflects_spec(tmp_path: Path) -> None:
    ds = _make_edition_2_data_source(tmp_path)
    spec = ds.edition
    assert spec.edition == 2
    assert spec.year == 2016
    assert spec.datum == "GDA94"
    assert spec.sa2_code_column == "SA2_MAIN16"
    assert spec.sa2_name_column == "SA2_NAME16"


def test_edition_2_url_does_not_use_base_url(tmp_path: Path) -> None:
    """Edition 2's URL is fixed — base_url is ignored."""
    ds = _make_edition_2_data_source(tmp_path)
    assert "unused.test" not in ds.url


@responses.activate
def test_edition_2_fetch_extracts_and_returns_shapefile(
    tmp_path: Path, fake_boundary_zip_bytes_edition_2: bytes
) -> None:
    ds = _make_edition_2_data_source(tmp_path)
    responses.add(responses.GET, ds.url, body=fake_boundary_zip_bytes_edition_2, status=200)

    shp = ds.fetch()
    assert shp.exists()
    assert shp.suffix == ".shp"
    assert ds.is_cached()


@responses.activate
def test_edition_2_load_returns_geodataframe_with_e2_columns(
    tmp_path: Path, fake_boundary_zip_bytes_edition_2: bytes
) -> None:
    ds = _make_edition_2_data_source(tmp_path)
    responses.add(responses.GET, ds.url, body=fake_boundary_zip_bytes_edition_2, status=200)

    gdf = ds.load()

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 3
    # Edition 2 DBF columns — NOT Edition 3's SA2_CODE21/SA2_NAME21
    assert "SA2_MAIN16" in gdf.columns
    assert "SA2_NAME16" in gdf.columns
    assert "SA2_CODE21" not in gdf.columns
    # CRS should be GDA94 / EPSG:4283. Fixture-written shapefiles may
    # lose the EPSG identifier on round-trip but preserve the datum
    # in `crs.name`.
    crs_epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    crs_name = ((gdf.crs.name if gdf.crs is not None else "") or "").upper()
    assert crs_epsg == 4283 or "GDA94" in crs_name


def test_explicit_edition_spec_overrides_config(tmp_path: Path) -> None:
    """Passing ``edition_spec=`` explicitly takes priority over the config-
    derived spec. Useful for future temporal-mode multi-edition orchestrators."""
    from census_augment.data_sources._edition import edition_2_spec

    ds = BoundariesDataSource(
        census=CensusConfig(),  # year=2021 by default
        base_url="https://abs.test/boundaries",
        root=tmp_path / "data" / "override",
        edition_spec=edition_2_spec(),
    )
    # The injected Edition 2 spec wins despite the Edition 3 config.
    assert ds.edition.edition == 2
    assert ds.filename == EDITION_2_FILENAME
