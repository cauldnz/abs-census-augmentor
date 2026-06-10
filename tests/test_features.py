"""Tests for census_augment.features (spec §21)."""

from __future__ import annotations

import logging
from pathlib import Path
from textwrap import dedent

import pandas as pd
import pytest

from census_augment.features import (
    FeatureEvaluator,
    FeatureSpec,
    features,
    parse_feature_spec,
)


# ---- spec parser --------------------------------------------------------


def _write_feature_spec(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "test_feature.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_minimal_feature_spec(tmp_path: Path) -> None:
    spec_path = _write_feature_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: test_feature
            status: proposed
            output_kind: percentage
            bounds: [0, 100]
            dataset: gcp
            default: false
            tags: [test]
            numerator:
              expression: field
              field: G02.foo
            denominator:
              expression: field
              field: G01.bar
            ---

            # test_feature

            Body content.
            """
        ),
    )
    spec = parse_feature_spec(spec_path)
    assert spec.id == "test_feature"
    assert spec.bounds == (0, 100)
    assert spec.numerator.field == "G02.foo"
    assert spec.denominator.field == "G01.bar"
    assert spec.edge_cases.zero_denominator == "null"  # default


def test_parse_sum_numerator(tmp_path: Path) -> None:
    spec_path = _write_feature_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: test_sum
            status: proposed
            output_kind: percentage
            bounds: null
            dataset: gcp
            numerator:
              expression: sum
              fields:
                - G62.foo
                - G62.bar
            denominator:
              expression: field
              field: G62.tot
            ---

            body
            """
        ),
    )
    spec = parse_feature_spec(spec_path)
    assert spec.numerator.expression == "sum"
    assert spec.numerator.fields == ["G62.foo", "G62.bar"]


def test_parse_invalid_spec_raises(tmp_path: Path) -> None:
    # Missing required fields.
    spec_path = _write_feature_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: bad
            status: proposed
            ---

            body
            """
        ),
    )
    with pytest.raises(ValueError):
        parse_feature_spec(spec_path)


# ---- registry -----------------------------------------------------------


def test_registry_loads_repo_features() -> None:
    """The package-level `features` registry picks up the repo's
    `features/` directory."""
    ids = {f.id for f in features.list_features()}
    assert "pct_drive_to_work" in ids
    assert "pct_renters" in ids
    assert "pct_aged_65_plus" in ids
    assert "pct_employed_full_time" in ids
    assert "pct_one_parent_family" in ids
    assert "motor_vehicles_per_dwelling" in ids


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError):
        features.get("nope_doesnt_exist")


# ---- source_fields() ----------------------------------------------------


def test_source_fields_for_field_expression() -> None:
    """``field`` expressions contribute their single ref."""
    spec = features.get("pct_renters")
    assert spec.source_fields() == {"G37.R_Tot_Total", "G37.Total_Total"}


def test_source_fields_for_sum_expression() -> None:
    """``sum`` expressions contribute every field; combined with the
    denominator's single field."""
    spec = features.get("pct_drive_to_work")
    assert spec.source_fields() == {
        "G62.One_method_Car_as_driver_P",
        "G62.One_method_Car_as_passenger_P",
        "G62.One_method_Truck_P",
        "G62.One_method_Motorbike_scootr_P",
        "G62.Tot_P",
    }


