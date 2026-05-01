"""Tests for census_augment.config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from census_augment.config import Config, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _base_config() -> dict[str, Any]:
    return {
        "input": {
            "path": "data/locations.csv",
            "address_column": "address",
        },
        "output": {
            "path": "out/locations_enriched.csv",
        },
        "geocoding": {
            "providers": ["nominatim"],
            "nominatim": {
                "user_agent": "census-augment-test/0.1 (test@example.com)",
            },
        },
        "variables": {
            "median_age": "G02.Median_age_persons",
        },
    }


def _write(tmp_path: Path, cfg: dict[str, Any]) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


# ---------- valid config ----------


def test_minimal_valid_config_loads(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _base_config()))
    assert isinstance(cfg, Config)
    assert cfg.input.path == Path("data/locations.csv")
    assert cfg.input.address_column == "address"
    assert cfg.input.latitude_column is None
    assert cfg.input.longitude_column is None
    assert cfg.output.prefix == "sa2_"
    assert cfg.census.year == 2021
    assert cfg.census.level == "SA2"
    assert cfg.census.profile == "GCP"
    assert cfg.census.asgs_edition == 3
    assert cfg.geocoding.providers == ["nominatim"]
    assert cfg.geocoding.nominatim.rate_limit_per_second == 1.0
    assert cfg.geocoding.cache_enabled is True
    assert cfg.variables == {"median_age": "G02.Median_age_persons"}


def test_example_config_loads() -> None:
    """The shipped config.example.yaml must validate cleanly."""
    cfg = load_config(PROJECT_ROOT / "config.example.yaml")
    assert cfg.census.year == 2021
    assert cfg.census.region == "AUS"
    assert "median_age" in cfg.variables
    assert "total_population" in cfg.variables
    assert cfg.output.prefix == "sa2_"
    assert cfg.geocoding.providers == ["gnaf", "nominatim"]
    assert cfg.geocoding.gnaf.mode == "cache"
    assert cfg.geocoding.gnaf.release == "latest"
    assert cfg.geocoding.gnaf.fuzzy_threshold == 0.85
    assert cfg.geocoding.nominatim.user_agent is not None


# ---------- missing required fields ----------


def test_missing_input_section_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["input"]
    with pytest.raises(ValidationError, match="input"):
        load_config(_write(tmp_path, cfg))


def test_missing_output_section_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["output"]
    with pytest.raises(ValidationError, match="output"):
        load_config(_write(tmp_path, cfg))


def test_missing_geocoding_section_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["geocoding"]
    with pytest.raises(ValidationError, match="geocoding"):
        load_config(_write(tmp_path, cfg))


def test_missing_nominatim_user_agent_fails_when_nominatim_in_providers(
    tmp_path: Path,
) -> None:
    cfg = _base_config()
    del cfg["geocoding"]["nominatim"]["user_agent"]
    with pytest.raises(ValidationError, match="user_agent"):
        load_config(_write(tmp_path, cfg))


def test_nominatim_user_agent_optional_when_only_gnaf_configured(
    tmp_path: Path,
) -> None:
    """G-NAF-only setups (offline) don't need a Nominatim User-Agent."""
    cfg = _base_config()
    cfg["geocoding"] = {"providers": ["gnaf"]}
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.geocoding.providers == ["gnaf"]
    assert loaded.geocoding.nominatim.user_agent is None


def test_geocoding_providers_default_to_gnaf_then_nominatim(
    tmp_path: Path,
) -> None:
    cfg = _base_config()
    # Drop the explicit providers list — default should be [gnaf, nominatim].
    del cfg["geocoding"]["providers"]
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.geocoding.providers == ["gnaf", "nominatim"]


def test_empty_providers_list_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = []
    with pytest.raises(ValidationError, match="at least one"):
        load_config(_write(tmp_path, cfg))


def test_duplicate_provider_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = ["nominatim", "nominatim"]
    with pytest.raises(ValidationError, match="duplicates"):
        load_config(_write(tmp_path, cfg))


def test_unknown_provider_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = ["google_maps"]
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, cfg))


