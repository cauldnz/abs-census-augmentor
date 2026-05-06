"""Tests for census_augment.cli."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import responses
import yaml
from typer.testing import CliRunner

from census_augment.cli import app


runner = CliRunner()


def _write_config(
    tmp_path: Path,
    variables: dict[str, str] | None = None,
    address_only: bool = False,
) -> Path:
    """Write a minimal valid config.yaml and return its path.

    By default, configures both lat/lon and address columns; pass
    ``address_only=True`` to omit the latitude/longitude pair (useful for
    tests that don't have an input CSV with coords).
    """
    cfg: dict[str, Any] = {
        "input": {
            "path": str(tmp_path / "input.csv"),
            "address_column": "address",
        },
        "output": {"path": str(tmp_path / "output.csv")},
        "geocoding": {"providers": ["nominatim"], "nominatim": {"user_agent": "test/0.1 (test@example.com)"}},
        "variables": variables or {"median_age": "G02.Median_age_persons"},
    }
    if not address_only:
        cfg["input"]["latitude_column"] = "lat"
        cfg["input"]["longitude_column"] = "lon"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return config_path


def _add_abs_mocks(
    boundaries_url: str,
    datapacks_url: str,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    """Common mocks for the boundary + DataPack downloads used by run/discover/validate --full."""
    responses.add(
        responses.GET, boundaries_url, body=fake_boundary_zip_bytes, status=200
    )
    responses.add(
        responses.GET, datapacks_url, body=fake_datapack_zip_bytes, status=200
    )


# Default base URLs from the spec / config defaults
_BOUNDARIES_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files/"
    "SA2_2021_AUST_SHP_GDA2020.zip"
)
_DATAPACKS_URL = (
    "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
    "2021_GCP_SA2_for_AUS_short-header.zip"
)


# ---- top-level help -------------------------------------------------------


def test_help_shows_command_list() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "discover", "fetch", "validate"):
        assert cmd in result.stdout


# ---- run ------------------------------------------------------------------


@responses.activate
def test_run_command_end_to_end(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    config_path = _write_config(tmp_path)
    (tmp_path / "input.csv").write_text(
        "address,lat,lon\nSydney,-33.86,151.21\n", encoding="utf-8"
    )
    _add_abs_mocks(
        _BOUNDARIES_URL,
        _DATAPACKS_URL,
        fake_boundary_zip_bytes,
        fake_datapack_zip_bytes,
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Run summary" in result.stdout
    assert "Total rows:           1" in result.stdout
    assert (tmp_path / "output.csv").exists()


def test_run_missing_config_file_fails(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["run", "--config", str(tmp_path / "nonexistent.yaml")]
    )
    assert result.exit_code != 0


def test_run_fails_when_input_path_missing_from_config(tmp_path: Path) -> None:
    """Spec §6.1: input.path is now optional (library use); CLI's run
    command must reject a config that omits it."""
    cfg = {
        "input": {"address_column": "address"},  # no path
        "output": {"path": str(tmp_path / "output.csv")},
        "geocoding": {"providers": ["nominatim"], "nominatim": {"user_agent": "test/0.1 (test@example.com)"}},
        "variables": {"median_age": "G02.Median_age_persons"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "input.path" in output


def test_run_fails_when_output_path_missing_from_config(tmp_path: Path) -> None:
    cfg = {
        "input": {
            "path": str(tmp_path / "input.csv"),
            "address_column": "address",
        },
        "output": {},  # no path
        "geocoding": {"providers": ["nominatim"], "nominatim": {"user_agent": "test/0.1 (test@example.com)"}},
        "variables": {"median_age": "G02.Median_age_persons"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = runner.invoke(app, ["run", "--config", str(config_path)])

    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "output.path" in output


# ---- discover -------------------------------------------------------------


@responses.activate
def test_discover_search_finds_matches(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "discover",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--search",
            "rent",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Median_rent_weekly" in result.stdout
    assert "G02" in result.stdout


@responses.activate
def test_discover_search_no_matches(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "discover",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--search",
            "definitely_not_a_real_term",
        ],
    )

    assert result.exit_code == 0
    assert "No matches" in result.stdout


@responses.activate
def test_discover_table_lists_columns(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "discover",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--table",
            "G02",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Selected Medians and Averages" in result.stdout
    assert "Median_age_persons" in result.stdout
    assert "Median_rent_weekly" in result.stdout


@responses.activate
def test_discover_table_unknown_includes_suggestions(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "discover",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--table",
            "G99",
        ],
    )

    assert result.exit_code == 1
    # CliRunner mixes stderr into stdout by default
    assert "G99" in result.stdout or "G99" in (result.stderr or "")


def test_discover_neither_search_nor_table_errors(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(
        app,
        ["discover", "--config", str(config_path), "--data-dir", str(tmp_path / "data")],
    )
    assert result.exit_code == 2


def test_discover_both_search_and_table_errors(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "discover",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--search",
            "x",
            "--table",
            "G02",
        ],
    )
    assert result.exit_code == 2


# ---- fetch ----------------------------------------------------------------


@responses.activate
def test_fetch_boundaries_only(
    tmp_path: Path, fake_boundary_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _BOUNDARIES_URL, body=fake_boundary_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "fetch",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--boundaries",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Boundaries:" in result.stdout
    assert (tmp_path / "data" / "boundaries").exists()


@responses.activate
def test_fetch_census_only(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "fetch",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--census",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "DataPacks:" in result.stdout
    assert (tmp_path / "data" / "census").exists()


@responses.activate
def test_fetch_both(
    tmp_path: Path,
    fake_boundary_zip_bytes: bytes,
    fake_datapack_zip_bytes: bytes,
) -> None:
    config_path = _write_config(tmp_path)
    _add_abs_mocks(
        _BOUNDARIES_URL,
        _DATAPACKS_URL,
        fake_boundary_zip_bytes,
        fake_datapack_zip_bytes,
    )

    result = runner.invoke(
        app,
        [
            "fetch",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--boundaries",
            "--census",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Boundaries:" in result.stdout
    assert "DataPacks:" in result.stdout


def test_fetch_with_no_target_errors(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    result = runner.invoke(
        app,
        ["fetch", "--config", str(config_path), "--data-dir", str(tmp_path / "data")],
    )
    assert result.exit_code == 2


# ---- validate -------------------------------------------------------------


def test_validate_structural_only_succeeds(tmp_path: Path) -> None:
    """Without --full, validate needs no HTTP and should pass for a valid config."""
    config_path = _write_config(tmp_path)

    result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert "structurally valid" in result.stdout


def test_validate_invalid_config_fails(tmp_path: Path) -> None:
    """An invalid YAML config should exit non-zero with a useful message."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                # Missing input section entirely - structural failure
                "output": {"path": "out.csv"},
                "geocoding": {"providers": ["nominatim"], "nominatim": {"user_agent": "x/1 (a@b.c)"}},
                "variables": {"foo": "G01.Tot_P_M"},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code != 0


@responses.activate
def test_validate_full_passes_with_valid_variables(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(tmp_path)
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--full",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "structurally valid" in result.stdout
    assert "valid against DataPack" in result.stdout


@responses.activate
def test_validate_full_fails_with_unknown_variable_ref(
    tmp_path: Path, fake_datapack_zip_bytes: bytes
) -> None:
    config_path = _write_config(
        tmp_path,
        variables={"bad": "G99.does_not_exist"},
    )
    responses.add(
        responses.GET, _DATAPACKS_URL, body=fake_datapack_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "validate",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--full",
        ],
    )

    assert result.exit_code == 1
    # "G99" might be in stdout or stderr depending on CliRunner config
    output = result.stdout + (result.stderr or "")
    assert "G99" in output


# ---- fetch --gnaf (Phase 7) ---------------------------------------------

_MB_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files/"
    "MB_2021_AUST_SHP_GDA2020.zip"
)


