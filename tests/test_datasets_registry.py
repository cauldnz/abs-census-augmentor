"""Tests for census_augment.datasets — spec parser + registry (spec §20)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from census_augment.datasets import (
    DatasetSpec,
    Registry,
    VariableSpec,
    registry,
)
from census_augment.datasets._registry import RegistryError
from census_augment.datasets._spec import parse_dataset_spec


# ---- spec parser --------------------------------------------------------


def _write_spec(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_minimal_valid_spec(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo_2021
            name: Foo 2021
            status: active
            custodian: Test Org
            licence: CC-BY-4.0
            update_cadence: one-shot
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            tags: [test]
            namespace: FOO
            ---

            # Foo 2021

            Description body.
            """
        ),
    )
    spec = parse_dataset_spec(spec_path)
    assert spec.id == "foo_2021"
    assert spec.namespace == "FOO"
    assert spec.tags == ["test"]
    assert spec.variables == []
    assert "Description body." in spec.body


def test_parse_spec_with_schema_table(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            name: Foo
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: one-shot
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            ---

            # Foo

            ## Schema

            | Variable | Type | Description |
            |---|---|---|
            | `FOO.aaa` | int | First var |
            | `FOO.bbb` | float | Second var |

            ## Other section

            (ignored)
            """
        ),
    )
    spec = parse_dataset_spec(spec_path)
    assert len(spec.variables) == 2
    assert spec.variables[0] == VariableSpec(field="aaa", type="int", description="First var")
    assert spec.variables[1].field == "bbb"


def test_parse_no_front_matter_raises(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path, "# Just markdown, no front-matter\n")
    with pytest.raises(ValueError, match="not a valid dataset spec"):
        parse_dataset_spec(spec_path)


def test_parse_invalid_yaml_raises(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        "---\n: : invalid : yaml :\n---\n\nbody\n",
    )
    with pytest.raises(ValueError, match="front-matter is not valid YAML"):
        parse_dataset_spec(spec_path)


def test_parse_spec_without_temporal_block_has_none(tmp_path: Path) -> None:
    """The `temporal:` field is optional; spec without it parses with
    `temporal=None` (datasets default to cross-sectional)."""
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            name: Foo
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: one-shot
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            ---

            body
            """
        ),
    )
    spec = parse_dataset_spec(spec_path)
    assert spec.temporal is None


def test_parse_spec_with_temporal_block(tmp_path: Path) -> None:
    """A spec with a `temporal:` block parses it into a
    `TemporalDatasetMetadata` instance with the expected fields."""
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            name: Foo
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: annual
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            temporal:
              cadence: annual
              cover_basis: financial_year_ending
              release_id_format: "YYYY-YY"
              available_releases:
                - "2018-19"
                - "2019-20"
                - "2020-21"
              asgs_edition_by_release:
                "2018-19": 2
                "2019-20": 3
                "2020-21": 3
            ---

            body
            """
        ),
    )
    spec = parse_dataset_spec(spec_path)
    assert spec.temporal is not None
    assert spec.temporal.cadence == "annual"
    assert spec.temporal.cover_basis == "financial_year_ending"
    assert spec.temporal.available_releases == ["2018-19", "2019-20", "2020-21"]
    assert spec.temporal.asgs_edition_by_release == {
        "2018-19": 2,
        "2019-20": 3,
        "2020-21": 3,
    }


def test_parse_temporal_block_rejects_unknown_cadence(tmp_path: Path) -> None:
    """An invalid `cadence` value fails parsing loudly."""
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            name: Foo
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: annual
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            temporal:
              cadence: bogus
              cover_basis: financial_year_ending
              release_id_format: "YYYY"
            ---

            body
            """
        ),
    )
    with pytest.raises(ValueError, match="invalid dataset spec"):
        parse_dataset_spec(spec_path)


