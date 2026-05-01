"""Tests for census_augment.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from census_augment.paths import default_cache_dir, default_data_dir


def test_default_data_dir_contains_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without override, the default uses platformdirs (so 'census-augment'
    appears in the resolved path)."""
    monkeypatch.delenv("CENSUS_AUGMENT_DATA_DIR", raising=False)
    p = default_data_dir()
    assert isinstance(p, Path)
    assert "census-augment" in str(p)
    assert p.name == "data"


def test_default_cache_dir_contains_app_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CENSUS_AUGMENT_CACHE_DIR", raising=False)
    p = default_cache_dir()
    assert isinstance(p, Path)
    assert "census-augment" in str(p)
    assert p.name == "cache"


def test_data_dir_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_AUGMENT_DATA_DIR", str(tmp_path / "custom_data"))
    assert default_data_dir() == tmp_path / "custom_data"


def test_cache_dir_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENSUS_AUGMENT_CACHE_DIR", str(tmp_path / "custom_cache"))
    assert default_cache_dir() == tmp_path / "custom_cache"


def test_default_dirs_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """data and cache should resolve to different subdirectories of the
    platform user cache, not the same place."""
    monkeypatch.delenv("CENSUS_AUGMENT_DATA_DIR", raising=False)
    monkeypatch.delenv("CENSUS_AUGMENT_CACHE_DIR", raising=False)
    assert default_data_dir() != default_cache_dir()
