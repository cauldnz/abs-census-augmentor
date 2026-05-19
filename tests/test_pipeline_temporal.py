"""Temporal-mode integration tests for the Pipeline.

Validates spec-temporal.md §9: rows resolve to per-dataset releases,
get bucketed, and the output gains per-dataset release columns. Tests
are hermetic — they stub the enricher / datasets so no real ABS data
is touched.

Phase F.2 (this PR) lifts the prior single-edition restriction:
cross-edition input is now supported via per-edition spatial indices
plus per-source-edition sub-enricher fan-out (spec-temporal.md §2).
The cross-edition tests below exercise both the success path (Edition
2 SpatialIndex pre-supplied via ``extra_spatial_indices``) and the
factory-missing failure path.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from census_augment.config import (
    CensusConfig,
    Config,
    DataSourcesConfig,
    GeocodingConfig,
    InputConfig,
    NominatimConfig,
    OutputConfig,
    TemporalConfig,
)
from census_augment.data_sources.datapacks import DataPackMetadata
from census_augment.geocoding.base import GeocodeResult
from census_augment.geocoding.cache import normalize_address
from census_augment.pipeline import Pipeline


# ---- fixtures (lighter than test_pipeline.py — no real fetchers) ---------


class _FakeGeocoder:
    """Returns input lat/lon as the geocoded result for any row."""

    def geocode(self, address: str) -> GeocodeResult:
        return GeocodeResult(
            address_input=address,
            address_normalized=normalize_address(address),
            lat=-33.86,
            lon=151.21,
            source="nominatim_fresh",
            provider="fake",
            timestamp=datetime(2024, 6, 1),
        )


def _make_config(tmp_path: Path, **temporal_overrides: Any) -> Config:
    """Config with date_column set + a temporal block."""
    return Config(
        input=InputConfig(
            path=tmp_path / "in.csv",
            latitude_column="lat",
            longitude_column="lon",
            date_column="transaction_date",
        ),
        output=OutputConfig(path=tmp_path / "out.csv"),
        census=CensusConfig(),
        data_sources=DataSourcesConfig(),
        geocoding=GeocodingConfig(
            providers=["nominatim"],
            nominatim=NominatimConfig(user_agent="test/0.1 (test@example.com)"),
        ),
        variables={"pop": "ERP.population_total"},
        temporal=TemporalConfig(**temporal_overrides),
    )


def _make_pipeline(tmp_path: Path, **temporal_overrides: Any) -> Pipeline:
    """Pipeline with a stub enricher that pretends to load ERP releases."""
    from census_augment.catalog import VariableCatalog
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.enrich import CensusEnricher
    from census_augment.spatial import SpatialIndex

    config = _make_config(tmp_path, **temporal_overrides)
    boundaries = gpd.GeoDataFrame(
        {
            "SA2_CODE21": ["117011326"],
            "SA2_NAME21": ["Test SA2"],
            "geometry": [Polygon([(150, -34), (152, -34), (152, -33), (150, -33)])],
        },
        crs="EPSG:4326",
    )
    spatial = SpatialIndex(boundaries)
    datapacks = DataPacksDataSource(
        census=CensusConfig(),
        base_url="https://x",
        root=tmp_path / "ds",
    )
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=catalog,
        variables=config.variables,
        output_prefix="sa2_",
        data_dir=tmp_path,
    )

    # Stub out add_enrichment_columns to return a deterministic value
    # tagged with whatever release the bucket's sub-enricher was
    # constructed with. This lets us verify per-bucket fan-out without
    # hitting real fetchers.
    def _fake_add(df: pd.DataFrame, *, sa2_code_col: str) -> pd.DataFrame:
        # The "release used" for this bucket is in
        # self._dataset_release_overrides — read it off the enricher.
        df = df.copy()
        release = "default"
        # The enricher under test is the bucket sub-enricher; we look
        # at its overrides.
        # MagicMock doesn't help here — we monkeypatch by replacing
        # add_enrichment_columns at class level later.
        df["sa2_pop"] = float(release == "default")
        return df

    # Monkeypatch at class level so sub-enrichers also get the stub.
    from census_augment import enrich as enrich_module

    def _stubbed_add(self: CensusEnricher, df: pd.DataFrame, *, sa2_code_col: str) -> pd.DataFrame:
        df = df.copy()
        rel = self._dataset_release_overrides.get("erp_by_sa2", "default")
        # Encode the bucket's release as the value so the test can
        # assert which release each row got.
        df["sa2_pop"] = [float(int(rel) if rel.isdigit() else 0) for _ in range(len(df))]
        return df

    # Bound monkeypatch
    enrich_module.CensusEnricher.add_enrichment_columns = _stubbed_add  # type: ignore[assignment]

    pipeline = Pipeline(
        config=config,
        geocoders=[_FakeGeocoder()],
        spatial=spatial,
        enricher=enricher,
    )
    return pipeline


# ---- happy path: single bucket -------------------------------------------


def test_temporal_single_bucket_produces_release_column(tmp_path: Path) -> None:
    """All rows resolve to the same release (single bucket); output
    gains the per-dataset release column."""
    pipeline = _make_pipeline(tmp_path)

    df = pd.DataFrame(
        {
            "label": ["a", "b"],
            "lat": [-33.86, -33.86],
            "lon": [151.21, 151.21],
            "transaction_date": pd.to_datetime(["2024-06-01", "2024-09-01"]),
        }
    )
    result = pipeline.augment(df)

    assert "erp_by_sa2_release" in result.df.columns
    # Both dates fall after the 2024 release window start (2023-07-01)
    # → both resolve to ERP 2024.
    assert result.df["erp_by_sa2_release"].tolist() == ["2024", "2024"]
    assert result.releases_used == {"erp_by_sa2": ["2024"]}


# ---- multi-bucket: per-row different releases ---------------------------


def test_temporal_multi_bucket(tmp_path: Path) -> None:
    """Rows from different release windows produce multiple buckets;
    each row's `<dataset>_release` reflects its bucket."""
    pipeline = _make_pipeline(tmp_path)

    df = pd.DataFrame(
        {
            "label": ["a", "b", "c"],
            "lat": [-33.86, -33.86, -33.86],
            "lon": [151.21, 151.21, 151.21],
            "transaction_date": pd.to_datetime(["2023-09-01", "2024-09-01", "2023-04-01"]),
        }
    )
    result = pipeline.augment(df)

    # ERP releases: 2024 covers 2023-07-01 to 2024-06-30; 2023 covers
    # 2022-07-01 to 2023-06-30. closest_at_or_before with row dates
    # 2023-09-01 (=> 2024 release), 2024-09-01 (=> 2024), 2023-04-01
    # (=> 2023 release).
    releases = result.df["erp_by_sa2_release"].tolist()
    assert releases == ["2024", "2024", "2023"]
    assert result.releases_used == {"erp_by_sa2": ["2023", "2024"]}


