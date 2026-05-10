"""Tests for v1.4's PRESET pipeline integration in CensusEnricher.

When a config asks for ``some_friendly: PRESET.<id>`` directly, the
enricher should:

1. Look the PRESET id up in the feature registry.
2. Auto-load every numerator / denominator source ref through the
   existing GCP / registered-dataset dispatch (deduped across PRESETs).
3. Run :class:`FeatureEvaluator` against the loaded source columns and
   surface the result as ``<output_prefix><friendly>``.
4. Drop the synthetic source columns so the caller sees only the
   PRESETs (and any other vars) they explicitly asked for.

These tests exercise the integration without hitting the network: the
GCP datapacks loader is stubbed and registered fetchers are
monkey-patched. End-to-end real-data behaviour is covered by
``tools/verify_real_parsers.py``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pandas as pd
import pytest

from census_augment.enrich import CensusEnricher


# ---- common fixtures ----------------------------------------------------


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


def _gcp_datapacks_with(table_data: dict[str, dict[str, list[float]]]) -> MagicMock:
    """Stub DataPacksDataSource that serves prebuilt per-table DataFrames.

    ``table_data`` maps table_id (e.g. "G37") to a dict of
    column→values. The same SA2 index is used across all tables so
    ``pd.concat`` can stitch the output cleanly.
    """
    sa2_index = pd.Index(["117011326", "117011327", "117011328"], name="sa2_code_2021")
    tables = {tid: pd.DataFrame(cols, index=sa2_index) for tid, cols in table_data.items()}
    datapacks = MagicMock()

    def fake_load_table(table_id: str) -> pd.DataFrame:
        return tables[table_id]

    datapacks.load_table.side_effect = fake_load_table
    return datapacks


# ---- PRESET-only ---------------------------------------------------------


def test_preset_only_config_loads_sources_and_evaluates() -> None:
    """A pure-PRESET config triggers source loading + evaluator + cleanup.

    Column names mirror the real G37 schema
    (``tests/fixtures/gcp-schemas/G37.txt``).
    """
    datapacks = _gcp_datapacks_with(
        {
            "G37": {
                "R_Tot_Total": [4500, 250, 1200],
                "Total_Total": [9000, 1000, 8000],
            },
        }
    )

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={"renters_pct": "PRESET.pct_renters"},
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    # The friendly column is present.
    assert "sa2_renters_pct" in lookup.columns

    # Synthetic source columns are not exposed to the caller.
    assert not any(c.startswith("sa2___preset_src__") for c in lookup.columns), (
        f"unexpected synthetic columns in result: {list(lookup.columns)}"
    )

    # The values match the formula the spec encodes
    # (R_Tot_Total / Total_Total * 100).
    expected = pd.Series([50.0, 25.0, 15.0], name="sa2_renters_pct")
    pd.testing.assert_series_equal(
        lookup["sa2_renters_pct"].reset_index(drop=True),
        expected,
        check_names=False,
    )


def test_preset_with_sum_numerator_evaluates_correctly() -> None:
    """pct_drive_to_work uses a multi-field sum numerator; sources still auto-load.

    Column names mirror the real G62 schema
    (``tests/fixtures/gcp-schemas/G62.txt``).
    """
    datapacks = _gcp_datapacks_with(
        {
            "G62": {
                "One_method_Car_as_driver_P": [400, 50, 100],
                "One_method_Car_as_passenger_P": [50, 10, 20],
                "One_method_Truck_P": [40, 5, 5],
                "One_method_Motorbike_scootr_P": [10, 0, 0],
                "Tot_P": [1000, 200, 500],
            }
        }
    )

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={"drove": "PRESET.pct_drive_to_work"},
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    # numerator = 400+50+40+10 = 500 / 1000 = 50%, etc.
    expected = pd.Series([50.0, 32.5, 25.0], name="sa2_drove")
    pd.testing.assert_series_equal(
        lookup["sa2_drove"].reset_index(drop=True),
        expected,
        check_names=False,
    )
    assert not any(c.startswith("sa2___preset_src__") for c in lookup.columns)


# ---- mixed PRESET + plain GCP -------------------------------------------


def test_preset_mixed_with_plain_gcp_variable() -> None:
    """A config with both PRESET and plain GCP refs returns both columns.

    Column names mirror the real G01 / G37 schemas
    (``tests/fixtures/gcp-schemas/``).
    """
    datapacks = _gcp_datapacks_with(
        {
            "G37": {
                "R_Tot_Total": [4500, 250, 1200],
                "Total_Total": [9000, 1000, 8000],
            },
            "G01": {
                "Tot_P_P": [12000, 2000, 9500],
            },
        }
    )

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={
            "pop_total": "G01.Tot_P_P",
            "renters_pct": "PRESET.pct_renters",
        },
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    assert "sa2_pop_total" in lookup.columns
    assert "sa2_renters_pct" in lookup.columns
    # No synthetic leakage.
    assert not any(c.startswith("sa2___preset_src__") for c in lookup.columns)
    # The user's pop_total surfaces through unchanged.
    assert lookup.loc["117011326", "sa2_pop_total"] == 12000
    # The PRESET evaluates correctly.
    assert lookup.loc["117011326", "sa2_renters_pct"] == 50.0


# ---- multiple PRESETs sharing sources -----------------------------------


def test_multiple_real_presets_load_only_required_tables() -> None:
    """Two real PRESETs covering several GCP tables produce all outputs.

    Column names mirror the real G01 / G29 schemas
    (``tests/fixtures/gcp-schemas/``). Note that ``pct_aged_65_plus``
    sums the three 65+ age bands within G01 — there is no separate
    G04 in the real DataPack (that table is split into G04A / G04B
    and neither has a 65+ total).
    """
    datapacks = _gcp_datapacks_with(
        {
            "G01": {
                "Tot_P_P": [10000, 5000, 2000],
                "Age_65_74_yr_P": [1000, 700, 400],
                "Age_75_84_yr_P": [400, 200, 150],
                "Age_85ov_P": [100, 100, 50],
            },
            "G29": {
                "OPF_ChU15_a_Total_F": [200, 100, 50],
                "CF_ChU15_a_Total_F": [800, 400, 200],
            },
        }
    )

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={
            "p65": "PRESET.pct_aged_65_plus",
            "ofp": "PRESET.pct_one_parent_family",
        },
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    assert "sa2_p65" in lookup.columns
    assert "sa2_ofp" in lookup.columns

    # (1000+400+100)/10000*100 = 15.0; 200/(200+800)*100 = 20.0
    assert lookup.loc["117011326", "sa2_p65"] == 15.0
    assert lookup.loc["117011326", "sa2_ofp"] == 20.0

    # Per-table grouping in _build_gcp_lookup ensures one load per table.
    loaded_tables = [call.args[0] for call in datapacks.load_table.call_args_list]
    assert sorted(loaded_tables) == ["G01", "G29"]
    # Each loaded once, not duplicated by synthetic source aliasing.
    assert len(loaded_tables) == len(set(loaded_tables))

    # No synthetic leakage.
    assert not any(c.startswith("sa2___preset_src__") for c in lookup.columns)


def test_overlapping_preset_sources_dedupe_via_custom_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two test PRESETs that share a source ref still load it once.

    Real shipped PRESETs happen not to overlap on GCP refs, so we
    spin up a custom registry with two specs that both reference
    ``G01.Tot_P_P`` and assert dedupe works.
    """
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "ratio_a.md").write_text(
        dedent(
            """\
            ---
            id: ratio_a
            status: proposed
            output_kind: ratio
            dataset: gcp_2021
            numerator:
              expression: field
              field: G01.Tot_P_P
            denominator:
              expression: field
              field: G02.foo
            ---
            ratio_a
            """
        ),
        encoding="utf-8",
    )
    (features_dir / "ratio_b.md").write_text(
        dedent(
            """\
            ---
            id: ratio_b
            status: proposed
            output_kind: ratio
            dataset: gcp_2021
            numerator:
              expression: field
              field: G01.Tot_P_P
            denominator:
              expression: field
              field: G02.bar
            ---
            ratio_b
            """
        ),
        encoding="utf-8",
    )

    from census_augment import enrich as enrich_module
    from census_augment import features as features_module

    custom_registry = features_module.FeatureRegistry.from_repo_specs(features_dir)
    monkeypatch.setattr(enrich_module, "features", custom_registry)

    datapacks = _gcp_datapacks_with(
        {
            "G01": {"Tot_P_P": [10000, 5000, 2000]},
            "G02": {"foo": [100, 100, 100], "bar": [200, 200, 200]},
        }
    )

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={
            "a": "PRESET.ratio_a",
            "b": "PRESET.ratio_b",
        },
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    assert "sa2_a" in lookup.columns
    assert "sa2_b" in lookup.columns

    # G01 loaded exactly once even though two PRESETs depend on
    # G01.Tot_P_P. The per-table grouping inside _build_gcp_lookup
    # together with synthetic-source dedupe in
    # _collect_synthetic_sources guarantees this.
    loaded_tables = [call.args[0] for call in datapacks.load_table.call_args_list]
    assert loaded_tables.count("G01") == 1


