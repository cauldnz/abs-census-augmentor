"""AU-specific address normaliser and component parser (spec §7.2, §19.3).

Pure functions used by the G-NAF geocoder for Tier 1 (exact match) and
Tier 2 (component match).

Per spec §14 #24 / §13, this is a rules-based AU normaliser. The
``Normalizer`` interface is the extension point for plugging in
``address-net`` or ``libpostal`` later if better address coverage is
needed; v1 deliberately keeps zero NLP/system dependencies.

The two public functions:

- :func:`normalize_address` — uppercase, strip punctuation, collapse
  whitespace, expand AS4590 street-type abbreviations, expand state
  abbreviations. Output is exact-match-comparable against G-NAF's
  pre-formatted ``ADDRESS_LABEL``.
- :func:`parse_address` — parse a normalised input into
  :class:`AddressComponents` (``unit_number``, ``street_number``,
  ``street_name``, ``street_type``, ``locality``, ``state``,
  ``postcode``) using positional rules. Returns ``None`` for inputs
  that don't look like a street address (PO boxes, business names
  alone, free text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# AS4590-derived street-type abbreviation → long form. Used by both the
# Tier 1 normaliser (input expansion to match G-NAF's pre-formatted label)
# and the Tier 2 parser (recognising the street-type token from the back
# of the address).
_STREET_TYPE_ABBREVIATIONS: dict[str, str] = {
    "ST": "STREET",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "RD": "ROAD",
    "BLVD": "BOULEVARD",
    "BVD": "BOULEVARD",
    "CT": "COURT",
    "CRT": "COURT",
    "DR": "DRIVE",
    "DRV": "DRIVE",
    "HWY": "HIGHWAY",
    "HWAY": "HIGHWAY",
    "LA": "LANE",
    "LN": "LANE",
    "PL": "PLACE",
    "TCE": "TERRACE",
    "TER": "TERRACE",
    "CR": "CRESCENT",
    "CRES": "CRESCENT",
    "CCT": "CIRCUIT",
    "CIR": "CIRCUIT",
    "CL": "CLOSE",
    "GR": "GROVE",
    "GRV": "GROVE",
    "PDE": "PARADE",
    "PWY": "PARKWAY",
    "PKWY": "PARKWAY",
    "WY": "WAY",
    "TR": "TRAIL",
    "TRL": "TRAIL",
    "MWY": "MOTORWAY",
}

# Recognised long-form street types — superset of the abbreviation map's
# values, plus several that have no common abbreviation but are valid
# AS4590 street types.
_STREET_TYPES: frozenset[str] = frozenset(_STREET_TYPE_ABBREVIATIONS.values()) | {
    "WALK",
    "PROMENADE",
    "ESPLANADE",
    "QUAY",
    "MEWS",
    "GARDENS",
    "GREEN",
    "SQUARE",
    "ROW",
    "LINK",
    "RISE",
    "VIEW",
    "VISTA",
    "BAY",
    "POINT",
    "RIDGE",
    "RUN",
    "GULLY",
    "GLEN",
    "HILL",
}

# Full state name → abbreviation (G-NAF labels use the abbreviation).
_STATE_NAMES_TO_ABBREVIATIONS: dict[str, str] = {
    "NEW SOUTH WALES": "NSW",
    "VICTORIA": "VIC",
    "QUEENSLAND": "QLD",
    "WESTERN AUSTRALIA": "WA",
    "SOUTH AUSTRALIA": "SA",
    "TASMANIA": "TAS",
    "NORTHERN TERRITORY": "NT",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}

_VALID_STATE_ABBREVIATIONS: frozenset[str] = frozenset(
    _STATE_NAMES_TO_ABBREVIATIONS.values()
)

# AU postcodes are 4 digits.
_POSTCODE_AT_END = re.compile(r"\b(\d{4})\s*$")

# Street number: leading digits, optional letter suffix, optional range.
# Examples: 1, 100, 100A, 100-102, 100A-102B.
_STREET_NUMBER_TOKEN = re.compile(r"^\d+[A-Z]?(?:-\d+[A-Z]?)?$")


@dataclass(frozen=True)
class AddressComponents:
    """Parsed AU address components used for Tier 2 component matching.

    Any field may be ``None`` if the parser couldn't identify it.
    """

    unit_number: str | None
    street_number: str | None
    street_name: str | None
    street_type: str | None
    locality: str | None
    state: str | None
    postcode: str | None


def normalize_address(address: str) -> str:
    """Normalise an address for Tier 1 exact match against G-NAF ``ADDRESS_LABEL``.

    Operations, in order:

    1. Uppercase.
    2. Replace ``,.;:!?`` with spaces (G-NAF labels carry no punctuation).
    3. Collapse internal whitespace.
    4. Expand state full names → state abbreviations (NEW SOUTH WALES → NSW).
    5. Expand AS4590 street-type abbreviations (ST → STREET) per token.
    6. Strip leading/trailing whitespace.

    Returns the empty string for empty input.
    """
    if not address:
        return ""

    s = address.upper()
    s = re.sub(r"[,.;:!?]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Expand state names BEFORE street-type expansion. Some state names
    # contain words that overlap with the street-type set (e.g. "SOUTH"
    # in NEW SOUTH WALES would otherwise be untouched, which is fine,
    # but doing this first keeps intent obvious).
    for full, abbrev in _STATE_NAMES_TO_ABBREVIATIONS.items():
        s = s.replace(full, abbrev)

    # Expand street-type abbreviations token-by-token.
    tokens = s.split()
    expanded = [_STREET_TYPE_ABBREVIATIONS.get(t, t) for t in tokens]
    return " ".join(expanded)


def parse_address(address: str) -> AddressComponents | None:
    """Parse a (possibly-unnormalised) AU address into components.

    Best-effort rules-based parser. Strategy:

    1. Normalise.
    2. Strip 4-digit postcode from the end.
    3. Strip state abbreviation from the end.
    4. Find the rightmost token that's a known street type; everything to
       its left is "number + street name", everything to its right is
       locality.
    5. Pull off unit + street number from the front (handles ``5/100``
       slash form and ``100-102`` ranges).

    Returns ``None`` if no useful component could be extracted (e.g. PO
    boxes, business names alone, free text without addresses).
    """
    normalised = normalize_address(address)
    if not normalised:
        return None

    # Strip postcode from end.
    postcode: str | None = None
    pc_match = _POSTCODE_AT_END.search(normalised)
    if pc_match:
        postcode = pc_match.group(1)
        normalised = normalised[: pc_match.start()].strip()

    tokens = normalised.split() if normalised else []

    # Strip state from end.
    state: str | None = None
    if tokens and tokens[-1] in _VALID_STATE_ABBREVIATIONS:
        state = tokens[-1]
        tokens = tokens[:-1]

    # Find the rightmost street-type token (scan from end).
    street_type: str | None = None
    street_type_idx: int | None = None
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i] in _STREET_TYPES:
            street_type = tokens[i]
            street_type_idx = i
            break

    # Pull off the leading number (unit/street_number/range).
    unit_number: str | None = None
    street_number: str | None = None
    if tokens:
        first = tokens[0]
        if "/" in first:
            head, tail = first.split("/", 1)
            # ``head`` is the unit (possibly preceded by U/UNIT in another
            # token, but the slash form alone is enough to disambiguate).
            if head and (head.isdigit() or head.rstrip("0123456789").upper() in ("", "U")):
                unit_number = head
                if _STREET_NUMBER_TOKEN.match(tail):
                    street_number = tail
                    tokens = tokens[1:]
                    if street_type_idx is not None:
                        street_type_idx -= 1
        elif _STREET_NUMBER_TOKEN.match(first):
            street_number = first
            tokens = tokens[1:]
            if street_type_idx is not None:
                street_type_idx -= 1

    # Split the remainder into street_name and locality at the street_type token.
    street_name: str | None = None
    locality: str | None = None
    if street_type_idx is not None and street_type_idx >= 0:
        street_name = " ".join(tokens[:street_type_idx]).strip() or None
        locality = " ".join(tokens[street_type_idx + 1 :]).strip() or None
    elif tokens:
        # No street type recognised — everything remaining is locality.
        locality = " ".join(tokens).strip() or None

    components = AddressComponents(
        unit_number=unit_number,
        street_number=street_number,
        street_name=street_name,
        street_type=street_type,
        locality=locality,
        state=state,
        postcode=postcode,
    )
    if all(
        v is None
        for v in (street_number, street_name, locality, postcode)
    ):
        return None
    return components
