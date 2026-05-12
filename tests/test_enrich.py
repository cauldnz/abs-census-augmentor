"""Tests for census_augment.enrich."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses
from pytest_mock import MockerFixture

from census_augment.catalog import CatalogError, VariableCatalog
from census_augment.config import CensusConfig
from census_augment.data_sources.datapacks import (
    DataPackMetadata,
    DataPacksDataSource,
)
from census_augment.enrich import CensusEnricher


BASE_URL = "https://abs.test/datapacks"
EXPECTED_URL = f"{BASE_URL}/2021_GCP_SA2_for_AUS_short-header.zip"


def _make_data_source(tmp_path: Path) -> DataPacksDataSource:
    return DataPacksDataSource(
        census=CensusConfig(),
        base_url=BASE_URL,
        root=tmp_path / "data" / "census",
    )


def _make_enricher(
    tmp_path: Path,
    variables: dict[str, str],
    output_prefix: str = "sa2_",
) -> tuple[CensusEnricher, DataPacksDataSource]:
    ds = _make_data_source(tmp_path)
    catalog = VariableCatalog.from_data_source(ds)
    enricher = CensusEnricher(
        datapacks=ds,
        catalog=catalog,
        variables=variables,
        output_prefix=output_prefix,
    )
    return enricher, ds


# ---- build_lookup ---------------------------------------------------------


@responses.activate
def test_build_lookup_single_variable(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"median_age": "G02.Median_age_persons"})
    lookup = enricher.build_lookup()

    assert list(lookup.columns) == ["sa2_median_age"]
    assert "117011326" in lookup.index
    assert lookup.loc["117011326", "sa2_median_age"] == 35


@responses.activate
def test_build_lookup_multiple_variables_same_table(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(
        tmp_path,
        {
            "median_age": "G02.Median_age_persons",
            "median_rent": "G02.Median_rent_weekly",
        },
    )
    lookup = enricher.build_lookup()

    assert set(lookup.columns) == {"sa2_median_age", "sa2_median_rent"}
    assert lookup.loc["117011326", "sa2_median_age"] == 35
    assert lookup.loc["117011326", "sa2_median_rent"] == 550


@responses.activate
def test_build_lookup_multiple_variables_different_tables(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(
        tmp_path,
        {
            "median_age": "G02.Median_age_persons",
            "total_pop": "G01.Tot_P_P",
        },
    )
    lookup = enricher.build_lookup()

    assert set(lookup.columns) == {"sa2_median_age", "sa2_total_pop"}
    assert lookup.loc["117011326", "sa2_median_age"] == 35
    assert lookup.loc["117011326", "sa2_total_pop"] == 10200


@responses.activate
def test_build_lookup_loads_each_unique_table_only_once(
    tmp_path: Path,
    fake_datapack_zip_bytes: bytes,
    mocker: MockerFixture,
) -> None:
    """Three variables, two unique tables → load_table called twice."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, ds = _make_enricher(
        tmp_path,
        {
            "median_age": "G02.Median_age_persons",
            "median_rent": "G02.Median_rent_weekly",  # same table
            "total_pop": "G01.Tot_P_P",  # different table
        },
    )
    spy = mocker.spy(ds, "load_table")
    enricher.build_lookup()

    assert spy.call_count == 2
    loaded_tables = {c.args[0] for c in spy.call_args_list}
    assert loaded_tables == {"G01", "G02"}


def test_build_lookup_empty_variables_returns_empty(tmp_path: Path) -> None:
    """No HTTP needed — empty variables short-circuits before any IO."""
    ds = _make_data_source(tmp_path)
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(datapacks=ds, catalog=catalog, variables={})
    lookup = enricher.build_lookup()

    assert lookup.empty


@responses.activate
def test_build_lookup_unknown_table_raises_catalog_error(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"bad_variable": "G99.does_not_exist"})
    with pytest.raises(CatalogError, match="G99"):
        enricher.build_lookup()


# ---- add_enrichment_columns: happy path -----------------------------------