def test_gnaf_release_format_validated(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = ["gnaf"]
    cfg["geocoding"]["gnaf"] = {"release": "Q1-2026"}
    with pytest.raises(ValidationError, match="YYYYMM"):
        load_config(_write(tmp_path, cfg))


def test_gnaf_release_explicit_yyyymm_accepted(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = ["gnaf"]
    cfg["geocoding"]["gnaf"] = {"release": "202602"}
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.geocoding.gnaf.release == "202602"


def test_gnaf_fuzzy_threshold_out_of_range_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["geocoding"]["gnaf"] = {"fuzzy_threshold": 1.5}
    with pytest.raises(ValidationError, match="fuzzy_threshold"):
        load_config(_write(tmp_path, cfg))


def test_datum_mismatch_emits_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _base_config()
    cfg["geocoding"]["providers"] = ["gnaf", "nominatim"]
    cfg["geocoding"]["gnaf"] = {"datum": "GDA94"}  # census defaults to GDA2020
    with caplog.at_level("WARNING", logger="census_augment.config"):
        load_config(_write(tmp_path, cfg))
    assert any("Datum mismatch" in rec.message for rec in caplog.records)


def test_no_warning_when_datums_match(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _base_config()  # gnaf default datum=GDA2020 matches census default
    cfg["geocoding"]["providers"] = ["gnaf", "nominatim"]
    with caplog.at_level("WARNING", logger="census_augment.config"):
        load_config(_write(tmp_path, cfg))
    assert not any("Datum mismatch" in rec.message for rec in caplog.records)


def test_missing_variables_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["variables"]
    with pytest.raises(ValidationError, match="variables"):
        load_config(_write(tmp_path, cfg))


def test_empty_variables_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["variables"] = {}
    with pytest.raises(ValidationError, match="at least one"):
        load_config(_write(tmp_path, cfg))


def test_input_with_no_locator_columns_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["input"]["address_column"]
    with pytest.raises(ValidationError, match="address_column"):
        load_config(_write(tmp_path, cfg))


def test_input_lat_without_lon_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["input"]["latitude_column"] = "lat"
    with pytest.raises(ValidationError, match=r"latitude_column|longitude_column"):
        load_config(_write(tmp_path, cfg))


def test_input_lon_without_lat_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["input"]["address_column"]
    cfg["input"]["longitude_column"] = "lon"
    with pytest.raises(ValidationError, match=r"latitude_column|longitude_column"):
        load_config(_write(tmp_path, cfg))


def test_input_path_optional_for_library_use(tmp_path: Path) -> None:
    """input.path is optional (CLI run command requires it; library doesn't)."""
    cfg = _base_config()
    del cfg["input"]["path"]
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.input.path is None
    assert loaded.input.address_column == "address"


def test_output_path_optional_for_library_use(tmp_path: Path) -> None:
    """output.path is optional (CLI run command requires it; library doesn't)."""
    cfg = _base_config()
    del cfg["output"]["path"]
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.output.path is None
    assert loaded.output.prefix == "sa2_"


def test_both_paths_optional_at_once(tmp_path: Path) -> None:
    """A pure-library config with neither path is valid."""
    cfg = _base_config()
    del cfg["input"]["path"]
    del cfg["output"]["path"]
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.input.path is None
    assert loaded.output.path is None


def test_input_with_only_lat_lon_is_valid(tmp_path: Path) -> None:
    cfg = _base_config()
    del cfg["input"]["address_column"]
    cfg["input"]["latitude_column"] = "lat"
    cfg["input"]["longitude_column"] = "lon"
    loaded = load_config(_write(tmp_path, cfg))
    assert loaded.input.address_column is None
    assert loaded.input.latitude_column == "lat"
    assert loaded.input.longitude_column == "lon"


# ---------- invalid friendly names ----------


@pytest.mark.parametrize(
    "bad_name",
    [
        "Median_age",
        "1median",
        "median age",
        "median-age",
        "_median",
        "",
    ],
)
def test_invalid_friendly_name_fails(tmp_path: Path, bad_name: str) -> None:
    cfg = _base_config()
    cfg["variables"] = {bad_name: "G02.Median_age_persons"}
    with pytest.raises(ValidationError, match="invalid"):
        load_config(_write(tmp_path, cfg))


# ---------- bad variable references ----------


@pytest.mark.parametrize(
    "bad_ref",
    [
        "G02",
        "G02.Median.persons",
        "G02 Median_age",
        ".Median_age",
        "G02.",
        "1G02.Median_age",
    ],
)
def test_bad_variable_reference_fails(tmp_path: Path, bad_ref: str) -> None:
    cfg = _base_config()
    cfg["variables"] = {"median_age": bad_ref}
    with pytest.raises(ValidationError, match=r"median_age|invalid"):
        load_config(_write(tmp_path, cfg))


# ---------- extras forbidden ----------


def test_unknown_top_level_key_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["unexpected"] = "oops"
    with pytest.raises(ValidationError, match="unexpected"):
        load_config(_write(tmp_path, cfg))


def test_unknown_nested_key_fails(tmp_path: Path) -> None:
    cfg = _base_config()
    cfg["census"] = {"region": "NSW", "typo_field": "boom"}
    with pytest.raises(ValidationError, match="typo_field"):
        load_config(_write(tmp_path, cfg))


# ---------- file-loading edge cases ----------


def test_empty_yaml_file_fails(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_config(p)


def test_non_mapping_yaml_fails(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_config(p)