# ---- cross-edition: Phase F.2 succeeds with per-edition spatial index ----


def _edition_2_synthetic_spatial_index() -> Any:
    """Build a SpatialIndex over a synthetic Edition 2 boundary GDF.

    Uses Edition 2 column conventions (``SA2_MAIN16`` / ``SA2_NAME16``,
    EPSG:4283 / GDA94) so the temporal orchestrator's call to
    ``SpatialIndex.lookup_many`` succeeds without touching the real
    ABS Edition 2 download.
    """
    from census_augment.spatial import SpatialIndex

    boundaries = gpd.GeoDataFrame(
        {
            "SA2_MAIN16": ["117011326"],
            "SA2_NAME16": ["Test SA2 (2016)"],
            "geometry": [Polygon([(150, -34), (152, -34), (152, -33), (150, -33)])],
        },
        crs="EPSG:4283",
    )
    return SpatialIndex(
        boundaries,
        code_column="SA2_MAIN16",
        name_column="SA2_NAME16",
    )


def _make_pipeline_with_edition_2(tmp_path: Path, **temporal_overrides: Any) -> Pipeline:
    """Same wiring as ``_make_pipeline`` but with an Edition 2 SpatialIndex
    pre-supplied via ``extra_spatial_indices``. Enough to exercise the
    cross-edition fan-out without a real boundary fetch.
    """
    from census_augment.catalog import VariableCatalog
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.enrich import CensusEnricher
    from census_augment.spatial import SpatialIndex

    config = _make_config(tmp_path, **temporal_overrides)
    boundaries = gpd.GeoDataFrame(
        {
            "SA2_CODE21": ["117011326"],
            "SA2_NAME21": ["Test SA2"],
            "geometry": [Polygon([(150, -34), (152, -34), (152, -33), (150, -33)])],
        },
        crs="EPSG:4326",
    )
    spatial = SpatialIndex(boundaries)
    datapacks = DataPacksDataSource(
        census=CensusConfig(),
        base_url="https://x",
        root=tmp_path / "ds",
    )
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=catalog,
        variables=config.variables,
        output_prefix="sa2_",
        data_dir=tmp_path,
    )

    return Pipeline(
        config=config,
        geocoders=[_FakeGeocoder()],
        spatial=spatial,
        enricher=enricher,
        extra_spatial_indices={2: _edition_2_synthetic_spatial_index()},
    )


