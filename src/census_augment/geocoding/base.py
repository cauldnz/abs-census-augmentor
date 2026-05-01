"""Abstract geocoder interface and result type (spec §7.2, §19.1).

The ``source`` field doubles as the v1.0 match-quality tier identifier
(spec §19.1). Its value set is the same enum that ends up in the output
``geo_source`` column (spec §8): provider-prefixed for clarity
(``gnaf_exact`` / ``nominatim_fresh`` / etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

#: Match-quality tier identifier (spec §8 / §19.1).
#: ``input`` is set by the pipeline when lat/lon was provided directly
#: (no geocoder invoked). ``failed`` covers both "no provider matched"
#: and "geocoder produced an error". The other values are
#: provider-prefixed to make downstream filtering on geocoding quality
#: trivial (e.g. ``df[df.geo_source.isin(["gnaf_exact", "gnaf_component"])]``).
GeocodeSource = Literal[
    "input",
    "gnaf_exact",
    "gnaf_component",
    "gnaf_fuzzy",
    "nominatim_cache",
    "nominatim_fresh",
    "failed",
]


@dataclass(frozen=True)
class GeocodeResult:
    """Result of a geocoding lookup.

    Successful results have non-None ``lat``/``lon``. Failed lookups
    have ``lat``/``lon`` set to ``None`` and ``source == "failed"``.

    G-NAF results additionally populate ``mb_code`` (the 11-digit ABS
    Mesh Block identifier) — when present, it lets the pipeline bypass
    the spatial join via the §7.3 fast path. Nominatim results don't
    carry ``mb_code``.

    Tier 3 fuzzy matches populate ``match_score`` with the similarity
    score (0.0–1.0). Other tiers leave it ``None``.
    """

    address_input: str
    address_normalized: str
    lat: float | None
    lon: float | None
    source: GeocodeSource
    provider: str
    timestamp: datetime
    raw_response: dict[str, Any] | None = None
    mb_code: str | None = None
    match_score: float | None = None

    @property
    def is_success(self) -> bool:
        return self.lat is not None and self.lon is not None


class Geocoder(Protocol):
    """Synchronous geocoder interface.

    Implementations must:

    - Use a :class:`~census_augment.geocoding.cache.GeocodeCache` to
      short-circuit repeat lookups.
    - Return a :class:`GeocodeResult` with ``source="failed"`` and null
      coordinates on **any** failure mode the pipeline should treat as
      "row didn't geocode" — including HTTP errors, network errors, and
      malformed responses. The pipeline's contract (spec §10) is "address
      fails to geocode → null coords, flag, continue", which would break
      if implementations propagated network errors instead.
    - Propagate genuine programming errors (e.g. invalid argument types)
      so they surface during development rather than being masked as
      data-quality issues.
    """

    def geocode(self, address: str) -> GeocodeResult: ...