def test_source_fields_dedupes_across_numerator_and_denominator(
    tmp_path: Path,
) -> None:
    """If the same field appears in both num and den, it surfaces once."""
    spec_path = _write_feature_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: same_top_and_bottom
            status: proposed
            output_kind: ratio
            dataset: gcp
            numerator:
              expression: field
              field: G01.Tot_P_P
            denominator:
              expression: field
              field: G01.Tot_P_P
            ---
            test
            """
        ),
    )
    spec = parse_feature_spec(spec_path)
    assert spec.source_fields() == {"G01.Tot_P_P"}


# ---- evaluator ----------------------------------------------------------


def _make_spec(**overrides: object) -> FeatureSpec:
    """Build a FeatureSpec via dict for tests."""
    base: dict[str, object] = {
        "id": "test",
        "status": "proposed",
        "output_kind": "percentage",
        "bounds": [0, 100],
        "dataset": "gcp",
        "default": False,
        "tags": [],
        "numerator": {"expression": "field", "field": "G62.num"},
        "denominator": {"expression": "field", "field": "G62.den"},
        "edge_cases": {
            "zero_denominator": "null",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "warn",
        },
        "sources": [],
        "body": "",
    }
    base.update(overrides)
    return FeatureSpec(**base)


def _make_df(**columns: list[float]) -> pd.DataFrame:
    df = pd.DataFrame(columns)
    df.index = [f"sa2_{i}" for i in range(len(df))]
    df.index.name = "sa2_code_2021"
    return df


def test_evaluate_simple_ratio() -> None:
    spec = _make_spec()
    df = _make_df(**{"G62.num": [50.0, 30.0], "G62.den": [100.0, 60.0]})
    evaluator = FeatureEvaluator(spec)
    result = evaluator.evaluate(df)
    # Percentage: 50% and 50%
    assert list(result) == [50.0, 50.0]


def test_evaluate_sum_numerator() -> None:
    spec = _make_spec(
        numerator={
            "expression": "sum",
            "fields": ["G62.a", "G62.b"],
        },
        denominator={"expression": "field", "field": "G62.den"},
    )
    df = _make_df(
        **{
            "G62.a": [20.0, 10.0],
            "G62.b": [30.0, 20.0],
            "G62.den": [100.0, 100.0],
        }
    )
    result = FeatureEvaluator(spec).evaluate(df)
    assert list(result) == [50.0, 30.0]


def test_evaluate_zero_denominator_default_null() -> None:
    spec = _make_spec()
    df = _make_df(**{"G62.num": [50.0, 30.0], "G62.den": [100.0, 0.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == 50.0
    assert pd.isna(result.iloc[1])


def test_evaluate_zero_denominator_zero_policy() -> None:
    spec = _make_spec(
        edge_cases={
            "zero_denominator": "zero",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "warn",
        },
    )
    df = _make_df(**{"G62.num": [50.0, 30.0], "G62.den": [100.0, 0.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[1] == 0.0


def test_evaluate_zero_denominator_error_policy() -> None:
    spec = _make_spec(
        edge_cases={
            "zero_denominator": "error",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "warn",
        },
    )
    df = _make_df(**{"G62.num": [50.0, 30.0], "G62.den": [100.0, 0.0]})
    with pytest.raises(ValueError, match="denominator zero"):
        FeatureEvaluator(spec).evaluate(df)


def test_evaluate_ratio_output_kind() -> None:
    """ratio output_kind doesn't multiply by 100."""
    spec = _make_spec(output_kind="ratio", bounds=None)
    df = _make_df(**{"G62.num": [1.5], "G62.den": [1.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == 1.5


def test_evaluate_bounds_clip() -> None:
    spec = _make_spec(
        bounds=[0, 100],
        edge_cases={
            "zero_denominator": "null",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "clip",
        },
    )
    # Numerator > denominator → > 100% (out-of-bounds high).
    df = _make_df(**{"G62.num": [120.0], "G62.den": [100.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == 100.0  # clipped


def test_evaluate_bounds_warn(caplog: pytest.LogCaptureFixture) -> None:
    spec = _make_spec(
        bounds=[0, 100],
        edge_cases={
            "zero_denominator": "null",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "warn",
        },
    )
    df = _make_df(**{"G62.num": [120.0], "G62.den": [100.0]})
    with caplog.at_level(logging.WARNING):
        result = FeatureEvaluator(spec).evaluate(df)
    # Returns unclipped value but logs.
    assert result.iloc[0] == 120.0
    assert any("outside bounds" in rec.message for rec in caplog.records)


def test_evaluate_bounds_error() -> None:
    spec = _make_spec(
        bounds=[0, 100],
        edge_cases={
            "zero_denominator": "null",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "error",
        },
    )
    df = _make_df(**{"G62.num": [120.0], "G62.den": [100.0]})
    with pytest.raises(ValueError, match="outside.*bounds"):
        FeatureEvaluator(spec).evaluate(df)


def test_evaluate_missing_source_column_raises() -> None:
    spec = _make_spec()
    df = _make_df(**{"G62.num": [50.0]})  # missing G62.den
    with pytest.raises(KeyError, match="not in"):
        FeatureEvaluator(spec).evaluate(df)


def test_evaluate_strips_namespace_prefix_if_needed() -> None:
    """If the source column is bare (no namespace prefix), the
    evaluator finds it anyway."""
    spec = _make_spec(
        numerator={"expression": "field", "field": "G62.num"},
        denominator={"expression": "field", "field": "G62.den"},
    )
    # DataFrame has bare-column names — no `G62.` prefix.
    df = pd.DataFrame({"num": [50.0], "den": [100.0]})
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == 50.0


# ---- end-to-end against repo specs -------------------------------------


def test_pct_drive_to_work_against_synthetic_gcp_data() -> None:
    """End-to-end: the repo's pct_drive_to_work spec + synthetic GCP
    inputs produce the expected ratio. Column names mirror the real
    G62 schema (`tests/fixtures/gcp-schemas/G62.txt`)."""
    spec = features.get("pct_drive_to_work")
    df = pd.DataFrame(
        {
            "G62.One_method_Car_as_driver_P": [60.0],
            "G62.One_method_Car_as_passenger_P": [5.0],
            "G62.One_method_Truck_P": [3.0],
            "G62.One_method_Motorbike_scootr_P": [2.0],
            "G62.Tot_P": [100.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    # 60+5+3+2 = 70 / 100 = 70%
    assert result.iloc[0] == 70.0


def test_pct_aged_65_plus_against_synthetic_gcp_data() -> None:
    """The repo's pct_aged_65_plus spec sums G01's three 65+ age bands.
    Column names mirror the real G01 schema (`tests/fixtures/gcp-schemas/G01.txt`)."""
    spec = features.get("pct_aged_65_plus")
    df = pd.DataFrame(
        {
            "G01.Age_65_74_yr_P": [200.0],
            "G01.Age_75_84_yr_P": [100.0],
            "G01.Age_85ov_P": [50.0],
            "G01.Tot_P_P": [2000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    # (200+100+50) / 2000 = 17.5%
    assert result.iloc[0] == 17.5


# ---- scale multiplier (spec §21.3) -------------------------------------


def test_scale_defaults_to_one_no_op() -> None:
    """A spec without an explicit `scale` defaults to 1.0 — no change to
    the computed ratio. Backwards-compat guard for every pre-scale PRESET.
    """
    spec = _make_spec()
    assert spec.scale == 1.0
    df = _make_df(**{"G62.num": [50.0], "G62.den": [100.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == 50.0  # plain percentage, unscaled


def test_scale_multiplies_rate() -> None:
    """`scale: 1000` on a rate turns a tiny raw ratio into a per-1,000
    figure. 5 dwellings / 1000 residents → 0.005 × 1000 = 5.0.
    """
    spec = _make_spec(output_kind="rate", bounds=None, scale=1000.0)
    df = _make_df(**{"G62.num": [5.0], "G62.den": [1000.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(5.0)


def test_scale_applies_after_percentage_multiply() -> None:
    """When both percentage and scale are set, scale is applied after the
    ×100. (Rare combination, but the order is defined.) 0.5 ratio →
    ×100 = 50 → ×2 scale = 100.
    """
    spec = _make_spec(output_kind="percentage", bounds=None, scale=2.0)
    df = _make_df(**{"G62.num": [50.0], "G62.den": [100.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(100.0)


def test_scale_applied_before_bounds() -> None:
    """Bounds are checked in the scaled unit. With scale=1000 and the
    raw ratio 0.005, the scaled value 5.0 is inside [0, 10] — no clip.
    """
    spec = _make_spec(
        output_kind="rate",
        bounds=[0, 10],
        scale=1000.0,
        edge_cases={
            "zero_denominator": "null",
            "perturbation_tolerance": "warn_only",
            "out_of_bounds_behaviour": "clip",
        },
    )
    df = _make_df(**{"G62.num": [5.0], "G62.den": [1000.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(5.0)  # inside bounds, not clipped


def test_scale_null_denominator_still_null() -> None:
    """Scale doesn't resurrect a null — a zero denominator yields null
    regardless of the multiplier."""
    spec = _make_spec(output_kind="rate", bounds=None, scale=1000.0)
    df = _make_df(**{"G62.num": [5.0], "G62.den": [0.0]})
    result = FeatureEvaluator(spec).evaluate(df)
    assert pd.isna(result.iloc[0])


# ---- ABS BA PRESETs end-to-end -----------------------------------------


def test_housing_supply_rate_against_synthetic_abs_ba_data() -> None:
    """housing_supply_rate = total_dwellings / population × 1000.
    150 dwellings / 30,000 residents = 0.005 × 1000 = 5.0 per 1,000.
    """
    spec = features.get("housing_supply_rate")
    assert spec.scale == 1000.0
    df = pd.DataFrame(
        {
            "ABS_BA.total_dwellings_count": [150.0],
            "ERP.population_total": [30_000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(5.0)


def test_pct_apartment_approvals_against_synthetic_abs_ba_data() -> None:
    """pct_apartment_approvals = other_residential / total_dwellings × 100.
    90 apartments / 150 total dwellings = 60%.
    """
    spec = features.get("pct_apartment_approvals")
    df = pd.DataFrame(
        {
            "ABS_BA.new_other_residential_building_count": [90.0],
            "ABS_BA.total_dwellings_count": [150.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(60.0)


def test_mean_dwelling_approval_value_against_synthetic_abs_ba_data() -> None:
    """mean_dwelling_approval_value = (value_houses + value_other) /
    total_dwellings × 1000 (converting $'000 to dollars).
    ($60,000k house value + $30,000k apartment value) / 150 dwellings
    = $600k per dwelling in $'000 × 1000 = $600,000.
    """
    spec = features.get("mean_dwelling_approval_value")
    assert spec.scale == 1000.0
    df = pd.DataFrame(
        {
            "ABS_BA.value_new_houses": [60_000.0],  # $'000
            "ABS_BA.value_new_other_residential_building": [30_000.0],  # $'000
            "ABS_BA.total_dwellings_count": [150.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    # (60000 + 30000) / 150 = 600 ($'000 per dwelling) × 1000 = 600,000
    assert result.iloc[0] == pytest.approx(600_000.0)


# ---- AIHW MH treatment-intensity PRESETs end-to-end --------------------


def test_mh_prescriptions_per_patient_against_synthetic_aihw_data() -> None:
    """mh_prescriptions_per_patient = prescriptions / patients (ratio).
    90,000 scripts / 10,000 patients = 9.0 scripts per patient.
    """
    spec = features.get("mh_prescriptions_per_patient")
    assert spec.output_kind == "ratio"
    df = pd.DataFrame(
        {
            "AIHW_MHP.mh_prescriptions_count": [90_000.0],
            "AIHW_MHP.mh_patients_count": [10_000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(9.0)


def test_mh_medicare_services_per_patient_against_synthetic_aihw_data() -> None:
    """mh_medicare_services_per_patient = services / patients (ratio).
    90,000 services / 12,000 patients = 7.5 services per patient.
    """
    spec = features.get("mh_medicare_services_per_patient")
    assert spec.output_kind == "ratio"
    df = pd.DataFrame(
        {
            "AIHW_MBS.mh_medicare_services_count": [90_000.0],
            "AIHW_MBS.mh_medicare_patients_count": [12_000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(7.5)


def test_mh_community_contacts_per_patient_against_synthetic_aihw_data() -> None:
    """mh_community_contacts_per_patient = contacts / patients (ratio).
    85,000 contacts / 5,000 patients = 17.0 contacts per patient.
    """
    spec = features.get("mh_community_contacts_per_patient")
    assert spec.output_kind == "ratio"
    df = pd.DataFrame(
        {
            "AIHW_CMH.mh_community_contacts_count": [85_000.0],
            "AIHW_CMH.mh_community_patients_count": [5_000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(17.0)


def test_mh_community_contacts_per_patient_null_denominator() -> None:
    """A null/zero patient count (suppressed or unpublished SA4) yields
    null rather than a divide-by-zero — the cross-level downscale relies
    on this for SA2s mapped to SA4s AIHW didn't publish.
    """
    spec = features.get("mh_community_contacts_per_patient")
    df = pd.DataFrame(
        {
            "AIHW_CMH.mh_community_contacts_count": [85_000.0, 100.0],
            "AIHW_CMH.mh_community_patients_count": [5_000.0, 0.0],
        }
    )
    df.index = ["sa2_0", "sa2_1"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(17.0)
    assert pd.isna(result.iloc[1])


def test_mh_admitted_avg_length_of_stay_against_synthetic_aihw_data() -> None:
    """mh_admitted_avg_length_of_stay = patient days / hospitalisations.
    15,000 patient days / 1,000 separations = 15.0 days ALOS.
    """
    spec = features.get("mh_admitted_avg_length_of_stay")
    assert spec.output_kind == "ratio"
    df = pd.DataFrame(
        {
            "AIHW_APC.mh_patient_days_count": [15_000.0],
            "AIHW_APC.mh_hospitalisations_count": [1_000.0],
        }
    )
    df.index = ["sa2_0"]
    df.index.name = "sa2_code_2021"
    result = FeatureEvaluator(spec).evaluate(df)
    assert result.iloc[0] == pytest.approx(15.0)
