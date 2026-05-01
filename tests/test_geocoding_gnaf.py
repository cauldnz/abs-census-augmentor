"""Tests for census_augment.geocoding.gnaf (Phase 4a — Tier 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from census_augment.data_sources.gnaf import GnafDataSource
from census_augment.geocoding.gnaf import GnafGeocoder


# ---- helpers -------------------------------------------------------------


def _make_geocoder(
    fake_gnaf_data_dir: Path, fuzzy_threshold: float = 0.85
) -> GnafGeocoder:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    return GnafGeocoder(data_source=ds, fuzzy_threshold=fuzzy_threshold)


# ---- constructor validation ---------------------------------------------


def test_invalid_fuzzy_threshold_below_zero(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    with pytest.raises(ValueError, match="fuzzy_threshold"):
        GnafGeocoder(data_source=ds, fuzzy_threshold=-0.1)


def test_invalid_fuzzy_threshold_above_one(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    with pytest.raises(ValueError, match="fuzzy_threshold"):
        GnafGeocoder(data_source=ds, fuzzy_threshold=1.1)


# ---- Tier 1: exact match -------------------------------------------------


def test_tier1_exact_match_returns_gnaf_exact(fake_gnaf_data_dir: Path) -> None:
    """An input that matches an ADDRESS_LABEL verbatim returns gnaf_exact."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("1 GEORGE STREET SYDNEY NSW 2000")

    assert result.is_success
    assert result.source == "gnaf_exact"
    assert result.provider == "gnaf"
    assert result.lat == -33.864
    assert result.lon == 151.211
    assert result.mb_code == "11701132601"
    assert result.match_score is None  # not a fuzzy match


def test_tier1_match_via_normalisation(fake_gnaf_data_dir: Path) -> None:
    """Inputs that DON'T match verbatim but normalise to a hit still match.

    The fixture has '1 GEORGE STREET SYDNEY NSW 2000'. The input here is
    lowercase with abbreviations and punctuation — normalize_address
    should produce the same string.
    """
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("1 george st, sydney nsw 2000")

    assert result.is_success
    assert result.source == "gnaf_exact"
    assert result.address_normalized == "1 GEORGE STREET SYDNEY NSW 2000"


def test_tier1_match_includes_mb_code_for_sa2_fast_path(
    fake_gnaf_data_dir: Path,
) -> None:
    """The MB_CODE field is the §7.3 fast-path key — verify it's populated."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("100 PITT STREET SYDNEY NSW 2000")

    assert result.is_success
    assert result.mb_code == "11701132602"


def test_tier1_miss_returns_failed(fake_gnaf_data_dir: Path) -> None:
    """An address not in G-NAF returns a failed result, allowing the
    pipeline to fall through to the next provider (Nominatim)."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("999 NOWHERE LANE NULLVILLE XX 9999")

    assert not result.is_success
    assert result.source == "failed"
    assert result.lat is None
    assert result.lon is None
    assert result.mb_code is None


def test_empty_input_returns_failed(fake_gnaf_data_dir: Path) -> None:
    """Defensive: empty input doesn't crash; just fails."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("")
    assert not result.is_success
    assert result.source == "failed"


def test_match_returns_normalised_form_in_result(
    fake_gnaf_data_dir: Path,
) -> None:
    """address_normalized field reflects what we actually matched against."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("100 Pitt Street, Sydney NSW 2000")
    assert result.address_normalized == "100 PITT STREET SYDNEY NSW 2000"


def test_provider_is_gnaf_for_both_hit_and_miss(
    fake_gnaf_data_dir: Path,
) -> None:
    geo = _make_geocoder(fake_gnaf_data_dir)
    hit = geo.geocode("1 GEORGE STREET SYDNEY NSW 2000")
    miss = geo.geocode("999 BOGUS LANE")
    assert hit.provider == "gnaf"
    assert miss.provider == "gnaf"


def test_raw_response_includes_full_gnaf_row(fake_gnaf_data_dir: Path) -> None:
    """raw_response carries the underlying G-NAF row for debugging /
    downstream inspection."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("1 GEORGE STREET SYDNEY NSW 2000")
    assert result.raw_response is not None
    assert result.raw_response["ADDRESS_DETAIL_PID"] == "GANSW000000001"
    assert result.raw_response["MB_CODE"] == "11701132601"


def test_address_input_preserves_user_input(fake_gnaf_data_dir: Path) -> None:
    """address_input is the original (un-normalised) string, useful for
    audit trails and downstream display."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    inp = "1 george street, sydney nsw 2000"
    result = geo.geocode(inp)
    assert result.address_input == inp


# ---- Phase 4b sentinel: Tier 2/3 not yet implemented (will become real
#      tests once those tiers land in a follow-up commit) ---------------


def test_input_that_only_tier2_could_match_falls_through_for_now(
    fake_gnaf_data_dir: Path,
) -> None:
    """An input that wouldn't match Tier 1 verbatim (e.g. an unusual
    component order that needs Tier 2 parsing) falls through with
    'failed' until Tier 2 lands. This test guards against accidental
    Tier 2 implementation slipping in here without test coverage."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    # Unusual component order — would need Tier 2 to match.
    result = geo.geocode("Sydney, 1 George Street, NSW 2000")
    assert result.source == "failed"