def test_temporal_cross_edition_raises_without_spatial_index(tmp_path: Path) -> None:
    """A row resolving to an Edition 2 release with no Edition 2
    SpatialIndex wired raises a clear RuntimeError naming the missing
    edition. Replaces the prior Phase E.2 ``NotImplementedError`` —
    Phase F.2 lifts the cross-edition block but still surfaces a loud
    failure when the orchestrator can't construct the boundary for
    the resolved release's edition."""
    pipeline = _make_pipeline(tmp_path)

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            "transaction_date": pd.to_datetime(["2020-06-01"]),
        }
    )
    with pytest.raises(RuntimeError, match="Edition 2"):
        pipeline.augment(df)


def test_temporal_cross_edition_succeeds_with_extra_spatial_index(tmp_path: Path) -> None:
    """A row dated 2020 resolves ERP to a 2020 release (Edition 2).
    With ``extra_spatial_indices={2: SpatialIndex(...)}`` supplied, the
    cross-edition orchestrator looks up the Edition-2 SA2 code and the
    bucket's sub-enricher merges on it. Output gains the new
    ``sa2_code_edition`` + ``<dataset>_sa2_code_source`` columns."""
    pipeline = _make_pipeline_with_edition_2(tmp_path)

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            "transaction_date": pd.to_datetime(["2020-06-01"]),
        }
    )
    result = pipeline.augment(df)

    # Cross-edition: source edition is 2, reference edition is 3.
    assert "sa2_code_edition" in result.df.columns
    assert result.df["sa2_code_edition"].iloc[0] == 3
    # Per-dataset source-SA2 column (only emitted when source != reference).
    assert "erp_by_sa2_sa2_code_source" in result.df.columns
    assert result.df["erp_by_sa2_sa2_code_source"].iloc[0] == "117011326"
    # Release column shows the Edition-2 release.
    assert result.df["erp_by_sa2_release"].iloc[0] == "2020"
    assert result.releases_used == {"erp_by_sa2": ["2020"]}
    # The canonical ``sa2_code`` is in the reference edition.
    assert result.df["sa2_code"].iloc[0] == "117011326"
    # Private per-edition columns are dropped before returning.
    assert "_sa2_code_edition_2" not in result.df.columns
    assert "_sa2_code_edition_3" not in result.df.columns


def test_temporal_mixed_edition_buckets(tmp_path: Path) -> None:
    """One row on Edition 3 + one row on Edition 2 → two buckets, each
    looked up against its own edition. The Edition-3 row gets no
    ``<dataset>_sa2_code_source`` (source == reference); the Edition-2
    row does."""
    pipeline = _make_pipeline_with_edition_2(tmp_path)

    df = pd.DataFrame(
        {
            "lat": [-33.86, -33.86],
            "lon": [151.21, 151.21],
            # 2020 → Edition 2; 2024 → Edition 3.
            "transaction_date": pd.to_datetime(["2020-06-01", "2024-06-01"]),
        }
    )
    result = pipeline.augment(df)

    releases = result.df["erp_by_sa2_release"].tolist()
    assert releases == ["2020", "2024"]
    # When ANY row uses a non-reference edition, the per-dataset
    # source-SA2 column is emitted; reference-edition rows still get a
    # value (same as canonical sa2_code).
    assert "erp_by_sa2_sa2_code_source" in result.df.columns
    source_codes = result.df["erp_by_sa2_sa2_code_source"].tolist()
    assert source_codes == ["117011326", "117011326"]
    # Same canonical reference-edition sa2_code.
    assert result.df["sa2_code"].tolist() == ["117011326", "117011326"]
    assert result.releases_used == {"erp_by_sa2": ["2020", "2024"]}


# ---- temporal config: closest rule respected ----------------------------


