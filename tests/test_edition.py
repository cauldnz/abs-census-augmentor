"""Tests for census_augment.data_sources._edition (BoundaryEditionSpec factories)."""

from __future__ import annotations

import pytest

from census_augment.data_sources._edition import (
    EDITION_3_BASE_URL,
    BoundaryEditionSpec,
    edition_2_spec,
    edition_3_spec,
    edition_spec_for,
)


# ---- edition_3_spec ----------------------------------------------------------


def test_edition_3_default_is_gda2020() -> None:
    spec = edition_3_spec()
    assert isinstance(spec, BoundaryEditionSpec)
    assert spec.edition == 3
    assert spec.year == 2021
    assert spec.datum == "GDA2020"
    assert spec.sa2_zip_filename == "SA2_2021_AUST_SHP_GDA2020.zip"
    assert spec.sa2_code_column == "SA2_CODE21"
    assert spec.sa2_name_column == "SA2_NAME21"


def test_edition_3_gda94() -> None:
    spec = edition_3_spec(datum="GDA94")
    assert spec.datum == "GDA94"
    assert spec.sa2_zip_filename == "SA2_2021_AUST_SHP_GDA94.zip"
    assert spec.sa2_download_url.endswith("SA2_2021_AUST_SHP_GDA94.zip")


def test_edition_3_uses_default_base_url() -> None:
    spec = edition_3_spec()
    assert spec.sa2_download_url.startswith(EDITION_3_BASE_URL)
    assert spec.sa2_download_url.endswith("/SA2_2021_AUST_SHP_GDA2020.zip")


def test_edition_3_custom_base_url() -> None:
    spec = edition_3_spec(base_url="https://example.test/boundaries")
    assert spec.sa2_download_url == "https://example.test/boundaries/SA2_2021_AUST_SHP_GDA2020.zip"


def test_edition_3_base_url_trailing_slash_normalised() -> None:
    spec = edition_3_spec(base_url="https://example.test/boundaries/")
    assert spec.sa2_download_url == "https://example.test/boundaries/SA2_2021_AUST_SHP_GDA2020.zip"


# ---- edition_2_spec ----------------------------------------------------------


def test_edition_2_is_gda94_2016() -> None:
    spec = edition_2_spec()
    assert spec.edition == 2
    assert spec.year == 2016
    assert spec.datum == "GDA94"


def test_edition_2_sa2_filename_matches_abs_pattern() -> None:
    spec = edition_2_spec()
    assert spec.sa2_zip_filename == "1270055001_sa2_2016_aust_shape.zip"


def test_edition_2_sa2_url_is_openagent_form() -> None:
    """Edition 2 URL is the ABS Lotus Notes openagent query string.

    Checked here so any future code change that reformats the URL fails
    loudly — the openagent UNID is part of the contract.
    """
    spec = edition_2_spec()
    assert spec.sa2_download_url.startswith(
        "https://www.ausstats.abs.gov.au/ausstats/subscriber.nsf/log?openagent"
    )
    assert "1270055001_sa2_2016_aust_shape.zip" in spec.sa2_download_url


def test_edition_2_column_names_match_2016_dbf_convention() -> None:
    spec = edition_2_spec()
    assert spec.sa2_code_column == "SA2_MAIN16"
    assert spec.sa2_name_column == "SA2_NAME16"


# ---- edition_spec_for --------------------------------------------------------


def test_edition_spec_for_2021_gda2020() -> None:
    spec = edition_spec_for(year=2021, datum="GDA2020")
    assert spec.edition == 3
    assert spec.datum == "GDA2020"


def test_edition_spec_for_2021_gda94() -> None:
    spec = edition_spec_for(year=2021, datum="GDA94")
    assert spec.edition == 3
    assert spec.datum == "GDA94"
    assert spec.sa2_zip_filename == "SA2_2021_AUST_SHP_GDA94.zip"


def test_edition_spec_for_2016_gda94() -> None:
    spec = edition_spec_for(year=2016, datum="GDA94")
    assert spec.edition == 2
    assert spec.datum == "GDA94"


def test_edition_spec_for_2016_gda2020_rejected() -> None:
    """ABS never published 2016 boundaries in GDA2020 — the combo is invalid."""
    with pytest.raises(ValueError, match="GDA94"):
        edition_spec_for(year=2016, datum="GDA2020")


def test_edition_spec_for_passes_through_base_url() -> None:
    spec = edition_spec_for(year=2021, datum="GDA2020", base_url="https://mock.test/x")
    assert spec.sa2_download_url == "https://mock.test/x/SA2_2021_AUST_SHP_GDA2020.zip"


def test_edition_spec_for_2016_ignores_base_url() -> None:
    """Edition 2's URL is fixed — passing base_url shouldn't change it."""
    spec = edition_spec_for(year=2016, datum="GDA94", base_url="https://mock.test/x")
    assert "mock.test" not in spec.sa2_download_url
    assert "1270055001_sa2_2016_aust_shape.zip" in spec.sa2_download_url


def test_spec_is_frozen() -> None:
    """BoundaryEditionSpec is a frozen dataclass — accidental mutation raises."""
    spec = edition_3_spec()
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        spec.datum = "GDA94"  # type: ignore[misc]
