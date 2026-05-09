"""Tests for v1.3's CensusEnricher dispatch across registered datasets.

The enricher used to be GCP-only. v1.3 splits variables by namespace
and routes to the registered dataset's fetcher. These tests confirm
the dispatch logic without exercising the actual fetchers' network
paths.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from census_augment.enrich import CensusEnricher


def _stub_catalog() -> MagicMock:
    """A VariableCatalog stub that resolves G\\d+.foo to itself."""
    catalog = MagicMock()
    def fake_resolve(ref: str):  # type: ignore[no-untyped-def]
        table_id, _, code = ref.partition(".")
        meta = MagicMock()
        meta.table_id = table_id
        meta.code = code
        return meta
    catalog.resolve.side_effect = fake_resolve
    return catalog


def _stub_datapacks() -> MagicMock:
    datapacks = MagicMock()
    df = pd.DataFrame(
        {"foo": [42], "bar": [99]},
        index=pd.Index(["117011326"], name="sa2_code_2021"),
    )
    datapacks.load_table.return_value = df
    return datapacks


def test_enricher_routes_gcp_variables_to_catalog_path() -> None:
    enricher = CensusEnricher(
        datapacks=_stub_datapacks(),
        catalog=_stub_catalog(),
        variables={"my_foo": "G02.foo"},
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()
    assert "sa2_my_foo" in lookup.columns
    assert lookup.loc["117011326", "sa2_my_foo"] == 42


def test_enricher_routes_dataset_variables_to_fetcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SEIFA variable triggers the SEIFA fetcher path."""
    # Stub out the SEIFA fetcher's load() — we don't want network.
    fake_load = MagicMock(
        return_value=pd.DataFrame(
            {"irsd_aus_decile": [8]},
            index=pd.Index(["117011326"], name="sa2_code_2021"),
        )
    )

    def fake_build_seifa(root: Path):  # type: ignore[no-untyped-def]
        instance = MagicMock()
        instance.load = fake_load
        return instance

    from census_augment import enrich as enrich_module

    monkeypatch.setitem(
        enrich_module._FETCHER_FACTORIES, "seifa_2021", fake_build_seifa
    )

    enricher = CensusEnricher(
        datapacks=_stub_datapacks(),
        catalog=_stub_catalog(),
        variables={"irsd_decile": "SEIFA.irsd_aus_decile"},
        output_prefix="sa2_",
        data_dir=tmp_path,
    )
    lookup = enricher.build_lookup()
    fake_load.assert_called_once()
    assert "sa2_irsd_decile" in lookup.columns
    assert lookup.loc["117011326", "sa2_irsd_decile"] == 8


def test_enricher_handles_mixed_gcp_and_dataset_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config with both `G02.foo` and `SEIFA.bar` produces both columns."""
    fake_load = MagicMock(
        return_value=pd.DataFrame(
            {"irsd_aus_decile": [8]},
            index=pd.Index(["117011326"], name="sa2_code_2021"),
        )
    )

    def fake_build_seifa(root: Path):  # type: ignore[no-untyped-def]
        instance = MagicMock()
        instance.load = fake_load
        return instance

    from census_augment import enrich as enrich_module

    monkeypatch.setitem(
        enrich_module._FETCHER_FACTORIES, "seifa_2021", fake_build_seifa
    )

    enricher = CensusEnricher(
        datapacks=_stub_datapacks(),
        catalog=_stub_catalog(),
        variables={
            "my_foo": "G02.foo",
            "irsd_decile": "SEIFA.irsd_aus_decile",
        },
        output_prefix="sa2_",
        data_dir=tmp_path,
    )
    lookup = enricher.build_lookup()
    assert "sa2_my_foo" in lookup.columns
    assert "sa2_irsd_decile" in lookup.columns


def test_enricher_dataset_variable_without_data_dir_raises() -> None:
    enricher = CensusEnricher(
        datapacks=_stub_datapacks(),
        catalog=_stub_catalog(),
        variables={"irsd": "SEIFA.irsd_aus_decile"},
        output_prefix="sa2_",
        # data_dir intentionally None
    )
    with pytest.raises(ValueError, match="data_dir"):
        enricher.build_lookup()


def test_enricher_dataset_missing_column_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking for a SEIFA field the fetcher doesn't expose raises clearly."""
    fake_load = MagicMock(
        return_value=pd.DataFrame(
            {"irsd_aus_decile": [8]},  # has irsd, not ier
            index=pd.Index(["117011326"], name="sa2_code_2021"),
        )
    )

    def fake_build_seifa(root: Path):  # type: ignore[no-untyped-def]
        instance = MagicMock()
        instance.load = fake_load
        return instance

    from census_augment import enrich as enrich_module

    monkeypatch.setitem(
        enrich_module._FETCHER_FACTORIES, "seifa_2021", fake_build_seifa
    )

    enricher = CensusEnricher(
        datapacks=_stub_datapacks(),
        catalog=_stub_catalog(),
        variables={"ier": "SEIFA.ier_aus_decile"},  # not present
        output_prefix="sa2_",
        data_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="doesn't expose columns"):
        enricher.build_lookup()