# ---- error cases --------------------------------------------------------


def test_unknown_preset_id_raises_clearly() -> None:
    """``PRESET.does_not_exist`` raises with a helpful list of known ids."""
    with pytest.raises(ValueError, match="unknown PRESET id"):
        CensusEnricher(
            datapacks=_gcp_datapacks_with({}),
            catalog=_stub_catalog(),
            variables={"x": "PRESET.does_not_exist"},
            output_prefix="sa2_",
        ).build_lookup()


def test_malformed_preset_ref_raises_clearly() -> None:
    """A bare ``PRESET.`` with no id raises, not silently skipped."""
    with pytest.raises(ValueError, match="malformed PRESET ref"):
        CensusEnricher(
            datapacks=_gcp_datapacks_with({}),
            catalog=_stub_catalog(),
            variables={"x": "PRESET."},
            output_prefix="sa2_",
        ).build_lookup()


def test_friendly_name_collision_with_synthetic_prefix_raises() -> None:
    """A user variable starting with ``__preset_src__`` is rejected.

    The enricher reserves that prefix for internal source-column
    injection. We surface a clean error at construction time rather
    than silently mangling the user's column.
    """
    with pytest.raises(ValueError, match="reserved for internal"):
        CensusEnricher(
            datapacks=_gcp_datapacks_with({}),
            catalog=_stub_catalog(),
            variables={"__preset_src__foo": "G01.Tot_P_P"},
            output_prefix="sa2_",
        )


