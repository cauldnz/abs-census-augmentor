"""G-NAF geocoder (spec §7.2, §19).

Implements the :class:`~census_augment.geocoding.base.Geocoder` Protocol
using a :class:`~census_augment.data_sources.gnaf.GnafDataSource` as
backend. Three cascading match tiers (spec §19.3); on miss across all
three, returns a ``failed`` result so the pipeline falls through to the
next provider in ``geocoding.providers``.

Match-quality values per spec §19.1:

- ``gnaf_exact`` — Tier 1 hit: input normalises to a verbatim
  ``ADDRESS_LABEL``.
- ``gnaf_component`` — Tier 2 hit: parsed components match within a
  postcode-filtered candidate set.
- ``gnaf_fuzzy`` — Tier 3 hit: ``rapidfuzz`` similarity above
  ``fuzzy_threshold`` within a postcode-filtered candidate set;
  ``match_score`` populated.
- ``failed`` — fall through to the next provider.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rapidfuzz import fuzz

from ..data_sources.gnaf import GnafDataSource
from .base import GeocodeResult
from .normalize import AddressComponents, normalize_address, parse_address

_log = logging.getLogger(__name__)

_PROVIDER = "gnaf"

# Tier 3 candidate set cap. Bigger means more thorough fuzzy scoring but
# more work per geocode call; postcode pre-filter keeps this comfortable
# in practice (typical AU postcode has ~1k–10k addresses).
_TIER3_CANDIDATE_LIMIT = 1000


class GnafGeocoder:
    """G-NAF geocoder. Tier 1 in this commit; Tiers 2+3 land next."""

    def __init__(
        self,
        *,
        data_source: GnafDataSource,
        fuzzy_threshold: float = 0.85,
    ) -> None:
        if not (0.0 <= fuzzy_threshold <= 1.0):
            raise ValueError(
                f"fuzzy_threshold must be in [0.0, 1.0]; got {fuzzy_threshold!r}"
            )
        self._data_source = data_source
        self._fuzzy_threshold = fuzzy_threshold

    def geocode(self, address: str) -> GeocodeResult:
        """Try Tiers 1 → 2 → 3 in order. On miss across all three, return
        a ``failed`` result so the pipeline falls through to the next
        provider in ``geocoding.providers``.
        """
        normalized = normalize_address(address)
        if not normalized:
            return self._failed_result(address, normalized)

        result = self._tier1_exact(address, normalized)
        if result is not None:
            return result

        # Tiers 2 + 3 share a parse step.
        components = parse_address(address)
        if components is None:
            return self._failed_result(address, normalized)

        result = self._tier2_component(address, normalized, components)
        if result is not None:
            return result

        result = self._tier3_fuzzy(address, normalized, components)
        if result is not None:
            return result

        return self._failed_result(address, normalized)

    # ---- tier implementations ------------------------------------------

    def _tier1_exact(
        self, address_input: str, normalized: str
    ) -> GeocodeResult | None:
        """Exact-match the normalised input against G-NAF's ``ADDRESS_LABEL``.

        Returns the hit as a ``GeocodeResult`` with
        ``source="gnaf_exact"``, or ``None`` to fall through to Tier 2.
        """
        con = self._data_source.open_connection()
        # Parameterised query: DuckDB supports ``?`` placeholders.
        rows = con.execute(
            "SELECT ADDRESS_DETAIL_PID, ADDRESS_LABEL, LATITUDE, LONGITUDE, "
            "MB_CODE FROM gnaf WHERE ADDRESS_LABEL = ? LIMIT 1",
            [normalized],
        ).fetchall()
        if not rows:
            _log.debug("GnafGeocoder Tier 1 miss for %r", normalized)
            return None
        pid, label, lat, lon, mb_code = rows[0]
        _log.debug(
            "GnafGeocoder Tier 1 hit: %r -> %s (mb=%s)", normalized, pid, mb_code
        )
        return GeocodeResult(
            address_input=address_input,
            address_normalized=normalized,
            lat=float(lat),
            lon=float(lon),
            source="gnaf_exact",
            provider=_PROVIDER,
            timestamp=datetime.now(timezone.utc),
            raw_response={
                "ADDRESS_DETAIL_PID": pid,
                "ADDRESS_LABEL": label,
                "LATITUDE": float(lat),
                "LONGITUDE": float(lon),
                "MB_CODE": mb_code,
            },
            mb_code=mb_code,
            match_score=None,
        )

    def _tier2_component(
        self,
        address_input: str,
        normalized: str,
        components: AddressComponents,
    ) -> GeocodeResult | None:
        """Component match within a postcode-filtered candidate set.

        Requires at least a street_number + street_name (otherwise we'd
        match too many addresses). Postcode pre-filter is mandatory for
        scaling — without one we fall through.

        We don't have separate STREET_NUMBER / STREET_NAME columns in
        G-NAF Core; instead we substring-match the canonical
        ``"<num> <name>[ <type>]"`` form against ADDRESS_LABEL within the
        postcode bucket. Returns the unique match if exactly one
        candidate is found, else ``None`` to fall through to Tier 3.
        """
        if not (components.street_number and components.street_name):
            return None
        if not components.postcode:
            return None  # without a postcode the candidate set is too large

        # Canonical substring: "1 GEORGE STREET" — order matters in the label.
        if components.street_type:
            substring = (
                f"{components.street_number} {components.street_name} "
                f"{components.street_type}"
            )
        else:
            substring = (
                f"{components.street_number} {components.street_name}"
            )

        con = self._data_source.open_connection()
        rows = con.execute(
            "SELECT ADDRESS_DETAIL_PID, ADDRESS_LABEL, LATITUDE, LONGITUDE, "
            "MB_CODE FROM gnaf "
            "WHERE POSTCODE = ? AND ADDRESS_LABEL LIKE ? LIMIT 2",
            [components.postcode, f"%{substring}%"],
        ).fetchall()

        if len(rows) != 1:
            # Zero matches → fall through. >1 matches → ambiguous; don't
            # guess. Tier 3 fuzzy scoring will pick a winner if there is
            # a clear best candidate.
            _log.debug(
                "GnafGeocoder Tier 2 %s for %r in postcode %s: "
                "%d candidate(s)",
                "miss" if not rows else "ambiguous",
                substring,
                components.postcode,
                len(rows),
            )
            return None

        pid, label, lat, lon, mb_code = rows[0]
        _log.debug(
            "GnafGeocoder Tier 2 hit: %r -> %s (mb=%s)", normalized, pid, mb_code
        )
        return GeocodeResult(
            address_input=address_input,
            address_normalized=normalized,
            lat=float(lat),
            lon=float(lon),
            source="gnaf_component",
            provider=_PROVIDER,
            timestamp=datetime.now(timezone.utc),
            raw_response={
                "ADDRESS_DETAIL_PID": pid,
                "ADDRESS_LABEL": label,
                "LATITUDE": float(lat),
                "LONGITUDE": float(lon),
                "MB_CODE": mb_code,
                "MATCH_TIER": "gnaf_component",
            },
            mb_code=mb_code,
            match_score=None,
        )

    def _tier3_fuzzy(
        self,
        address_input: str,
        normalized: str,
        components: AddressComponents,
    ) -> GeocodeResult | None:
        """rapidfuzz token-set similarity within a postcode-filtered
        candidate set.

        Requires either a postcode (preferred) or at least a locality;
        otherwise the candidate set is the whole 15 M-row table and the
        cost isn't justified — we fall through.
        """
        con = self._data_source.open_connection()

        # Pull a candidate set, postcode-filtered if possible.
        if components.postcode:
            rows = con.execute(
                "SELECT ADDRESS_DETAIL_PID, ADDRESS_LABEL, LATITUDE, "
                "LONGITUDE, MB_CODE FROM gnaf WHERE POSTCODE = ? LIMIT ?",
                [components.postcode, _TIER3_CANDIDATE_LIMIT],
            ).fetchall()
        elif components.locality:
            rows = con.execute(
                "SELECT ADDRESS_DETAIL_PID, ADDRESS_LABEL, LATITUDE, "
                "LONGITUDE, MB_CODE FROM gnaf "
                "WHERE ADDRESS_LABEL LIKE ? LIMIT ?",
                [f"%{components.locality}%", _TIER3_CANDIDATE_LIMIT],
            ).fetchall()
        else:
            return None

        if not rows:
            return None

        # Score each candidate. token_set_ratio is forgiving of word
        # ordering and missing tokens; ideal for "1 GEORGE ST SYDNEY 2000"
        # vs "1 GEORGE STREET SYDNEY NSW 2000".
        best_score = 0.0
        best_row: tuple[str, str, float, float, str] | None = None
        for row in rows:
            label = row[1]
            score = fuzz.token_set_ratio(normalized, label) / 100.0
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None or best_score < self._fuzzy_threshold:
            _log.debug(
                "GnafGeocoder Tier 3 miss: best score %.3f below threshold %.3f",
                best_score,
                self._fuzzy_threshold,
            )
            return None

        pid, label, lat, lon, mb_code = best_row
        _log.debug(
            "GnafGeocoder Tier 3 hit: %r -> %s (score=%.3f, mb=%s)",
            normalized,
            pid,
            best_score,
            mb_code,
        )
        return GeocodeResult(
            address_input=address_input,
            address_normalized=normalized,
            lat=float(lat),
            lon=float(lon),
            source="gnaf_fuzzy",
            provider=_PROVIDER,
            timestamp=datetime.now(timezone.utc),
            raw_response={
                "ADDRESS_DETAIL_PID": pid,
                "ADDRESS_LABEL": label,
                "LATITUDE": float(lat),
                "LONGITUDE": float(lon),
                "MB_CODE": mb_code,
                "MATCH_TIER": "gnaf_fuzzy",
                "MATCH_SCORE": best_score,
            },
            mb_code=mb_code,
            match_score=best_score,
        )

    # ---- internals ------------------------------------------------------

    @staticmethod
    def _failed_result(address_input: str, normalized: str) -> GeocodeResult:
        return GeocodeResult(
            address_input=address_input,
            address_normalized=normalized,
            lat=None,
            lon=None,
            source="failed",
            provider=_PROVIDER,
            timestamp=datetime.now(timezone.utc),
        )
