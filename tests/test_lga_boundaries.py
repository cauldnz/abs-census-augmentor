"""Tests for census_augment.data_sources.lga_boundaries."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
import responses
from shapely.geometry import Polygon

from census_augment.data_sources.lga_boundaries import (
    DEFAULT_LGA_BASE_URL,
    KNOWN_LGA_YEARS,
    LgaBoundariesDataSource,
)

BASE_URL = "https://abs.test/lga"
EXPECTED_FILENAME_2025 = "LGA_2025_AUST_GDA2020.zip"
EXPECTED_URL_2025 = f"{BASE_URL}/{EXPECTED_FILENAME_2025}"


def _make_data_source(
    tmp_path: Path,
    *,
    base_url: str = BASE_URL,
    year: int | str = "latest",
) -> LgaBoundariesDataSource:
    return LgaBoundariesDataSource(
        root=tmp_path / "data" / "lga",
        base_url=base_url,
        year=year,
    )


@pytest.fixture
def fake_lga_gdf() -> gpd.GeoDataFrame:
    """Three-polygon synthetic LGA GeoDataFrame matching the real
    ABS schema (LGA_CODE25, LGA_NAME25, STE_CODE21, STE_NAME21,
    AREASQKM, geometry) in EPSG:7844 (GDA2020).
    """
    return gpd.GeoDataFrame(
        {
            "LGA_CODE25": ["10500", "10580", "11500"],
            "LGA_NAME25": ["Albury", "Armidale", "Bega Valley"],
            "STE_CODE21": ["1", "1", "1"],
            "STE_NAME21": ["New South Wales"] * 3,
            "AREASQKM": [305.6386, 7809.4406, 6279.4],
        },
        geometry=[
            Polygon([(146.9, -36.1), (147.0, -36.1), (147.0, -36.0), (146.9, -36.0)]),
            Polygon([(151.6, -30.5), (151.8, -30.5), (151.8, -30.3), (151.6, -30.3)]),
            Polygon([(149.7, -36.7), (150.1, -36.7), (150.1, -36.3), (149.7, -36.3)]),
        ],
        crs="EPSG:7844",
    )


@pytest.fixture
def fake_lga_zip_bytes(tmp_path: Path, fake_lga_gdf: gpd.GeoDataFrame) -> bytes:
    """In-memory ZIP carrying a synthetic LGA_2025 shapefile + sidecars.

    Mirrors the real ABS layout: bare ``LGA_2025_AUST_GDA2020.shp``
    (no ``SHP`` infix; that only appears on the SA2 ZIP).
    """
    work_dir = tmp_path / "_fixture_lga"
    work_dir.mkdir(parents=True, exist_ok=True)
    shp_path = work_dir / "LGA_2025_AUST_GDA2020.shp"
    fake_lga_gdf.to_file(shp_path, driver="ESRI Shapefile")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for sidecar in work_dir.iterdir():
            zf.write(sidecar, arcname=sidecar.name)
    return buf.getvalue()


# ---------- filename / URL construction ----------


def test_filename_default_year(tmp_path: Path) -> None:
    """``year="latest"`` resolves to the most recent known release."""
    ds = _make_data_source(tmp_path)
    assert ds.filename == EXPECTED_FILENAME_2025
    assert ds.year == max(KNOWN_LGA_YEARS)


def test_filename_specific_year(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path, year=2023)
    assert ds.filename == "LGA_2023_AUST_GDA2020.zip"
    assert ds.year == 2023


def test_filename_has_no_shp_infix(tmp_path: Path) -> None:
    """Real-data lesson: the LGA filename pattern does NOT include
    ``_SHP_`` (unlike SA2's ``SA2_2021_AUST_SHP_GDA2020.zip``). Probe
    this in tests to catch regression.
    """
    ds = _make_data_source(tmp_path)
    assert "_SHP_" not in ds.filename, (
        "LGA filename must not include _SHP_ infix; this differs from the "
        "SA2 boundary pattern and is the kind of guess CLAUDE.md's Real "
        "Data First rule exists to prevent."
    )


def test_url_construction(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.url == EXPECTED_URL_2025


def test_unknown_year_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not in known set"):
        _make_data_source(tmp_path, year=1999)


def test_non_integer_year_string_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'latest' or an integer"):
        _make_data_source(tmp_path, year="not-a-year")


def test_code_and_name_column_track_year(tmp_path: Path) -> None:
    """DBF columns use a two-digit year suffix (LGA_CODE25 for 2025,
    LGA_CODE23 for 2023). Don't confuse with SA2's no-year version.
    """
    ds_2025 = _make_data_source(tmp_path, year=2025)
    assert ds_2025.code_column == "LGA_CODE25"
    assert ds_2025.name_column == "LGA_NAME25"

    ds_2023 = _make_data_source(tmp_path, year=2023)
    assert ds_2023.code_column == "LGA_CODE23"
    assert ds_2023.name_column == "LGA_NAME23"


def test_default_base_url_matches_abs_constant(tmp_path: Path) -> None:
    """Sanity: the default base URL string is the one we live-probed
    (canonical ASGS Edition 3 path)."""
    ds = LgaBoundariesDataSource(root=tmp_path / "lga")
    assert ds.url.startswith(DEFAULT_LGA_BASE_URL)


# ---------- caching + fetch ----------


def test_not_cached_initially(tmp_path: Path) -> None:
    ds = _make_data_source(tmp_path)
    assert ds.is_cached() is False
    assert ds.shapefile_path is None


@responses.activate
def test_fetch_downloads_extracts_and_returns_shapefile(
    tmp_path: Path, fake_lga_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL_2025, body=fake_lga_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    shp = ds.fetch()

    assert shp.exists()
    assert shp.suffix == ".shp"
    assert shp.with_suffix(".dbf").exists()
    assert shp.with_suffix(".prj").exists()
    assert shp.with_suffix(".shx").exists()
    assert ds.is_cached()
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_returns_cached_without_redownload(tmp_path: Path, fake_lga_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL_2025, body=fake_lga_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    first = ds.fetch()
    second = ds.fetch()

    assert first == second
    assert len(responses.calls) == 1


@responses.activate
def test_load_returns_geodataframe_with_lga_codes(
    tmp_path: Path, fake_lga_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL_2025, body=fake_lga_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    gdf = ds.load()

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert set(gdf["LGA_CODE25"]) == {"10500", "10580", "11500"}
    assert gdf.crs is not None
    # CRS round-trip through .shp doesn't always preserve the EPSG code,
    # but the datum identification (GDA2020) survives via the .prj WKT.
    crs_name = (gdf.crs.name or "").upper()
    assert "GDA2020" in crs_name, f"expected GDA2020 CRS, got {gdf.crs}"


@responses.activate
def test_load_uses_feather_cache_on_second_call(tmp_path: Path, fake_lga_zip_bytes: bytes) -> None:
    """First ``load()`` materialises a feather sidecar; second call reads
    feather (no .shp re-parse). Network calls don't increase on the
    second call either.
    """
    responses.add(responses.GET, EXPECTED_URL_2025, body=fake_lga_zip_bytes, status=200)

    ds = _make_data_source(tmp_path)
    gdf_first = ds.load()
    shp = ds.shapefile_path
    assert shp is not None
    feather = shp.with_suffix(".feather")
    assert feather.exists()

    # Second instance — fresh in-process state, hits the on-disk feather.
    ds2 = _make_data_source(tmp_path)
    gdf_second = ds2.load()
    assert set(gdf_first["LGA_CODE25"]) == set(gdf_second["LGA_CODE25"])
    # Still only the one network call total.
    assert len(responses.calls) == 1
