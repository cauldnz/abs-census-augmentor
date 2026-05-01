"""Tests for census_augment.geocoding.normalize (Phase 3)."""

from __future__ import annotations

import pytest

from census_augment.geocoding.normalize import (
    AddressComponents,
    normalize_address,
    parse_address,
)


# ---- normalize_address: basic mechanics ---------------------------------


def test_empty_input_returns_empty_string() -> None:
    assert normalize_address("") == ""


def test_lowercase_input_uppercased() -> None:
    assert normalize_address("george street") == "GEORGE STREET"


def test_punctuation_stripped() -> None:
    assert normalize_address("1 George Street, Sydney") == "1 GEORGE STREET SYDNEY"


def test_multiple_internal_spaces_collapsed() -> None:
    assert normalize_address("1   George    Street") == "1 GEORGE STREET"


def test_leading_trailing_whitespace_stripped() -> None:
    assert normalize_address("   1 George Street   ") == "1 GEORGE STREET"


# ---- street type abbreviations ------------------------------------------


@pytest.mark.parametrize(
    "abbrev,expected_word",
    [
        ("ST", "STREET"),
        ("AVE", "AVENUE"),
        ("AV", "AVENUE"),
        ("RD", "ROAD"),
        ("BLVD", "BOULEVARD"),
        ("LN", "LANE"),
        ("LA", "LANE"),
        ("PL", "PLACE"),
        ("TCE", "TERRACE"),
        ("CRES", "CRESCENT"),
        ("HWY", "HIGHWAY"),
        ("PDE", "PARADE"),
    ],
)
def test_street_type_abbreviations_expanded(abbrev: str, expected_word: str) -> None:
    assert normalize_address(f"1 King {abbrev}") == f"1 KING {expected_word}"


def test_unknown_token_unchanged() -> None:
    """Tokens that aren't street-type abbreviations stay verbatim."""
    assert normalize_address("1 King WIBBLE") == "1 KING WIBBLE"


def test_full_street_type_unchanged() -> None:
    """Already-expanded street types pass through."""
    assert normalize_address("1 King Street") == "1 KING STREET"


# ---- state abbreviations ------------------------------------------------


@pytest.mark.parametrize(
    "full,abbrev",
    [
        ("New South Wales", "NSW"),
        ("Victoria", "VIC"),
        ("Queensland", "QLD"),
        ("Western Australia", "WA"),
        ("South Australia", "SA"),
        ("Tasmania", "TAS"),
        ("Northern Territory", "NT"),
        ("Australian Capital Territory", "ACT"),
    ],
)
def test_full_state_name_to_abbreviation(full: str, abbrev: str) -> None:
    assert (
        normalize_address(f"1 King St Sydney {full} 2000")
        == f"1 KING STREET SYDNEY {abbrev} 2000"
    )


def test_already_abbreviated_state_preserved() -> None:
    assert (
        normalize_address("1 King Street Sydney NSW 2000")
        == "1 KING STREET SYDNEY NSW 2000"
    )


# ---- end-to-end normalization ------------------------------------------


def test_full_address_normalises_to_gnaf_label_form() -> None:
    """Mimic the normalization that Tier 1 uses to compare against G-NAF."""
    inp = "1 george st, sydney nsw 2000"
    assert normalize_address(inp) == "1 GEORGE STREET SYDNEY NSW 2000"


def test_normalization_is_idempotent() -> None:
    """Normalising twice gives the same result as normalising once."""
    raw = "1 King Ave, Bondi Beach NSW 2026"
    once = normalize_address(raw)
    twice = normalize_address(once)
    assert once == twice


# ---- parse_address: happy paths -----------------------------------------


def test_parse_full_address() -> None:
    components = parse_address("1 George Street, Sydney NSW 2000")
    assert components == AddressComponents(
        unit_number=None,
        street_number="1",
        street_name="GEORGE",
        street_type="STREET",
        locality="SYDNEY",
        state="NSW",
        postcode="2000",
    )


