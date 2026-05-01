"""Tests for census_augment.pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import responses
from responses import matchers

from census_augment.config import (
    CensusConfig,
    Config,
    DataSourcesConfig,
    GeocodingConfig,
    InputConfig,
    OutputConfig,
)
from census_augment.data_sources.datapacks import DataPackMetadata
from census_augment.geocoding.base import GeocodeResult
from census_augment.geocoding.cache import normalize_address
from census_augment.pipeline import AugmentResult, Pipeline, RunSummary


# ---- helpers --------------------------------------------------------------


class _FakeGeocoder:
    """A test double that returns programmed responses by address."""

    def __init__(self, responses_by_address: dict[str, GeocodeResult]) -> None:
        self._responses = responses_by_address
        self.calls: list[str] = []

    def geocode(self, address: str) -> GeocodeResult:
        self.calls.append(address)
        if address in self._responses:
            return self._responses[address]
        return _failed_result(address)


def _failed_result(address: str) -> GeocodeResult:
    return GeocodeResult(
        address_input=address,
        address_normalized=normalize_address(address),
        lat=None,
        lon=None,
        source="failed",
        provider="fake",
        timestamp=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )


def _success_result(
    address: str, lat: float, lon: float, source: str = "fresh"
) -> GeocodeResult:
    return GeocodeResult(
        address_input=address,
        address_normalized=normalize_address(address),
        lat=lat,
        lon=lon,
        source=source,  # type: ignore[arg-type]
        provider="fake",
        timestamp=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )


def _make_config(
    *,
    tmp_path: Path,
    address_column: str | None = "address",
    latitude_column: str | None = "lat",
    longitude_column: str | None = "lon",
    variables: dict[str, str] | None = None,
    prefix: str = "sa2_",
) -> Config:
    if variables is None:
        variables = {"median_age": "G02.Median_age_persons"}
    return Config(
        input=InputConfig(
            path=tmp_path / "input.csv",
            address_column=address_column,
            latitude_column=latitude_column,
            longitude_column=longitude_column,
        ),
        output=OutputConfig(
            path=tmp_path / "output.csv",
            prefix=prefix,
        ),
        census=CensusConfig(),
        data_sources=DataSourcesConfig(),
        geocoding=GeocodingConfig(user_agent="test/0.1 (test@example.com)"),
        variables=variables,
    )


# ---- RunSummary -----------------------------------------------------------


def test_run_summary_format_human_readable() -> None:
    summary = RunSummary(
        total_rows=10,
        geo_input=3,
        geo_cache=2,
        geo_fresh=4,
        geo_failed=1,
        sa2_unmatched=1,
        fully_enriched=7,
        partially_enriched=2,
    )
    text = summary.format_human_readable()
    assert "Total rows:           10" in text
    assert "From input lat/lon: 3" in text
    assert "Failed:             1" in text
    assert "Outside any SA2:    1" in text
    assert "Fully enriched:     7" in text
    assert "Partially enriched: 2" in text


# ---- Pipeline construction validation -------------------------------------


def _empty_pipeline_pieces(tmp_path: Path) -> dict[str, Any]:
    """Minimal collaborators good enough to satisfy Pipeline's __init__."""
    from census_augment.catalog import VariableCatalog
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.enrich import CensusEnricher
    from census_augment.spatial import SpatialIndex
    import geopandas as gpd
    from shapely.geometry import Polygon

    boundaries = gpd.GeoDataFrame(
        {
            "SA2_CODE21": ["X"],
            "SA2_NAME21": ["X"],
            "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        },
        crs="EPSG:4326",
    )
    spatial = SpatialIndex(boundaries)
    ds = DataPacksDataSource(
        census=CensusConfig(),
        base_url="https://x",
        root=tmp_path / "ds",
    )
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(
        datapacks=ds, catalog=catalog, variables={}, output_prefix="sa2_"
    )
    return {
        "geocoder": _FakeGeocoder({}),
        "spatial": spatial,
        "enricher": enricher,
    }


def test_pipeline_rejects_friendly_name_colliding_with_reserved_column(
    tmp_path: Path,
) -> None:
    """A variable named 'code' with prefix 'sa2_' collides with sa2_code (reserved)."""
    config = _make_config(
        tmp_path=tmp_path,
        variables={"code": "G01.Tot_P_M"},  # would produce sa2_code column
    )
    with pytest.raises(ValueError, match="reserved output columns"):
        Pipeline(config=config, **_empty_pipeline_pieces(tmp_path))


def test_pipeline_rejects_geo_lat_collision(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path=tmp_path,
        variables={"lat": "G01.Tot_P_M"},
        prefix="geo_",
    )
    with pytest.raises(ValueError, match="reserved output columns"):
        Pipeline(config=config, **_empty_pipeline_pieces(tmp_path))


# ---- _validate_input_columns ----------------------------------------------


def test_run_fails_when_input_columns_missing(tmp_path: Path) -> None:
    """When *all* configured locator columns are absent, lenient
    resolution drops them and the pipeline raises with a clear message
    listing the resolved (None) locators and the actual DataFrame columns."""
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text("foo,bar\n1,2\n", encoding="utf-8")

    pipeline = Pipeline(config=config, **_empty_pipeline_pieces(tmp_path))
    with pytest.raises(ValueError, match="no usable locator"):
        pipeline.run()


# ---- _resolve_coordinates per-row decision -------------------------------


def test_resolve_uses_input_latlon_when_present(tmp_path: Path) -> None:
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text(
        "address,lat,lon\nSomewhere,-33.86,151.21\n", encoding="utf-8"
    )
    fake_geo = _FakeGeocoder({})
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df = pd.read_csv(config.input.path)
    lats, lons, sources = pipeline._resolve_coordinates(
        df,
        addr_col=config.input.address_column,
        lat_col=config.input.latitude_column,
        lon_col=config.input.longitude_column,
    )

    assert lats == [-33.86]
    assert lons == [151.21]
    assert sources == ["input"]
    assert fake_geo.calls == []  # no geocoding call


def test_resolve_falls_back_to_address_when_latlon_null(tmp_path: Path) -> None:
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text(
        "address,lat,lon\nFallback Address,,\n", encoding="utf-8"
    )
    fake_geo = _FakeGeocoder(
        {"Fallback Address": _success_result("Fallback Address", -34.0, 150.0)}
    )
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df = pd.read_csv(config.input.path)
    lats, lons, sources = pipeline._resolve_coordinates(
        df,
        addr_col=config.input.address_column,
        lat_col=config.input.latitude_column,
        lon_col=config.input.longitude_column,
    )

    assert lats == [-34.0]
    assert lons == [150.0]
    assert sources == ["fresh"]
    assert fake_geo.calls == ["Fallback Address"]


def test_resolve_geocode_failure_propagates_source(tmp_path: Path) -> None:
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text(
        "address,lat,lon\nNo Such Place,,\n", encoding="utf-8"
    )
    fake_geo = _FakeGeocoder({})  # default: returns failed
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df = pd.read_csv(config.input.path)
    lats, lons, sources = pipeline._resolve_coordinates(
        df,
        addr_col=config.input.address_column,
        lat_col=config.input.latitude_column,
        lon_col=config.input.longitude_column,
    )

    assert lats == [None]
    assert lons == [None]
    assert sources == ["failed"]


def test_resolve_cache_source_propagates(tmp_path: Path) -> None:
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text(
        "address,lat,lon\nCached,,\n", encoding="utf-8"
    )
    fake_geo = _FakeGeocoder(
        {"Cached": _success_result("Cached", -33.0, 151.0, source="cache")}
    )
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df = pd.read_csv(config.input.path)
    _, _, sources = pipeline._resolve_coordinates(
        df,
        addr_col=config.input.address_column,
        lat_col=config.input.latitude_column,
        lon_col=config.input.longitude_column,
    )

    assert sources == ["cache"]


def test_resolve_no_locator_at_all_yields_failed(tmp_path: Path) -> None:
    """Empty address row + null lat/lon → failed."""
    config = _make_config(tmp_path=tmp_path)
    config.input.path.write_text(
        "address,lat,lon\n,,\n", encoding="utf-8"
    )
    fake_geo = _FakeGeocoder({})
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df = pd.read_csv(config.input.path)
    lats, lons, sources = pipeline._resolve_coordinates(
        df,
        addr_col=config.input.address_column,
        lat_col=config.input.latitude_column,
        lon_col=config.input.longitude_column,
    )

    assert lats == [None]
    assert lons == [None]
    assert sources == ["failed"]
    assert fake_geo.calls == []  # no geocoding attempt for empty address


# ---- end-to-end smoke (the real proof) ------------------------------------


@responses.activate
def test_end_to_end_smoke(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """End-to-end via Pipeline.from_config with all HTTP mocked.

    Three rows: one with input lat/lon, one geocoded successfully,
    one geocoded as a failure. Verifies output column order, content,
    and run summary.
    """
    config = _make_config(
        tmp_path=tmp_path,
        variables={
            "median_age": "G02.Median_age_persons",
            "total_pop": "G01.Tot_P_P",
        },
    )
    config.input.path.write_text(
        "address,lat,lon\n"
        "Sydney CBD,-33.86,151.21\n"      # input lat/lon
        "1 Macquarie St,,\n"               # geocode success
        "Nowhere,,\n",                     # geocode failure
        encoding="utf-8",
    )

    # Mock HTTP for all data sources
    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)
    responses.add(
        responses.GET,
        "https://nominatim.openstreetmap.org/search",
        json=[{"lat": "-33.86", "lon": "151.21", "display_name": "1 Macquarie St"}],
        status=200,
        match=[matchers.query_param_matcher({"q": "1 Macquarie St"}, strict_match=False)],
    )
    responses.add(
        responses.GET,
        "https://nominatim.openstreetmap.org/search",
        json=[],
        status=200,
        match=[matchers.query_param_matcher({"q": "Nowhere"}, strict_match=False)],
    )

    pipeline = Pipeline.from_config(
        config,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    summary = pipeline.run()

    # Run summary classification
    assert summary.total_rows == 3
    assert summary.geo_input == 1
    assert summary.geo_fresh == 1
    assert summary.geo_failed == 1
    assert summary.geo_cache == 0
    # Both successful geocodes land in fixture's Sydney CBD polygon
    assert summary.fully_enriched == 2
    assert summary.partially_enriched == 0  # geocode-failed row has no SA2

    # Output file content + column order per spec §8
    out = pd.read_csv(config.output.path)
    assert list(out.columns) == [
        "address",
        "lat",
        "lon",
        "geo_lat",
        "geo_lon",
        "geo_source",
        "sa2_code",
        "sa2_name",
        "sa2_median_age",
        "sa2_total_pop",
    ]
    assert len(out) == 3

    # Row 0: input lat/lon → SA2 from spatial → enrichment
    assert out.loc[0, "geo_source"] == "input"
    assert out.loc[0, "sa2_code"] == 117011326
    assert out.loc[0, "sa2_name"] == "Sydney CBD"
    assert out.loc[0, "sa2_median_age"] == 35
    assert out.loc[0, "sa2_total_pop"] == 10200

    # Row 1: geocoded successfully into the same Sydney CBD polygon
    assert out.loc[1, "geo_source"] == "fresh"
    assert out.loc[1, "sa2_code"] == 117011326
    assert out.loc[1, "sa2_median_age"] == 35

    # Row 2: failed → null all the way down
    assert out.loc[2, "geo_source"] == "failed"
    assert pd.isna(out.loc[2, "geo_lat"])
    assert pd.isna(out.loc[2, "sa2_code"])
    assert pd.isna(out.loc[2, "sa2_median_age"])


@responses.activate
def test_end_to_end_with_partially_enriched_row(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """A row whose SA2 isn't in the DataPack should count as partially enriched."""
    # Use coordinates inside one of the fixture polygons (so SA2 join succeeds)
    # but *also* an unrelated row whose lat/lon won't match any SA2.
    config = _make_config(
        tmp_path=tmp_path,
        variables={"median_age": "G02.Median_age_persons"},
    )
    config.input.path.write_text(
        "address,lat,lon\n"
        "Sydney,-33.86,151.21\n"     # SA2 hit + enrichment hit
        "Open ocean,-40.0,160.0\n",  # outside any SA2 (sa2_unmatched)
        encoding="utf-8",
    )

    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    summary = pipeline.run()

    assert summary.total_rows == 2
    assert summary.geo_input == 2
    assert summary.sa2_unmatched == 1
    assert summary.fully_enriched == 1
    assert summary.partially_enriched == 0


# ---- Pipeline.augment (library entry point, spec §18) ---------------------


@responses.activate
def test_augment_returns_augment_result_with_expected_shape(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    config = _make_config(
        tmp_path=tmp_path,
        variables={"median_age": "G02.Median_age_persons"},
    )
    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
    )

    df_in = pd.DataFrame(
        {
            "label": ["Sydney", "Open ocean", "Bad row"],
            "address": [None, None, None],
            "lat": [-33.86, -40.0, None],
            "lon": [151.21, 160.0, None],
        }
    )
    result = pipeline.augment(df_in)

    assert isinstance(result, AugmentResult)
    assert isinstance(result.df, pd.DataFrame)
    assert isinstance(result.summary, RunSummary)
    assert isinstance(result.added_columns, list)
    assert "geo_lat" in result.added_columns
    assert "sa2_code" in result.added_columns
    assert "sa2_median_age" in result.added_columns

    # Row 0: Sydney CBD inside fixture polygon, fully enriched
    assert result.is_fully_enriched.iloc[0]
    assert not result.geocoding_failed.iloc[0]
    assert not result.sa2_unmatched.iloc[0]

    # Row 1: open ocean - has coords but no SA2 match
    assert not result.is_fully_enriched.iloc[1]
    assert not result.geocoding_failed.iloc[1]
    assert result.sa2_unmatched.iloc[1]

    # Row 2: null inputs - geocoding failed
    assert not result.is_fully_enriched.iloc[2]
    assert result.geocoding_failed.iloc[2]
    assert not result.sa2_unmatched.iloc[2]


@responses.activate
def test_augment_does_not_mutate_input(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    config = _make_config(tmp_path=tmp_path)
    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
    )

    df_in = pd.DataFrame({"address": ["x"], "lat": [-33.86], "lon": [151.21]})
    cols_before = list(df_in.columns)
    pipeline.augment(df_in)
    # Input DataFrame must be unchanged
    assert list(df_in.columns) == cols_before


@responses.activate
def test_augment_with_column_name_overrides(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """Per-call kwargs override config.input.* — useful when a notebook
    DataFrame uses different column names than what the config declares."""
    # Config has only lat/lon configured; we'll override their names per-call.
    config = _make_config(
        tmp_path=tmp_path,
        address_column=None,
        latitude_column="lat",
        longitude_column="lon",
    )
    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
    )

    # DataFrame uses "latitude"/"longitude" instead of configured "lat"/"lon"
    df_in = pd.DataFrame({"latitude": [-33.86], "longitude": [151.21]})
    result = pipeline.augment(
        df_in, latitude_column="latitude", longitude_column="longitude"
    )

    assert result.df.loc[0, "geo_source"] == "input"
    # In-memory: sa2_code is a string from spatial.lookup_many (no CSV round-trip)
    assert result.df.loc[0, "sa2_code"] == "117011326"


def test_augment_missing_column_raises(tmp_path: Path) -> None:
    """All configured columns absent → no usable locator → raises with
    a message that reports both resolution result and DataFrame schema."""
    config = _make_config(tmp_path=tmp_path)
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)

    df_in = pd.DataFrame({"only_this_column": [1]})
    with pytest.raises(ValueError, match="no usable locator"):
        pipeline.augment(df_in)


def test_augment_explicit_none_override_disables_locator(tmp_path: Path) -> None:
    """Passing ``address_column=None`` explicitly should disable address
    resolution for that call, even when config has it set."""
    config = _make_config(tmp_path=tmp_path)  # configures all 3 cols
    fake_geo = _FakeGeocoder({})
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    # df has address present too — but we'll override to None so it's ignored
    df_in = pd.DataFrame({
        "address": ["Some Address"],
        "lat": [-33.86],
        "lon": [151.21],
    })
    result = pipeline.augment(df_in, address_column=None)

    # Geocoder was never consulted (lat/lon path took precedence)
    assert fake_geo.calls == []
    # And nothing's flagged as unused — address override was intentional
    assert result.summary.unused_configured_columns == []
    assert result.df.loc[0, "geo_source"] == "input"


def test_augment_lenient_absent_address_column(tmp_path: Path) -> None:
    """A config with address_column set but a DataFrame that doesn't
    have it should NOT error — drop the address with a warning, use
    lat/lon, and surface the absent column in the run summary."""
    config = _make_config(tmp_path=tmp_path)  # configures all 3
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)

    df_in = pd.DataFrame({"lat": [-33.86], "lon": [151.21]})
    result = pipeline.augment(df_in)

    assert result.df.loc[0, "geo_source"] == "input"
    assert result.summary.unused_configured_columns == ["address"]


def test_augment_lenient_absent_lat_lon(tmp_path: Path) -> None:
    """Conversely, if lat/lon are configured but absent, we fall back
    to address (still lenient — no hard error)."""
    config = _make_config(tmp_path=tmp_path)
    fake_geo = _FakeGeocoder(
        {"Sydney CBD": _success_result("Sydney CBD", -33.86, 151.21)}
    )
    pieces = _empty_pipeline_pieces(tmp_path)
    pieces["geocoder"] = fake_geo
    pipeline = Pipeline(config=config, **pieces)

    df_in = pd.DataFrame({"address": ["Sydney CBD"]})  # no lat/lon
    result = pipeline.augment(df_in)

    assert fake_geo.calls == ["Sydney CBD"]
    assert sorted(result.summary.unused_configured_columns) == ["lat", "lon"]


@responses.activate
def test_from_config_uses_null_cache_when_cache_disabled(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """``geocoding.cache_enabled = false`` should result in a NullCache
    being wired into the geocoder, so per-row geocodes never short-circuit."""
    from census_augment.geocoding.cache import NullCache
    from census_augment.geocoding.nominatim import NominatimGeocoder

    config = _make_config(tmp_path=tmp_path)
    config = config.model_copy(
        update={
            "geocoding": config.geocoding.model_copy(
                update={"cache_enabled": False}
            )
        }
    )

    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
    )

    assert isinstance(pipeline._geocoder, NominatimGeocoder)
    assert isinstance(pipeline._geocoder._cache, NullCache)


@responses.activate
def test_from_config_uses_real_cache_by_default(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """Default (cache_enabled=True) wires a normal GeocodeCache."""
    from census_augment.geocoding.cache import GeocodeCache, NullCache

    config = _make_config(tmp_path=tmp_path)
    boundaries_url = (
        f"{config.data_sources.boundaries_base_url}/SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        f"{config.data_sources.datapacks_base_url}/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.from_config(
        config, data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
    )

    cache = pipeline._geocoder._cache
    assert isinstance(cache, GeocodeCache)
    assert not isinstance(cache, NullCache)


def test_run_summary_format_includes_unused_columns(tmp_path: Path) -> None:
    """The human-readable run summary lists unused configured columns
    so CLI users see why an apparently-set field had no effect."""
    config = _make_config(tmp_path=tmp_path)
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)

    df_in = pd.DataFrame({"lat": [-33.86], "lon": [151.21]})
    result = pipeline.augment(df_in)

    text = result.summary.format_human_readable()
    assert "Unused configured columns" in text
    assert "address" in text


def test_augment_no_locator_raises(tmp_path: Path) -> None:
    """Even though InputConfig validates at config load, augment() also
    defends in case overrides nullify all locators (shouldn't be possible
    today, but the check protects against future signature changes)."""
    # Build a config with only address, then call augment passing nothing
    # but a DataFrame that doesn't have it - the missing-column check
    # fires first, but exercises the same defensive path.
    config = _make_config(
        tmp_path=tmp_path,
        latitude_column=None,
        longitude_column=None,
    )
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)
    df_in = pd.DataFrame({"unrelated": [1]})

    with pytest.raises(ValueError):
        pipeline.augment(df_in)


# ---- Pipeline.create (notebook factory, spec §18.1) ----------------------


@responses.activate
def test_create_factory_builds_pipeline(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """Pipeline.create constructs a default Config from kwargs and runs."""
    boundaries_url = (
        "https://www.abs.gov.au/statistics/standards/"
        "australian-statistical-geography-standard-asgs-edition-3/"
        "jul2021-jun2026/access-and-downloads/digital-boundary-files/"
        "SA2_2021_AUST_SHP_GDA2020.zip"
    )
    datapacks_url = (
        "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
        "2021_GCP_SA2_for_AUS_short-header.zip"
    )
    responses.add(responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200)
    responses.add(responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200)

    pipeline = Pipeline.create(
        variables={"median_age": "G02.Median_age_persons"},
        user_agent="test/0.1 (test@example.com)",
        latitude_column="lat",
        longitude_column="lon",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )

    df = pd.DataFrame({"lat": [-33.86], "lon": [151.21]})
    result = pipeline.augment(df)

    assert "sa2_median_age" in result.df.columns
    assert result.df.loc[0, "sa2_median_age"] == 35


def test_create_factory_requires_at_least_one_locator() -> None:
    """InputConfig's at-least-one-locator rule still applies."""
    with pytest.raises(Exception):
        Pipeline.create(
            variables={"x": "G01.Tot_P_M"},
            user_agent="test/0.1 (test@example.com)",
            # no address_column, no latitude/longitude → InputConfig fails
        )


# ---- Pipeline.run requires paths (spec §6.1, §18) -------------------------


def test_run_raises_when_input_path_missing(tmp_path: Path) -> None:
    """Library users can build a Config without input.path (for augment()),
    but run() must reject it."""
    config = _make_config(tmp_path=tmp_path)
    config = config.model_copy(
        update={"input": config.input.model_copy(update={"path": None})}
    )
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)

    with pytest.raises(ValueError, match="input.path"):
        pipeline.run()


def test_run_raises_when_output_path_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path=tmp_path)
    config = config.model_copy(
        update={"output": config.output.model_copy(update={"path": None})}
    )
    config.input.path.write_text("address,lat,lon\nx,-33.86,151.21\n")
    pieces = _empty_pipeline_pieces(tmp_path)
    pipeline = Pipeline(config=config, **pieces)

    with pytest.raises(ValueError, match="output.path"):
        pipeline.run()
