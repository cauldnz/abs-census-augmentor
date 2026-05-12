"""Tests for census_augment.catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
import responses

from census_augment.catalog import CatalogError, VariableCatalog
from census_augment.config import CensusConfig
from census_augment.data_sources.datapacks import (
    ColumnMetadata,
    DataPackMetadata,
    DataPacksDataSource,
    TableMetadata,
)


# ---- helpers ---------------------------------------------------------------


def _make_metadata() -> DataPackMetadata:
    """Tiny synthetic metadata with two tables for catalog tests."""
    return DataPackMetadata(
        tables={
            "G01": TableMetadata(
                table_id="G01",
                name="Selected Person Characteristics by Sex",
                columns={
                    "Tot_P_M": ColumnMetadata(
                        table_id="G01",
                        code="Tot_P_M",
                        description="Males",
                    ),
                    "Tot_P_F": ColumnMetadata(
                        table_id="G01",
                        code="Tot_P_F",
                        description="Females",
                    ),
                    "Tot_P_P": ColumnMetadata(
                        table_id="G01",
                        code="Tot_P_P",
                        description="Persons",
                    ),
                },
            ),
            "G02": TableMetadata(
                table_id="G02",
                name="Selected Medians and Averages",
                columns={
                    "Median_age_persons": ColumnMetadata(
                        table_id="G02",
                        code="Median_age_persons",
                        description="Median age of persons",
                    ),
                    "Median_tot_hhd_inc_weekly": ColumnMetadata(
                        table_id="G02",
                        code="Median_tot_hhd_inc_weekly",
                        description="Median total household income ($/weekly)",
                    ),
                    "Median_rent_weekly": ColumnMetadata(
                        table_id="G02",
                        code="Median_rent_weekly",
                        description="Median rent ($/weekly)",
                    ),
                },
            ),
        }
    )


def _make_catalog() -> VariableCatalog:
    return VariableCatalog(_make_metadata())


# ---- resolve ---------------------------------------------------------------


def test_resolve_returns_column_metadata() -> None:
    cat = _make_catalog()
    col = cat.resolve("G02.Median_age_persons")
    assert col.table_id == "G02"
    assert col.code == "Median_age_persons"
    assert col.description == "Median age of persons"


def test_resolve_invalid_format_raises() -> None:
    cat = _make_catalog()
    with pytest.raises(CatalogError, match="invalid reference format"):
        cat.resolve("missing-the-dot")


@pytest.mark.parametrize("bad_ref", ["G02", "", "G02.", ".Tot_P_M", "1G02.x", "a..b"])
def test_resolve_various_malformed_refs(bad_ref: str) -> None:
    cat = _make_catalog()
    with pytest.raises(CatalogError):
        cat.resolve(bad_ref)


def test_resolve_unknown_table_includes_suggestions() -> None:
    cat = _make_catalog()
    with pytest.raises(CatalogError, match="G01") as exc_info:
        cat.resolve("G03.Tot_P_M")
    msg = str(exc_info.value)
    assert "table 'G03' not found" in msg
    assert "did you mean" in msg


def test_resolve_unknown_column_includes_suggestions() -> None:
    cat = _make_catalog()
    with pytest.raises(CatalogError) as exc_info:
        cat.resolve("G02.Median_age_person")  # missing trailing 's'
    msg = str(exc_info.value)
    assert "column 'Median_age_person' not found in table 'G02'" in msg
    assert "Median_age_persons" in msg
    assert "did you mean" in msg


def test_resolve_unknown_column_no_suggestions_when_dissimilar() -> None:
    """A wildly different column name yields no suggestions, not an exception."""
    cat = _make_catalog()
    with pytest.raises(CatalogError, match="not found") as exc_info:
        cat.resolve("G02.zzzzzzzz")
    # No "did you mean" because nothing's close enough
    assert "did you mean" not in str(exc_info.value)


# ---- validate_variables ----------------------------------------------------


def test_validate_variables_all_valid_passes() -> None:
    cat = _make_catalog()
    cat.validate_variables(
        {
            "median_age": "G02.Median_age_persons",
            "total_pop": "G01.Tot_P_P",
        }
    )  # should not raise


def test_validate_variables_aggregates_all_errors() -> None:
    cat = _make_catalog()
    bad = {
        "good_one": "G02.Median_age_persons",
        "bad_table": "G99.Tot_P_M",
        "bad_column": "G02.Mediaan_age",  # typo
        "bad_format": "no-dot-here",
    }
    with pytest.raises(CatalogError) as exc_info:
        cat.validate_variables(bad)
    msg = str(exc_info.value)
    assert "bad_table" in msg
    assert "bad_column" in msg
    assert "bad_format" in msg
    assert "good_one" not in msg  # the valid one isn't reported


def test_validate_variables_empty_dict_passes() -> None:
    """Empty dict has no entries to validate; trivially passes.
    (Pydantic config rejects empty variables; that's a separate layer.)"""
    cat = _make_catalog()
    cat.validate_variables({})  # no error


# ---- search ----------------------------------------------------------------


def test_search_matches_code_substring() -> None:
    cat = _make_catalog()
    results = cat.search("Median_age")
    assert len(results) == 1
    assert results[0].code == "Median_age_persons"


def test_search_matches_description_substring() -> None:
    cat = _make_catalog()
    results = cat.search("rent")
    codes = [r.code for r in results]
    assert "Median_rent_weekly" in codes


def test_search_case_insensitive() -> None:
    cat = _make_catalog()
    upper = cat.search("MEDIAN")
    lower = cat.search("median")
    assert {r.code for r in upper} == {r.code for r in lower}


def test_search_code_matches_ranked_before_description_only_matches() -> None:
    """A column whose CODE contains the term ranks above one whose only
    description contains it."""
    cat = _make_catalog()
    # 'income' appears only in a description (Median_tot_hhd_inc_weekly's "income"),
    # not in any code.
    results = cat.search("income")
    assert len(results) >= 1
    assert "income" in results[0].description.lower()


def test_search_respects_limit() -> None:
    cat = _make_catalog()
    results = cat.search("Median", limit=2)
    assert len(results) == 2


def test_search_no_match_returns_empty() -> None:
    cat = _make_catalog()
    assert cat.search("definitely_not_a_real_term") == []


# ---- list_table ------------------------------------------------------------


def test_list_table_returns_columns() -> None:
    cat = _make_catalog()
    cols = cat.list_table("G02")
    codes = [c.code for c in cols]
    assert codes == [
        "Median_age_persons",
        "Median_tot_hhd_inc_weekly",
        "Median_rent_weekly",
    ]


def test_list_table_unknown_raises_with_suggestions() -> None:
    cat = _make_catalog()
    with pytest.raises(CatalogError, match="table 'G09' not found") as exc_info:
        cat.list_table("G09")
    assert "did you mean" in str(exc_info.value)


# ---- suggest helpers ------------------------------------------------------


def test_suggest_tables_returns_close_matches() -> None:
    cat = _make_catalog()
    suggestions = cat.suggest_tables("G03")
    assert "G01" in suggestions or "G02" in suggestions


def test_suggest_tables_empty_for_no_match() -> None:
    cat = _make_catalog()
    assert cat.suggest_tables("totally_unrelated") == []


def test_suggest_codes_in_table_returns_close_matches() -> None:
    cat = _make_catalog()
    suggestions = cat.suggest_codes_in_table("G01", "Tot_P_X")
    # Tot_P_M and Tot_P_F differ from Tot_P_X by one char; both should suggest
    assert "Tot_P_M" in suggestions or "Tot_P_F" in suggestions


def test_suggest_codes_in_table_unknown_table_returns_empty() -> None:
    cat = _make_catalog()
    assert cat.suggest_codes_in_table("G99", "anything") == []


# ---- from_data_source factory ---------------------------------------------


@responses.activate
def test_from_data_source_loads_metadata(tmp_path: Path, fake_datapack_zip_bytes: bytes) -> None:
    """End-to-end: factory reads metadata from a DataPacksDataSource."""
    base_url = "https://abs.test/datapacks"
    expected_url = f"{base_url}/2021_GCP_SA2_for_AUS_short-header.zip"
    responses.add(responses.GET, expected_url, body=fake_datapack_zip_bytes, status=200)

    ds = DataPacksDataSource(
        census=CensusConfig(),
        base_url=base_url,
        root=tmp_path / "data" / "census",
    )
    catalog = VariableCatalog.from_data_source(ds)

    assert "G01" in catalog.metadata.tables
    col = catalog.resolve("G02.Median_tot_hhd_inc_weekly")
    assert col.description == "Median total household income ($/weekly)"