@responses.activate
def test_add_enrichment_columns_adds_named_columns(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(
        tmp_path,
        {
            "median_age": "G02.Median_age_persons",
            "total_pop": "G01.Tot_P_P",
        },
    )
    df_in = pd.DataFrame(
        {
            "address": ["Sydney CBD", "North Sydney"],
            "sa2_code": ["117011326", "117011327"],
        }
    )

    df_out = enricher.add_enrichment_columns(df_in)

    assert "sa2_median_age" in df_out.columns
    assert "sa2_total_pop" in df_out.columns
    assert df_out.loc[0, "sa2_median_age"] == 35
    assert df_out.loc[0, "sa2_total_pop"] == 10200
    assert df_out.loc[1, "sa2_median_age"] == 38
    assert df_out.loc[1, "sa2_total_pop"] == 9300


@responses.activate
def test_add_enrichment_columns_preserves_input_row_order(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """Input order must be preserved across the merge — pipeline relies on it."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"total_pop": "G01.Tot_P_P"})
    df_in = pd.DataFrame(
        {
            "address": ["A", "B", "C"],
            "sa2_code": ["117011328", "117011326", "117011327"],
        }
    )

    df_out = enricher.add_enrichment_columns(df_in)

    assert df_out["address"].tolist() == ["A", "B", "C"]
    assert df_out["sa2_total_pop"].tolist() == [12300, 10200, 9300]


@responses.activate
def test_add_enrichment_columns_preserves_existing_input_columns(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"median_age": "G02.Median_age_persons"})
    df_in = pd.DataFrame(
        {
            "address": ["Sydney"],
            "geo_lat": [-33.86],
            "geo_lon": [151.21],
            "sa2_code": ["117011326"],
            "sa2_name": ["Sydney CBD"],
        }
    )

    df_out = enricher.add_enrichment_columns(df_in)

    for col in ["address", "geo_lat", "geo_lon", "sa2_code", "sa2_name"]:
        assert col in df_out.columns


# ---- add_enrichment_columns: null / unmatched SA2 -------------------------


@responses.activate
def test_add_enrichment_columns_with_null_sa2_yields_null_enrichment(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"median_age": "G02.Median_age_persons"})
    df_in = pd.DataFrame(
        {
            "address": ["Geocoded fine", "Geocode failed"],
            "sa2_code": ["117011326", None],
        }
    )

    df_out = enricher.add_enrichment_columns(df_in)

    assert df_out.loc[0, "sa2_median_age"] == 35
    assert pd.isna(df_out.loc[1, "sa2_median_age"])


@responses.activate
def test_add_enrichment_columns_with_unmatched_sa2_yields_null_enrichment(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    """SA2 code that's not in the DataPack (e.g. a real code outside the
    fixture's 3-row slice) gets null enrichment, not an error."""
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"median_age": "G02.Median_age_persons"})
    df_in = pd.DataFrame(
        {
            "address": ["Known", "Unknown"],
            "sa2_code": ["117011326", "999999999"],
        }
    )

    df_out = enricher.add_enrichment_columns(df_in)

    assert df_out.loc[0, "sa2_median_age"] == 35
    assert pd.isna(df_out.loc[1, "sa2_median_age"])


# ---- add_enrichment_columns: configuration ---------------------------------


@responses.activate
def test_add_enrichment_columns_custom_prefix(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(
        tmp_path,
        {"pop": "G01.Tot_P_P"},
        output_prefix="abs_2021_",
    )
    df_in = pd.DataFrame({"sa2_code": ["117011326"]})

    df_out = enricher.add_enrichment_columns(df_in)

    assert "abs_2021_pop" in df_out.columns
    assert df_out.loc[0, "abs_2021_pop"] == 10200


@responses.activate
def test_add_enrichment_columns_custom_sa2_code_column(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    responses.add(responses.GET, EXPECTED_URL, body=fake_datapack_zip_bytes, status=200)
    enricher, _ = _make_enricher(tmp_path, {"pop": "G01.Tot_P_P"})
    df_in = pd.DataFrame({"my_sa2": ["117011326"]})

    df_out = enricher.add_enrichment_columns(df_in, sa2_code_col="my_sa2")

    assert df_out.loc[0, "sa2_pop"] == 10200


def test_add_enrichment_columns_missing_sa2_code_col_raises(
    tmp_path: Path,
) -> None:
    """No HTTP needed — we fail before touching the data source."""
    ds = _make_data_source(tmp_path)
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(datapacks=ds, catalog=catalog, variables={})
    df = pd.DataFrame({"address": ["x"]})

    with pytest.raises(ValueError, match="sa2_code_col"):
        enricher.add_enrichment_columns(df, sa2_code_col="missing")


@responses.activate
def test_add_enrichment_columns_empty_variables_is_no_op(
    tmp_path: Path,
) -> None:
    """No HTTP needed — empty variables means no DataPack work."""
    ds = _make_data_source(tmp_path)
    catalog = VariableCatalog(DataPackMetadata(tables={}))
    enricher = CensusEnricher(datapacks=ds, catalog=catalog, variables={})
    df_in = pd.DataFrame({"address": ["x", "y"], "sa2_code": ["117011326", "117011327"]})

    df_out = enricher.add_enrichment_columns(df_in)

    assert list(df_out.columns) == ["address", "sa2_code"]
    assert df_out.equals(df_in)
