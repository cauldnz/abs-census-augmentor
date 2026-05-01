"""Abstract geocoder interface and result type (spec §7.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

GeocodeSource = Literal["cache", "fresh", "failed"]


@dataclass(frozen=True)
class GeocodeResult:
    """Result of a geocoding lookup.

    Successful results have non-None ``lat``/``lon`` and ``source`` in
    ``{"cache", "fresh"}``. Failed lookups have ``lat`` / ``lon`` set to
    ``None`` and ``source == "failed"``.
    """

    address_input: str
    address_normalized: str
    lat: float | None
    lon: float | None
    source: GeocodeSource
    provider: str
    timestamp: datetime
    raw_response: dict[str, Any] | None = None

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