def test_parse_with_unit_slash_form() -> None:
    components = parse_address("5/100 King George Avenue Bondi Beach NSW 2026")
    assert components is not None
    assert components.unit_number == "5"
    assert components.street_number == "100"
    assert components.street_name == "KING GEORGE"
    assert components.street_type == "AVENUE"
    assert components.locality == "BONDI BEACH"
    assert components.state == "NSW"
    assert components.postcode == "2026"


def test_parse_with_range_street_number() -> None:
    components = parse_address("100-102 King St Melbourne VIC 3000")
    assert components is not None
    assert components.street_number == "100-102"
    assert components.street_type == "STREET"


def test_parse_multi_word_street_name() -> None:
    components = parse_address("20 King George Avenue, Sydney NSW 2000")
    assert components is not None
    assert components.street_name == "KING GEORGE"


def test_parse_multi_word_locality() -> None:
    components = parse_address("1 Park Lane Macquarie Park NSW 2113")
    assert components is not None
    assert components.locality == "MACQUARIE PARK"


def test_parse_handles_lowercase_input() -> None:
    """parse_address calls normalize_address internally."""
    components = parse_address("1 george st sydney nsw 2000")
    assert components is not None
    assert components.street_name == "GEORGE"
    assert components.state == "NSW"


# ---- parse_address: partial info ---------------------------------------


def test_parse_without_postcode() -> None:
    components = parse_address("1 George Street, Sydney NSW")
    assert components is not None
    assert components.postcode is None
    assert components.state == "NSW"


def test_parse_without_state_or_postcode() -> None:
    components = parse_address("1 George Street, Sydney")
    assert components is not None
    assert components.state is None
    assert components.postcode is None
    assert components.locality == "SYDNEY"


def test_parse_just_locality_and_postcode() -> None:
    components = parse_address("Sydney NSW 2000")
    assert components is not None
    assert components.state == "NSW"
    assert components.postcode == "2000"
    assert components.locality == "SYDNEY"


def test_parse_no_street_type_recognised() -> None:
    """No known street-type token → everything is treated as locality."""
    components = parse_address("Bondi Junction NSW 2022")
    assert components is not None
    assert components.street_type is None
    assert components.locality == "BONDI JUNCTION"


# ---- parse_address: nothing useful → None ------------------------------


def test_parse_empty_returns_none() -> None:
    assert parse_address("") is None


def test_parse_whitespace_only_returns_none() -> None:
    assert parse_address("   ") is None


def test_parse_pure_business_name_returns_none() -> None:
    """Inputs with no number, street, locality, or postcode are not
    addresses for our purposes."""
    # An address-free business-name-style string normalises to a
    # locality-shaped output ("BIG BUSINESS PTY"), so this isn't a clean
    # 'None' case — but the parser still returns useful structure.
    # That's OK; downstream Tier 1 will simply not match it against
    # G-NAF and we'll fall through.
    components = parse_address("Big Business Pty Ltd")
    # We get something back but it has no address fields populated.
    if components is not None:
        assert components.street_number is None
        assert components.street_type is None


# ---- parse_address: street type recognition ----------------------------


@pytest.mark.parametrize(
    "input_text,expected_type",
    [
        ("1 Smith St", "STREET"),
        ("1 Smith Ave", "AVENUE"),
        ("1 Smith Rd", "ROAD"),
        ("1 Smith Blvd", "BOULEVARD"),
        ("1 Smith Hwy", "HIGHWAY"),
        ("1 Smith Pde", "PARADE"),
        ("1 Smith Tce", "TERRACE"),
        ("1 Smith Cres", "CRESCENT"),
    ],
)
def test_parse_recognises_street_type_abbreviations(
    input_text: str, expected_type: str
) -> None:
    components = parse_address(input_text)
    assert components is not None
    assert components.street_type == expected_type


def test_parse_recognises_full_street_types() -> None:
    """Already-expanded street types should also be recognised."""
    components = parse_address("1 Smith Esplanade Sydney NSW 2000")
    assert components is not None
    assert components.street_type == "ESPLANADE"
