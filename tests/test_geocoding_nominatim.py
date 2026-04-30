"""Tests for census_augment.geocoding.nominatim."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import responses
from requests.exceptions import ConnectionError as RequestsConnectionError
from responses import matchers

from census_augment.geocoding.cache import GeocodeCache, address_hash, normalize_address
from census_augment.geocoding.nominatim import NominatimGeocoder

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
SEARCH_URL = f"{NOMINATIM_BASE}/search"


def _ok_payload(
    lat: str = "-33.8688", lon: str = "151.2093", display_name: str = "1 Main St"
) -> list[dict[str, Any]]:
    return [
        {
            "place_id": 12345,
            "lat": lat,
            "lon": lon,
            "display_name": display_name,
        }
    ]


class _FakeClock:
    """Settable monotonic clock for rate-limit tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_geocoder(
    tmp_path: Path,
    *,
    sleeps: list[float] | None = None,
    monotonic: Callable[[], float] | None = None,
    user_agent: str = "test/0.1 (test@example.com)",
    rate_limit_per_second: float = 1.0,
    max_retries: int = 3,
) -> NominatimGeocoder:
    cache = GeocodeCache(tmp_path / "cache")
    sleep_fn: Callable[[float], None] = (
        sleeps.append if sleeps is not None else (lambda _seconds: None)
    )
    if monotonic is None:
        # Fix at 0 so back-to-back calls deterministically trigger rate-limit sleep
        monotonic = _FakeClock(0.0)
    return NominatimGeocoder(
        user_agent=user_agent,
        cache=cache,
        rate_limit_per_second=rate_limit_per_second,
        max_retries=max_retries,
        sleep=sleep_fn,
        monotonic=monotonic,
    )


# ---------- constructor validation ----------