@responses.activate
def test_fetch_gnaf_downloads_mb_correspondence_and_prints_attribution(
    tmp_path: Path,
    fake_mb_correspondence_zip_bytes: bytes,
    fake_gnaf_data_dir: Path,
) -> None:
    """``fetch --gnaf`` must print the Geoscape attribution string and
    download the MB correspondence shapefile alongside G-NAF (the §7.3
    fast path needs both)."""
    # Pre-populate the gnaf cache so fetch() resolves without S3 download.
    config_path = _write_config(tmp_path, address_only=True)

    responses.add(
        responses.GET, _MB_URL, body=fake_mb_correspondence_zip_bytes, status=200
    )

    result = runner.invoke(
        app,
        [
            "fetch",
            "--config",
            str(config_path),
            "--data-dir",
            str(fake_gnaf_data_dir),  # already has gnaf/202602/addresses.parquet
            "--gnaf",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Geoscape" in result.stdout
    assert "G-NAF:" in result.stdout
    assert "MB lookup:" in result.stdout


def test_fetch_gnaf_reports_missing_cache_clearly(
    tmp_path: Path,
    fake_mb_correspondence_zip_bytes: bytes,
) -> None:
    """When G-NAF can't resolve (no cached release *and* no releases on S3)
    the CLI exits non-zero with a useful message — but doesn't surface a
    stack trace."""
    import boto3
    from moto import mock_aws

    config_path = _write_config(tmp_path, address_only=True)

    with mock_aws():
        # Set up an empty bucket at the default s3_base_url's location.
        # Listing succeeds but returns no geoscape-* prefixes -> "no releases" error.
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="minus34.com", ObjectOwnership="ObjectWriter")
        s3.delete_public_access_block(Bucket="minus34.com")
        s3.put_bucket_acl(Bucket="minus34.com", ACL="public-read")

        result = runner.invoke(
            app,
            [
                "fetch",
                "--config",
                str(config_path),
                "--data-dir",
                str(tmp_path / "empty_data"),  # no cache prepopulated
                "--gnaf",
            ],
        )

    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "G-NAF" in output


# ---- gnaf-info ----------------------------------------------------------


def test_gnaf_info_when_provider_not_enabled(tmp_path: Path) -> None:
    """``gnaf-info`` exits with a useful message when G-NAF isn't even
    in providers (the user hasn't opted in)."""
    config_path = _write_config(tmp_path)  # nominatim-only by default
    result = runner.invoke(
        app, ["gnaf-info", "--config", str(config_path)]
    )
    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "G-NAF is not in" in output


def test_gnaf_info_with_cached_release(
    tmp_path: Path, fake_gnaf_data_dir: Path
) -> None:
    """A populated cache produces a tidy info dump."""
    cfg: dict[str, Any] = {
        "input": {
            "path": str(tmp_path / "input.csv"),
            "address_column": "address",
        },
        "output": {"path": str(tmp_path / "output.csv")},
        "geocoding": {
            "providers": ["gnaf", "nominatim"],
            "nominatim": {"user_agent": "test/0.1 (test@example.com)"},
        },
        "variables": {"median_age": "G02.Median_age_persons"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "gnaf-info",
            "--config",
            str(config_path),
            "--data-dir",
            str(fake_gnaf_data_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Mode:" in result.stdout
    assert "cache" in result.stdout
    assert "Resolved release: 202602" in result.stdout
    assert "Cached size:" in result.stdout


def test_gnaf_info_with_no_cached_release(tmp_path: Path) -> None:
    """When G-NAF is configured but nothing's cached, the command still
    succeeds and points the user at the fix."""
    cfg: dict[str, Any] = {
        "input": {
            "path": str(tmp_path / "input.csv"),
            "address_column": "address",
        },
        "output": {"path": str(tmp_path / "output.csv")},
        "geocoding": {
            "providers": ["gnaf", "nominatim"],
            "nominatim": {"user_agent": "test/0.1 (test@example.com)"},
        },
        "variables": {"median_age": "G02.Median_age_persons"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "gnaf-info",
            "--config",
            str(config_path),
            "--data-dir",
            str(tmp_path / "empty"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "<not cached>" in result.stdout
    assert "fetch --gnaf" in result.stdout
