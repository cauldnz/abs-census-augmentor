"""Tests for census_augment._http_retry.

Validates the streaming-download retry semantics required by spec §10:

- Retry on ConnectionError / Timeout / 502 / 503 / 504.
- Up to 3 attempts by default with 1s / 2s / 4s exponential backoff.
- Other 4xx / 5xx propagate immediately (404 from a wrong URL must
  not be silently retried).
- Sleeps are injectable so tests run in milliseconds, not seconds.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import requests
import responses

from census_augment._http_retry import retry_stream_get

URL = "https://abs.test/some-file.zip"


def _record_sleeps() -> tuple[list[float], Callable[[float], None]]:
    """Return (sleeps_list, sleep_fn) for asserting backoff schedule."""
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleeps, fake_sleep


@responses.activate
def test_returns_200_immediately_no_retry() -> None:
    """Happy path: a 200 on the first call returns straight away."""
    responses.add(responses.GET, URL, body=b"hello", status=200)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 200
    assert sleeps == []  # no retries -> no sleeps


@responses.activate
def test_retries_on_503_then_succeeds() -> None:
    """503 first, 200 on retry -> succeeds after one backoff."""
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body=b"finally", status=200)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 200
    assert sleeps == [1.0]  # one backoff between attempts


@responses.activate
def test_exponential_backoff_schedule() -> None:
    """Two transient failures -> 1s then 2s sleeps."""
    responses.add(responses.GET, URL, status=502)
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body=b"ok", status=200)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 200
    assert sleeps == [1.0, 2.0]


@responses.activate
def test_gives_up_after_max_retries_on_5xx() -> None:
    """4 consecutive 503s -> raises the final HTTPError."""
    for _ in range(4):
        responses.add(responses.GET, URL, status=503)
    # one more for the final re-issue inside retry_stream_get
    responses.add(responses.GET, URL, status=503)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    with pytest.raises(requests.HTTPError):
        retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    # 3 retries -> 3 sleeps
    assert sleeps == [1.0, 2.0, 4.0]


@responses.activate
def test_404_propagates_without_retry() -> None:
    """404 (not transient) returns immediately with a 404 response.

    The caller's ``raise_for_status`` produces the HTTPError; the
    retry helper itself doesn't retry non-transient 4xx / 5xx.
    """
    responses.add(responses.GET, URL, status=404)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 404
    assert sleeps == []  # no retries for 404


@responses.activate
def test_500_propagates_without_retry() -> None:
    """500 is treated as a real upstream bug, not retried.

    Spec §10 design: only 502/503/504 (proxy / gateway / upstream-
    overloaded) are retry-worthy; a 500 means the server crashed
    and we want loud failure.
    """
    responses.add(responses.GET, URL, status=500)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 500
    assert sleeps == []


@responses.activate
def test_retries_on_connection_error() -> None:
    """ConnectionError -> retried; second call returns 200."""
    responses.add(responses.GET, URL, body=requests.ConnectionError("dropped"))
    responses.add(responses.GET, URL, body=b"ok", status=200)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    response = retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert response.status_code == 200
    assert sleeps == [1.0]


@responses.activate
def test_gives_up_on_persistent_connection_error() -> None:
    """4 ConnectionErrors -> raises after exhausting retries."""
    for _ in range(4):
        responses.add(responses.GET, URL, body=requests.ConnectionError("nope"))
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    with pytest.raises(requests.ConnectionError):
        retry_stream_get(session, URL, timeout=10, sleep=fake_sleep)

    assert sleeps == [1.0, 2.0, 4.0]


@responses.activate
def test_label_appears_in_log_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """Caller's label ('SEIFA workbook') shows up in retry log lines."""
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, body=b"ok", status=200)
    sleeps, fake_sleep = _record_sleeps()

    session = requests.Session()
    with caplog.at_level("WARNING"):
        retry_stream_get(session, URL, timeout=10, sleep=fake_sleep, label="SEIFA workbook")

    assert any("SEIFA workbook" in record.message for record in caplog.records)