def test_temporal_closest_rule_via_config(tmp_path: Path) -> None:
    """Setting `temporal.resolution: closest` picks the release whose
    midpoint is nearest — different bucket assignment from
    closest_at_or_before for the same dates."""
    pipeline = _make_pipeline(tmp_path, resolution="closest")

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            # 2024-01-15: FY ending 2024 (Jul 2023–Jun 2024) midpoint
            # ~early Jan. FY ending 2023 midpoint ~early Jan 2023.
            # closest picks 2024.
            "transaction_date": pd.to_datetime(["2024-01-15"]),
        }
    )
    result = pipeline.augment(df)
    assert result.df["erp_by_sa2_release"].iloc[0] == "2024"


# ---- out_of_range: fail vs nearest --------------------------------------


def test_temporal_out_of_range_fail_default(tmp_path: Path) -> None:
    """A row dated before any ERP release fails by default."""
    pipeline = _make_pipeline(tmp_path)

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            "transaction_date": pd.to_datetime(["2000-01-01"]),
        }
    )
    with pytest.raises(ValueError, match="predates the earliest"):
        pipeline.augment(df)


def test_temporal_out_of_range_nearest_clamps(tmp_path: Path) -> None:
    """``out_of_range: nearest`` clamps to the earliest release. Earliest
    ERP release is 2016 (Edition 2); now that Phase F.2 lifts the
    single-edition block, the pipeline succeeds and the clamped row
    rides the cross-edition path."""
    pipeline = _make_pipeline_with_edition_2(tmp_path, out_of_range="nearest")

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            # Pre-2016 → clamped to earliest ERP release (2016, Edition 2).
            "transaction_date": pd.to_datetime(["2010-01-01"]),
        }
    )
    result = pipeline.augment(df)
    # Clamped to ERP 2016.
    assert result.df["erp_by_sa2_release"].iloc[0] == "2016"
    assert result.df["sa2_code_edition"].iloc[0] == 3
    # Source-edition SA2 emitted (2016 → Edition 2 ≠ reference 3).
    assert "erp_by_sa2_sa2_code_source" in result.df.columns


# ---- input-validation -----------------------------------------------------


def test_temporal_missing_date_column_raises(tmp_path: Path) -> None:
    """`input.date_column` references a column that's not in the input."""
    pipeline = _make_pipeline(tmp_path)

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
            # Note: no `transaction_date` column.
        }
    )
    with pytest.raises(ValueError, match="not in the input DataFrame"):
        pipeline.augment(df)


# ---- cross-sectional mode unchanged --------------------------------------


def test_cross_sectional_mode_unaffected_by_temporal_code(tmp_path: Path) -> None:
    """A config without input.date_column runs cross-sectional —
    releases_used is None and no `<dataset>_release` columns appear."""
    from census_augment.catalog import VariableCatalog
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.enrich import CensusEnricher
    from census_augment.spatial import SpatialIndex

    # Build a non-temporal config (no date_column).
    config = Config(
        input=InputConfig(
            path=tmp_path / "in.csv",
            latitude_column="lat",
            longitude_column="lon",
            # NO date_column
        ),
        output=OutputConfig(path=tmp_path / "out.csv"),
        census=CensusConfig(),
        data_sources=DataSourcesConfig(),
        geocoding=GeocodingConfig(
            providers=["nominatim"],
            nominatim=NominatimConfig(user_agent="test/0.1 (test@example.com)"),
        ),
        variables={"pop": "ERP.population_total"},
    )

    boundaries = gpd.GeoDataFrame(
        {
            "SA2_CODE21": ["117011326"],
            "SA2_NAME21": ["Test SA2"],
            "geometry": [Polygon([(150, -34), (152, -34), (152, -33), (150, -33)])],
        },
        crs="EPSG:4326",
    )
    spatial = SpatialIndex(boundaries)
    datapacks = DataPacksDataSource(
        census=CensusConfig(),
        base_url="https://x",
        root=tmp_path / "ds",
    )
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=catalog,
        variables=config.variables,
        output_prefix="sa2_",
        data_dir=tmp_path,
    )

    pipeline = Pipeline(
        config=config,
        geocoders=[_FakeGeocoder()],
        spatial=spatial,
        enricher=enricher,
    )

    df = pd.DataFrame(
        {
            "lat": [-33.86],
            "lon": [151.21],
        }
    )
    result = pipeline.augment(df)

    # No temporal columns; releases_used is None.
    assert "erp_by_sa2_release" not in result.df.columns
    assert result.releases_used is None
