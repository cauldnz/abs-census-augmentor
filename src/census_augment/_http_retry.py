"""Shared HTTP retry helper for ABS / data.gov.au streaming downloads.

Closes the gap between spec §10's promise ("Retry with exponential
backoff (3 attempts), then abort") and what the bulk fetchers
(`_AbsZipDataSource._download`, `_seifa/_erp/_dss/_ato.fetch`) were
actually doing — namely, a single `session.get()` followed by
`raise_for_status()` with no retry on transient 5xx or connection
errors.

The Nominatim geocoder (`geocoding/nominatim.py`) has its own
retry-with-rate-limit-awareness because it deals with per-lookup
4xx responses; this module is the streaming-download counterpart.

Scope:

- Retries on `requests.ConnectionError`, `requests.Timeout`, and HTTP
  502 / 503 / 504. Other 4xx / 5xx still raise immediately — those are
  caller errors (404 from a wrong URL, 401 from a missing token).
- Retries only the **initial connection**. Once `iter_content()` is
  draining bytes, a mid-stream break still propagates to the caller —
  resume-from-byte-N would need server range support and is out of
  scope for v1.
- Three attempts by default with 1s / 2s / 4s exponential backoff.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests

_log = logging.getLogger(__name__)

#: HTTP statuses that look like upstream/proxy transient failures.
#: Notably **not** 500 — that often signals a real upstream bug we
#: want loud, not a retry-and-hope. ABS occasionally serves 502/503
#: under load.
_TRANSIENT_STATUSES = frozenset({502, 503, 504})

DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 1.0


def retry_stream_get(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    label: str = "download",
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> requests.Response:
    """Open a streaming GET with retry-on-transient-failure.

    Returns the response in streaming mode (caller iterates ``.iter_content``
    and is responsible for closing — use as a context manager or call
    ``.close()`` explicitly).

    Raises the same exceptions ``session.get`` / ``response.raise_for_status``
    would, with retries exhausted, on terminal failure.

    ``label`` is woven into log lines so the caller's identity ("boundary
    ZIP", "SEIFA workbook") is visible in retry messages.
    """
    backoff = initial_backoff
    attempt = 0
    while True:
        try:
            response = session.get(url, stream=True, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt >= max_retries:
                _log.error(
                    "%s download to %s failed after %d attempts: %s",
                    label,
                    url,
                    attempt + 1,
                    e,
                )
                raise
            _log.warning(
                "%s download to %s failed (%s); retrying in %.1fs (attempt %d/%d)",
                label,
                url,
                e,
                backoff,
                attempt + 1,
                max_retries,
            )
            sleep(backoff)
            backoff *= 2
            attempt += 1
            continue

        if response.status_code in _TRANSIENT_STATUSES:
            response.close()
            if attempt >= max_retries:
                _log.error(
                    "%s download to %s returned %d after %d attempts; giving up",
                    label,
                    url,
                    response.status_code,
                    attempt + 1,
                )
                # Re-issue once more so the caller sees the response shape
                # for ``raise_for_status`` to produce its native error.
                response = session.get(url, stream=True, timeout=timeout)
                response.raise_for_status()
                return response  # pragma: no cover
            _log.warning(
                "%s download to %s returned %d; retrying in %.1fs (attempt %d/%d)",
                label,
                url,
                response.status_code,
                backoff,
                attempt + 1,
                max_retries,
            )
            sleep(backoff)
            backoff *= 2
            attempt += 1
            continue

        # 2xx / non-transient response — return it (caller decides
        # whether to raise_for_status). Other 4xx / 5xx will surface
        # the regular HTTPError from the caller's raise_for_status.
        return response