def test_constructor_rejects_empty_user_agent(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    with pytest.raises(ValueError, match="user_agent"):
        NominatimGeocoder(user_agent="   ", cache=cache)


def test_constructor_rejects_non_positive_rate(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    with pytest.raises(ValueError, match="rate_limit"):
        NominatimGeocoder(
            user_agent="x/1 (a@b.c)", cache=cache, rate_limit_per_second=0
        )


# ---------- happy path ----------


@responses.activate
def test_successful_lookup_returns_fresh_result(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    geocoder = _make_geocoder(tmp_path)

    result = geocoder.geocode("1 Main St, Sydney")

    assert result.is_success
    assert result.lat == -33.8688
    assert result.lon == 151.2093
    assert result.source == "fresh"
    assert result.provider == "nominatim"
    assert result.address_input == "1 Main St, Sydney"
    assert result.address_normalized == "1 main st, sydney"
    assert result.raw_response is not None
    assert result.raw_response["display_name"] == "1 Main St"
    assert len(responses.calls) == 1


@responses.activate
def test_successful_lookup_writes_to_cache(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    geocoder = _make_geocoder(tmp_path)
    geocoder.geocode("1 Main St")

    h = address_hash(normalize_address("1 Main St"))
    expected_path = tmp_path / "cache" / h[:2] / f"{h}.json"
    assert expected_path.exists()


@responses.activate
def test_user_agent_header_sent(tmp_path: Path) -> None:
    ua = "test-agent/0.1 (someone@example.com)"
    responses.add(
        responses.GET,
        SEARCH_URL,
        json=_ok_payload(),
        status=200,
        match=[matchers.header_matcher({"User-Agent": ua})],
    )
    geocoder = _make_geocoder(tmp_path, user_agent=ua)
    result = geocoder.geocode("1 Main St")
    assert result.is_success


@responses.activate
def test_query_parameters_sent(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        SEARCH_URL,
        json=_ok_payload(),
        status=200,
        match=[
            matchers.query_param_matcher(
                {"q": "1 Main St", "format": "json", "limit": "1"}
            )
        ],
    )
    geocoder = _make_geocoder(tmp_path)
    geocoder.geocode("1 Main St")


# ---------- cache short-circuit ----------


@responses.activate
def test_cache_hit_makes_no_http_request(tmp_path: Path) -> None:
    # First call populates cache
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    geocoder = _make_geocoder(tmp_path)
    first = geocoder.geocode("1 Main St")
    assert first.source == "fresh"

    # Second call should hit cache; no further HTTP
    second = geocoder.geocode("1 Main St")
    assert second.source == "cache"
    assert second.lat == first.lat
    assert second.lon == first.lon
    assert len(responses.calls) == 1  # still just the first call


@responses.activate
def test_normalized_variants_share_cache_entry(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    geocoder = _make_geocoder(tmp_path)
    geocoder.geocode("1 Main St")

    for variant in ["1 MAIN ST", "  1 main st  ", "1 Main St."]:
        result = geocoder.geocode(variant)
        assert result.source == "cache"
    assert len(responses.calls) == 1


# ---------- failure paths ----------


@responses.activate
def test_empty_results_yields_failed_lookup(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=[], status=200)
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("nowhere")
    assert result.source == "failed"
    assert result.lat is None
    assert result.lon is None


@responses.activate
def test_failed_lookup_not_cached(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=[], status=200)
    geocoder = _make_geocoder(tmp_path)
    geocoder.geocode("nowhere")

    h = address_hash(normalize_address("nowhere"))
    cache_path = tmp_path / "cache" / h[:2] / f"{h}.json"
    assert not cache_path.exists()


@responses.activate
def test_500_treated_as_failed_no_retry(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json={"err": "boom"}, status=500)
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")
    assert result.source == "failed"
    assert len(responses.calls) == 1  # no retry on 500


@responses.activate
def test_400_treated_as_failed(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json={"err": "bad"}, status=400)
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")
    assert result.source == "failed"


@responses.activate
def test_non_json_response_treated_as_failed(tmp_path: Path) -> None:
    responses.add(
        responses.GET, SEARCH_URL, body="not json at all", status=200
    )
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")
    assert result.source == "failed"


@responses.activate
def test_malformed_entry_missing_lat_treated_as_failed(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        SEARCH_URL,
        json=[{"display_name": "x"}],
        status=200,
    )
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")
    assert result.source == "failed"


@responses.activate
def test_network_error_treated_as_failed(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        SEARCH_URL,
        body=RequestsConnectionError("simulated outage"),
    )
    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")
    assert result.source == "failed"


# ---------- 429 / 503 rate-limit handling ----------


@responses.activate
def test_429_then_success_retries_and_succeeds(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json={}, status=429)
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)

    sleeps: list[float] = []
    geocoder = _make_geocoder(tmp_path, sleeps=sleeps)
    result = geocoder.geocode("anywhere")

    assert result.is_success
    assert result.source == "fresh"
    assert len(responses.calls) == 2
    # Some sleep happened for back-off (>=1s default)
    assert any(s >= 1.0 for s in sleeps)


@responses.activate
def test_503_then_success_retries_and_succeeds(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json={}, status=503)
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)

    geocoder = _make_geocoder(tmp_path)
    result = geocoder.geocode("anywhere")

    assert result.is_success
    assert len(responses.calls) == 2


@responses.activate
def test_persistent_429_exhausts_retries_and_fails(tmp_path: Path) -> None:
    # Need max_retries+1 = 4 mock responses (initial + 3 retries) all 429
    for _ in range(4):
        responses.add(responses.GET, SEARCH_URL, json={}, status=429)

    sleeps: list[float] = []
    # High rate-limit so per-request rate-limiter sleeps don't pollute the
    # back-off counting. Back-off should be 1.0, 2.0, 4.0 between the three
    # retries.
    geocoder = _make_geocoder(
        tmp_path, sleeps=sleeps, max_retries=3, rate_limit_per_second=1000.0
    )
    result = geocoder.geocode("rate-limited address")

    assert result.source == "failed"
    assert result.lat is None
    assert len(responses.calls) == 4
    backoff_sleeps = [s for s in sleeps if s >= 1.0]
    assert backoff_sleeps == [1.0, 2.0, 4.0]


@responses.activate
def test_429_respects_retry_after_header(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        SEARCH_URL,
        json={},
        status=429,
        headers={"Retry-After": "5"},
    )
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)

    sleeps: list[float] = []
    geocoder = _make_geocoder(tmp_path, sleeps=sleeps)
    geocoder.geocode("anywhere")

    # Retry-After of 5 is larger than default backoff of 1, so 5.0 should appear
    assert 5.0 in sleeps


@responses.activate
def test_429_with_invalid_retry_after_falls_back_to_backoff(tmp_path: Path) -> None:
    responses.add(
        responses.GET,
        SEARCH_URL,
        json={},
        status=429,
        headers={"Retry-After": "not-a-number"},
    )
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)

    sleeps: list[float] = []
    geocoder = _make_geocoder(tmp_path, sleeps=sleeps)
    geocoder.geocode("anywhere")

    # Without a usable header, default backoff (>= 1s) kicks in
    assert any(s >= 1.0 for s in sleeps)


# ---------- rate limiter ----------


@responses.activate
def test_rate_limiter_sleeps_between_requests(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    responses.add(
        responses.GET,
        SEARCH_URL,
        json=_ok_payload("-34.0", "150.0"),
        status=200,
    )

    sleeps: list[float] = []
    # Clock fixed at 0 so back-to-back calls always look "0s elapsed"
    geocoder = _make_geocoder(
        tmp_path, sleeps=sleeps, monotonic=_FakeClock(0.0)
    )
    geocoder.geocode("a")
    geocoder.geocode("b")

    assert 1.0 in sleeps  # full min_interval enforced
    assert len(responses.calls) == 2


@responses.activate
def test_rate_limiter_no_sleep_when_interval_passed(tmp_path: Path) -> None:
    responses.add(responses.GET, SEARCH_URL, json=_ok_payload(), status=200)
    responses.add(
        responses.GET,
        SEARCH_URL,
        json=_ok_payload("-34.0", "150.0"),
        status=200,
    )

    sleeps: list[float] = []
    clock = _FakeClock(0.0)
    geocoder = _make_geocoder(tmp_path, sleeps=sleeps, monotonic=clock)
    geocoder.geocode("a")
    clock.t = 5.0  # plenty of time elapsed
    geocoder.geocode("b")

    # No rate-limiter sleep should have been issued (interval already satisfied)
    assert sleeps == []
