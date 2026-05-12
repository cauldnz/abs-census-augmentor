"""Tests for census_augment.geocoding.cache and the GeocodeResult dataclass."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from census_augment.geocoding.base import GeocodeResult
from census_augment.geocoding.cache import (
    GeocodeCache,
    NullCache,
    address_hash,
    normalize_address,
)

# ---------- normalize_address ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello World", "hello world"),
        ("HELLO WORLD", "hello world"),
        ("  hello   world  ", "hello world"),
        ("hello\tworld\n", "hello world"),
        ("hello world.", "hello world"),
        ("hello world!!!", "hello world"),
        ("hello world.,;", "hello world"),
        ("hello, world.", "hello, world"),
        ("hello world  ...   ", "hello world"),
        ("", ""),
        ("   ", ""),
        ("Sydney NSW 2000", "sydney nsw 2000"),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


def test_normalize_is_idempotent() -> None:
    once = normalize_address("  Hello, World!  ")
    twice = normalize_address(once)
    assert once == twice


# ---------- address_hash ----------


def test_address_hash_deterministic() -> None:
    assert address_hash("hello") == address_hash("hello")


def test_address_hash_distinct_for_different_inputs() -> None:
    assert address_hash("hello") != address_hash("world")


def test_address_hash_is_sha256_hex() -> None:
    h = address_hash("hello")
    assert len(h) == 64
    assert h == hashlib.sha256(b"hello").hexdigest()


# ---------- GeocodeCache ----------


def _result(
    address: str = "1 Main St, Sydney",
    lat: float | None = -33.8688,
    lon: float | None = 151.2093,
    source: str = "fresh",
) -> GeocodeResult:
    return GeocodeResult(
        address_input=address,
        address_normalized=normalize_address(address),
        lat=lat,
        lon=lon,
        source=source,
        provider="nominatim",
        timestamp=datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc),
        raw_response={"display_name": "1 Main St, Sydney"},
    )


def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    assert cache.get("nothing here") is None


def test_set_then_get_round_trip(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    original = _result()
    cache.set(original)

    retrieved = cache.get(original.address_input)
    assert retrieved is not None
    assert retrieved.address_input == original.address_input
    assert retrieved.address_normalized == original.address_normalized
    assert retrieved.lat == original.lat
    assert retrieved.lon == original.lon
    assert retrieved.provider == original.provider
    assert retrieved.timestamp == original.timestamp
    assert retrieved.raw_response == original.raw_response


def test_get_flips_source_to_cache(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    cache.set(_result(source="fresh"))
    retrieved = cache.get("1 Main St, Sydney")
    assert retrieved is not None
    assert retrieved.source == "nominatim_cache"


def test_set_creates_sharded_directory_layout(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    address = "1 Main St, Sydney"
    cache.set(_result(address=address))

    expected_hash = address_hash(normalize_address(address))
    expected_path = tmp_path / expected_hash[:2] / f"{expected_hash}.json"
    assert expected_path.exists()


def test_set_overwrites_existing_entry(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    address = "1 Main St"
    cache.set(_result(address=address, lat=-33.0, lon=151.0))
    cache.set(_result(address=address, lat=-34.5, lon=149.5))

    retrieved = cache.get(address)
    assert retrieved is not None
    assert retrieved.lat == -34.5
    assert retrieved.lon == 149.5


@pytest.mark.parametrize(
    "variant",
    [
        "1 Main St",
        "1 MAIN ST",
        "  1 main st  ",
        "1 main st.",
        "1 Main St!!",
    ],
)
def test_lookup_normalizes_address(tmp_path: Path, variant: str) -> None:
    """Different surface forms of the same address hit the same cache entry."""
    cache = GeocodeCache(tmp_path)
    cache.set(_result(address="1 Main St"))
    assert cache.get(variant) is not None


def test_corrupt_json_treated_as_miss(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    address = "1 Main St"
    cache.set(_result(address=address))

    h = address_hash(normalize_address(address))
    cached_path = tmp_path / h[:2] / f"{h}.json"
    cached_path.write_text("{this is not json", encoding="utf-8")

    assert cache.get(address) is None


def test_malformed_payload_treated_as_miss(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    address = "1 Main St"
    h = address_hash(normalize_address(address))
    cached_path = tmp_path / h[:2] / f"{h}.json"
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(json.dumps({"unrelated": "data"}), encoding="utf-8")

    assert cache.get(address) is None


def test_atomic_write_no_tmp_file_left_behind(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    cache.set(_result())
    leftover = list(tmp_path.rglob("*.tmp"))
    assert leftover == []


def test_cache_root_property(tmp_path: Path) -> None:
    cache = GeocodeCache(tmp_path)
    assert cache.root == tmp_path


# ---------- GeocodeResult.is_success ----------


def test_is_success_true_when_coords_present() -> None:
    assert _result().is_success is True


def test_is_success_false_when_lat_none() -> None:
    assert _result(lat=None).is_success is False


def test_is_success_false_when_lon_none() -> None:
    assert _result(lon=None).is_success is False


def test_is_success_false_when_both_none() -> None:
    assert _result(lat=None, lon=None).is_success is False


# ---------- NullCache (when geocoding.cache_enabled is False) -------------


def test_null_cache_get_always_returns_none() -> None:
    cache = NullCache()
    cache.set(_result())  # silently ignored
    assert cache.get("1 Main St") is None


def test_null_cache_set_is_idempotent_no_op() -> None:
    """``set`` returns None and doesn't raise; ``get`` still misses
    afterward (proving nothing was persisted)."""
    cache = NullCache()
    assert cache.set(_result()) is None  # type: ignore[func-returns-value]
    cache.set(_result(address="2 Other St", lat=-34.0, lon=152.0))
    assert cache.get("1 Main St, Sydney") is None
    assert cache.get("2 Other St") is None
