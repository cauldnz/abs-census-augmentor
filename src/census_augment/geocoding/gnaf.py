"""G-NAF geocoder (spec §7.2, §19).

Implements the :class:`~census_augment.geocoding.base.Geocoder` Protocol
using a :class:`~census_augment.data_sources.gnaf.GnafDataSource` as
backend. Phase 4a ships **Tier 1 (exact ``ADDRESS_LABEL`` match)** only;
Tiers 2 and 3 (component match and FTS/fuzzy) land in a follow-up commit
that builds on the same backend.

Match-quality values per spec §19.1:

- ``gnaf_exact`` — Tier 1 hit (current commit).
- ``gnaf_component`` — Tier 2 hit (deferred).
- ``gnaf_fuzzy`` — Tier 3 hit (deferred), with ``match_score`` populated.
- ``failed`` — fall through to the next provider in
  ``geocoding.providers`` (handled by the pipeline, not here).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..data_sources.gnaf import GnafDataSource
from .base import GeocodeResult
from .normalize import normalize_address

_log = logging.getLogger(__name__)

_PROVIDER = "gnaf"


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
        """Try Tier 1 exact match. On miss, return a ``failed`` result so
        the pipeline falls through to the next provider in
        ``geocoding.providers``."""
        normalized = normalize_address(address)
        if not normalized:
            return self._failed_result(address, normalized)

        # Tier 1: exact ADDRESS_LABEL match.
        result = self._tier1_exact(address, normalized)
        if result is not None:
            return result

        # Tiers 2 and 3 are deferred to a follow-up commit. For now,
        # fall through (pipeline will try the next provider).
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
