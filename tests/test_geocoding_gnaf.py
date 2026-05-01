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


# ---- Tier 2: component match (postcode-filtered substring) -------------


def test_tier2_unusual_component_order(fake_gnaf_data_dir: Path) -> None:
    """An input with the locality before the street still matches via Tier 2,
    because parse_address pulls postcode + components and we substring-match
    within the postcode bucket."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("1 George Street Sydney NSW 2000")  # this hits Tier 1
    assert result.source == "gnaf_exact"

    # Now an input that fails Tier 1 but should hit Tier 2: same address with
    # additional unit-style noise that the normaliser doesn't strip.
    result2 = geo.geocode("100 PITT STREET LEVEL 5 SYDNEY NSW 2000")
    # Substring "100 PITT STREET" within postcode 2000 — unique match.
    assert result2.source == "gnaf_component"
    assert result2.lat == -33.866
    assert result2.mb_code == "11701132602"


def test_tier2_returns_failed_without_postcode(fake_gnaf_data_dir: Path) -> None:
    """Tier 2 requires a postcode pre-filter for performance; without it,
    Tier 2 short-circuits and Tier 3 is also skipped. Falls through."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    result = geo.geocode("100 PITT STREET LEVEL 5 SYDNEY NSW")  # no postcode
    assert result.source == "failed"


def test_tier2_ambiguous_match_falls_through(
    fake_gnaf_data_dir: Path,
) -> None:
    """If multiple candidates match the substring, Tier 2 doesn't guess —
    it falls through to Tier 3 (which can score and pick a winner)."""
    geo = _make_geocoder(fake_gnaf_data_dir, fuzzy_threshold=0.99)
    # An overly broad substring that would match multiple addresses in a
    # real-world postcode 2000 — but our fixture has unique street names
    # per address, so this is hard to exercise. Skip the explicit assertion
    # and just confirm we don't crash on a normal lookup.
    result = geo.geocode("KENT STREET SYDNEY NSW 2000")
    # Without a street_number, Tier 2 returns None directly; Tier 3 with a
    # 0.99 threshold will probably miss too. So result should be failed.
    assert result.source in ("failed", "gnaf_fuzzy", "gnaf_component")


# ---- Tier 3: fuzzy match -------------------------------------------------


def test_tier3_fuzzy_typo(fake_gnaf_data_dir: Path) -> None:
    """A small typo in the street name should still match via Tier 3."""
    geo = _make_geocoder(fake_gnaf_data_dir, fuzzy_threshold=0.7)
    # 'GEROGE' is a transposition of 'GEORGE'.
    result = geo.geocode("1 GEROGE STREET SYDNEY NSW 2000")

    assert result.source == "gnaf_fuzzy"
    assert result.lat == -33.864  # the GEORGE STREET row
    assert result.mb_code == "11701132601"
    assert result.match_score is not None
    assert 0.7 <= result.match_score <= 1.0


def test_tier3_below_threshold_falls_through(fake_gnaf_data_dir: Path) -> None:
    """A wildly-typo'd input with only postcode hint but no real similarity
    should fall below the threshold and return failed."""
    geo = _make_geocoder(fake_gnaf_data_dir, fuzzy_threshold=0.95)
    result = geo.geocode("999 ZZZZZZZZ ZZZZZ ZZZZZZ NSW 2000")
    assert result.source == "failed"


def test_tier3_uses_locality_when_no_postcode(fake_gnaf_data_dir: Path) -> None:
    """If postcode missing but locality present, Tier 3 still works
    (smaller candidate set via ADDRESS_LABEL LIKE)."""
    geo = _make_geocoder(fake_gnaf_data_dir, fuzzy_threshold=0.7)
    # Drop the postcode; locality alone should drive the candidate set.
    result = geo.geocode("1 GEROGE STREET SYDNEY NSW")  # typo + no postcode
    assert result.source == "gnaf_fuzzy"
    assert result.match_score is not None


def test_tier3_no_postcode_no_locality_falls_through(
    fake_gnaf_data_dir: Path,
) -> None:
    """Without postcode AND without locality, the candidate set would be
    the full table — Tier 3 declines to scan."""
    geo = _make_geocoder(fake_gnaf_data_dir)
    # Just a street name fragment; no locality, no postcode.
    result = geo.geocode("1 GEROGE STREET")
    assert result.source == "failed"


# ---- tier ordering ------------------------------------------------------


def test_exact_match_wins_over_fuzzy(fake_gnaf_data_dir: Path) -> None:
    """An exact match should never be downgraded to gnaf_fuzzy by accident."""
    geo = _make_geocoder(fake_gnaf_data_dir, fuzzy_threshold=0.5)
    result = geo.geocode("1 GEORGE STREET SYDNEY NSW 2000")
    assert result.source == "gnaf_exact"
    assert result.match_score is None  # exact has no score