def test_parse_temporal_block_rejects_unknown_asgs_edition(tmp_path: Path) -> None:
    """`asgs_edition_by_release` values must be 1/2/3/4."""
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            name: Foo
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: annual
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            temporal:
              cadence: annual
              cover_basis: financial_year_ending
              release_id_format: "YYYY"
              asgs_edition_by_release:
                "2018": 99
            ---

            body
            """
        ),
    )
    with pytest.raises(ValueError, match="invalid dataset spec"):
        parse_dataset_spec(spec_path)


def test_existing_seifa_spec_has_temporal_block() -> None:
    """The renamed ``seifa.md`` spec includes a temporal block covering
    both the 2016 and 2021 releases."""
    repo_root = Path(__file__).resolve().parents[1]
    spec = parse_dataset_spec(repo_root / "datasets" / "seifa.md")
    assert spec.id == "seifa"
    assert spec.temporal is not None
    assert spec.temporal.cadence == "per_census"
    assert spec.temporal.asgs_edition_by_release == {"2016": 2, "2021": 3}
    assert "2016" in spec.temporal.available_releases
    assert "2021" in spec.temporal.available_releases


def test_existing_erp_spec_has_temporal_block_with_edition_transition() -> None:
    """ERP's temporal block declares the 2021→2022 ASGS edition
    transition (2021 release on Edition 2; 2022 onwards on Edition 3)."""
    repo_root = Path(__file__).resolve().parents[1]
    spec = parse_dataset_spec(repo_root / "datasets" / "erp_by_sa2.md")
    assert spec.temporal is not None
    assert spec.temporal.asgs_edition_by_release["2021"] == 2
    assert spec.temporal.asgs_edition_by_release["2022"] == 3


def test_parse_missing_required_field_raises(tmp_path: Path) -> None:
    spec_path = _write_spec(
        tmp_path,
        dedent(
            """\
            ---
            id: foo
            # name missing
            status: active
            custodian: Org
            licence: CC-BY-4.0
            update_cadence: one-shot
            geography_level: SA2
            geography_edition: 2021_ASGS_Edition_3
            geography_native: true
            join_key: sa2_code_2021
            landing_page: https://example.com
            namespace: FOO
            ---

            body
            """
        ),
    )
    with pytest.raises(ValueError, match="invalid dataset spec"):
        parse_dataset_spec(spec_path)


# ---- registry -----------------------------------------------------------


def _make_spec(**overrides: object) -> DatasetSpec:
    """Build a valid DatasetSpec for testing, with sensible defaults."""
    defaults: dict[str, object] = {
        "id": "test_ds",
        "name": "Test",
        "status": "active",
        "custodian": "Test Org",
        "licence": "CC-BY-4.0",
        "update_cadence": "one-shot",
        "geography_level": "SA2",
        "geography_edition": "2021_ASGS_Edition_3",
        "geography_native": True,
        "join_key": "sa2_code_2021",
        "landing_page": "https://example.com",
        "namespace": "TEST",
        "body": "body",
    }
    defaults.update(overrides)
    return DatasetSpec(**defaults)


def test_registry_register_and_get() -> None:
    r = Registry()
    spec = _make_spec(id="seifa", namespace="SEIFA")
    r.register_spec(spec)
    assert r.get("seifa") is spec
    assert r.list_datasets() == [spec]


def test_registry_get_unknown_raises() -> None:
    r = Registry()
    with pytest.raises(RegistryError, match="No dataset registered"):
        r.get("nope")


def test_registry_resolve_namespaced_variable() -> None:
    r = Registry()
    seifa = _make_spec(id="seifa", namespace="SEIFA")
    erp = _make_spec(id="erp_by_sa2", namespace="ERP")
    r.register_spec(seifa)
    r.register_spec(erp)

    spec, field = r.resolve_variable("SEIFA.irsd_decile")
    assert spec is seifa
    assert field == "irsd_decile"

    spec, field = r.resolve_variable("ERP.population_total")
    assert spec is erp
    assert field == "population_total"


def test_registry_resolve_gcp_table_prefix() -> None:
    """G02.foo should route to the dataset with namespace='G' (GCP)."""
    r = Registry()
    gcp = _make_spec(id="gcp", namespace="G")
    r.register_spec(gcp)

    spec, field = r.resolve_variable("G02.Median_age_persons")
    assert spec is gcp
    # GCP keeps the table id in the field, since the catalog needs it.
    assert field == "G02.Median_age_persons"


def test_registry_resolve_unknown_namespace_raises() -> None:
    r = Registry()
    r.register_spec(_make_spec(id="seifa", namespace="SEIFA"))
    with pytest.raises(RegistryError, match="No dataset registered for namespace"):
        r.resolve_variable("UNKNOWN.foo")


def test_registry_resolve_malformed_ref_raises() -> None:
    r = Registry()
    with pytest.raises(RegistryError, match="has no namespace"):
        r.resolve_variable("noperiod")
    with pytest.raises(RegistryError, match="empty namespace"):
        r.resolve_variable(".field")


def test_registry_loads_repo_specs_on_import() -> None:
    """The package-level registry instance picks up the repo's datasets/."""
    ids = {s.id for s in registry.list_datasets()}
    assert {
        "gcp",
        "seifa",
        "erp_by_sa2",
        "dss_payments",
        "abs_personal_income",
    } <= ids


def test_repo_specs_resolve_known_namespaces() -> None:
    spec, field = registry.resolve_variable("SEIFA.irsd_aus_decile")
    assert spec.id == "seifa"
    assert field == "irsd_aus_decile"

    spec, field = registry.resolve_variable("ERP.population_total")
    assert spec.id == "erp_by_sa2"
    assert field == "population_total"

    spec, field = registry.resolve_variable("G02.Median_age_persons")
    assert spec.id == "gcp"