# ---- integration with non-GCP dataset PRESET sources --------------------


def test_preset_workspace_strips_namespace_prefix_consistently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirms the workspace passed to FeatureEvaluator uses bare
    ``<NAMESPACE>.<field>`` keys (matching how spec source fields are
    written), not the ``<output_prefix>__preset_src__...`` synthetic
    name. Custom feature spec used so we don't depend on the shipped
    PRESET defaults.
    """
    # Spin up an isolated FeatureRegistry rooted at a custom dir
    # containing exactly one fake spec.
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "my_test_ratio.md").write_text(
        dedent(
            """\
            ---
            id: my_test_ratio
            status: proposed
            output_kind: percentage
            bounds: [0, 100]
            dataset: gcp_2021
            numerator:
              expression: field
              field: G02.numer
            denominator:
              expression: field
              field: G02.denom
            ---
            test feature
            """
        ),
        encoding="utf-8",
    )

    from census_augment import features as features_module

    custom_registry = features_module.FeatureRegistry.from_repo_specs(features_dir)

    # Patch the singleton inside the enrich module — that's the one the
    # PRESET integration consults.
    from census_augment import enrich as enrich_module

    monkeypatch.setattr(enrich_module, "features", custom_registry)

    datapacks = _gcp_datapacks_with({"G02": {"numer": [25, 50, 0], "denom": [100, 100, 100]}})

    enricher = CensusEnricher(
        datapacks=datapacks,
        catalog=_stub_catalog(),
        variables={"ratio": "PRESET.my_test_ratio"},
        output_prefix="sa2_",
    )
    lookup = enricher.build_lookup()

    assert "sa2_ratio" in lookup.columns
    expected = pd.Series([25.0, 50.0, 0.0], name="sa2_ratio")
    pd.testing.assert_series_equal(
        lookup["sa2_ratio"].reset_index(drop=True),
        expected,
        check_names=False,
    )
    assert not any(c.startswith("sa2___preset_src__") for c in lookup.columns)
