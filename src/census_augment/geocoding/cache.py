"""Hash-keyed sharded JSON cache for geocoded addresses (spec §7.2)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import GeocodeResult

_log = logging.getLogger(__name__)


def normalize_address(address: str) -> str:
    """Return a canonical form of ``address`` for use as a cache key.

    Per spec §7.2: lowercase, whitespace-collapsed, trailing punctuation
    stripped. Used as the input to :func:`address_hash`.
    """
    s = address.lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[^\w]+$", "", s)
    return s


def address_hash(normalized: str) -> str:
    """SHA-256 hex digest of a normalized address."""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class GeocodeCache:
    """Sharded JSON cache for geocoder results.

    Layout: ``{root}/{hash[:2]}/{hash}.json`` (spec §7.2). The cache is a
    policy-free primitive — it stores whatever it is given and returns
    whatever it has. Caller decides what is worth caching (spec §14 #8 says
    failed lookups should not be cached at the geocoder level).
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, normalized: str) -> Path:
        h = address_hash(normalized)
        return self._root / h[:2] / f"{h}.json"

    def get(self, address: str) -> GeocodeResult | None:
        """Return the cached result for ``address``, or ``None`` on miss.

        Returned results always have ``source="cache"``. Corrupt or
        malformed cache files are logged and treated as misses so they
        can be transparently re-geocoded.
        """
        normalized = normalize_address(address)
        path = self._path_for(normalized)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning(
                "Corrupt geocode cache file %s (%s); treating as miss", path, exc
            )
            return None
        try:
            # v1.0: source is provider-prefixed. v1 only Nominatim caches
            # (G-NAF caches its underlying database, not individual lookups —
            # spec §7.2), so this is hardcoded; if a future provider gains
            # caching, we'd derive source from data["provider"].
            return GeocodeResult(
                address_input=data["address_input"],
                address_normalized=data["address_normalized"],
                lat=data["lat"],
                lon=data["lon"],
                source="nominatim_cache",
                provider=data["provider"],
                timestamp=datetime.fromisoformat(data["timestamp"]),
                raw_response=data.get("raw_response"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            _log.warning(
                "Malformed geocode cache file %s (%s); treating as miss", path, exc
            )
            return None

    def set(self, result: GeocodeResult) -> None:
        """Persist ``result`` to the cache.

        Writes are atomic (write to temp file, then rename) so a crashed
        process never leaves a partial JSON file readable by future runs.
        """
        path = self._path_for(result.address_normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "address_input": result.address_input,
            "address_normalized": result.address_normalized,
            "lat": result.lat,
            "lon": result.lon,
            "provider": result.provider,
            "timestamp": result.timestamp.isoformat(),
            "raw_response": result.raw_response,
        }
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


class NullCache(GeocodeCache):
    """No-op geocoding cache: always misses on read, ignores writes.

    Used when ``geocoding.cache_enabled`` is ``False`` so the
    NominatimGeocoder stays unchanged but every call goes to the
    network. Useful for debugging stale cached values or for tests
    that need to verify HTTP behaviour.
    """

    def __init__(self) -> None:
        # The parent's ``root`` is never touched (we override every method
        # that would access disk), but supplying a path keeps the type
        # contract clean.
        super().__init__(Path("."))

    def get(self, address: str) -> GeocodeResult | None:
        return None

    def set(self, result: GeocodeResult) -> None:
        return None
