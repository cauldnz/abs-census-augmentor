"""Nominatim geocoder implementation (spec §7.2)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

import requests

from .base import GeocodeResult
from .cache import GeocodeCache, normalize_address

_log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://nominatim.openstreetmap.org"
_RATE_LIMITED_STATUSES = frozenset({429, 503})


class NominatimGeocoder:
    """Geocoder backed by the Nominatim public API.

    Honours Nominatim's policy: 1 req/sec by default, ``User-Agent`` header
    required. Backs off exponentially on HTTP 429/503 (spec §14 #8) up to
    ``max_retries`` attempts before treating the lookup as failed. Failed
    lookups are not cached — they retry on the next run.
    """

    def __init__(
        self,
        user_agent: str,
        cache: GeocodeCache,
        rate_limit_per_second: float = 1.0,
        base_url: str = _DEFAULT_BASE_URL,
        max_retries: int = 3,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user_agent is required for Nominatim (policy)")
        if rate_limit_per_second <= 0:
            raise ValueError("rate_limit_per_second must be > 0")
        self._user_agent = user_agent
        self._cache = cache
        self._min_interval = 1.0 / rate_limit_per_second
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None

    def geocode(self, address: str) -> GeocodeResult:
        cached = self._cache.get(address)
        if cached is not None:
            return cached
        normalized = normalize_address(address)
        return self._fresh_lookup(address, normalized)

    def _fresh_lookup(self, address: str, normalized: str) -> GeocodeResult:
        backoff = 1.0
        for attempt in range(self._max_retries + 1):
            self._respect_rate_limit()
            response = self._do_request(address)
            if response is None:
                _log.warning("Nominatim request failed (network) for %r", address)
                return self._failed_result(address, normalized)
            if response.status_code in _RATE_LIMITED_STATUSES:
                if attempt >= self._max_retries:
                    _log.warning(
                        "Nominatim rate-limited for %r after %d attempts; giving up",
                        address,
                        attempt + 1,
                    )
                    return self._failed_result(address, normalized)
                wait = max(_parse_retry_after(response), backoff)
                _log.info(
                    "Nominatim rate-limited for %r; backing off %.1fs", address, wait
                )
                self._sleep(wait)
                backoff *= 2
                continue
            return self._handle_response(response, address, normalized)
        return self._failed_result(address, normalized)  # pragma: no cover

    def _respect_rate_limit(self) -> None:
        if self._last_request is not None:
            elapsed = self._monotonic() - self._last_request
            wait = self._min_interval - elapsed
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._monotonic()

    def _do_request(self, address: str) -> requests.Response | None:
        try:
            return self._session.get(
                f"{self._base_url}/search",
                params={"q": address, "format": "json", "limit": "1"},
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            _log.warning("Nominatim HTTP request raised: %s", exc)
            return None

    def _handle_response(
        self, response: requests.Response, address: str, normalized: str
    ) -> GeocodeResult:
        if not response.ok:
            _log.warning(
                "Nominatim returned %d for %r", response.status_code, address
            )
            return self._failed_result(address, normalized)
        try:
            data = response.json()
        except ValueError as exc:
            _log.warning("Nominatim returned non-JSON for %r: %s", address, exc)
            return self._failed_result(address, normalized)
        if not isinstance(data, list) or not data:
            return self._failed_result(address, normalized)
        entry = data[0]
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning("Malformed Nominatim entry for %r: %s", address, exc)
            return self._failed_result(address, normalized)
        result = GeocodeResult(
            address_input=address,
            address_normalized=normalized,
            lat=lat,
            lon=lon,
            source="fresh",
            provider="nominatim",
            timestamp=datetime.now(timezone.utc),
            raw_response=entry,
        )
        self._cache.set(result)
        return result

    def _failed_result(self, address: str, normalized: str) -> GeocodeResult:
        return GeocodeResult(
            address_input=address,
            address_normalized=normalized,
            lat=None,
            lon=None,
            source="failed",
            provider="nominatim",
            timestamp=datetime.now(timezone.utc),
            raw_response=None,
        )


def _parse_retry_after(response: requests.Response) -> float:
    """Parse the ``Retry-After`` header as seconds.

    Returns 0.0 if the header is absent or not a numeric value. Nominatim
    uses integer seconds; HTTP-date is theoretically valid per RFC 7231
    but treated as "no advice" here since back-off will kick in anyway.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return 0.0
    try:
        return max(float(header), 0.0)
    except ValueError:
        return 0.0
