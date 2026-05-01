"""Tests for census_augment.data_sources.gnaf (Phase 2: cache-mode plumbing)."""

from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from census_augment.data_sources.gnaf import GnafDataSource


# ---- constructor validation ----------------------------------------------


def test_default_release_is_latest(tmp_path: Path) -> None:
    """Default ``release`` is ``'latest'`` (resolved lazily)."""
    ds = GnafDataSource(data_dir=tmp_path / "data")
    # Don't access resolved_release here — it would try to resolve and fail
    # with no cache. Just confirm the request was retained.
    assert ds._release_request == "latest"  # type: ignore[attr-defined]


def test_explicit_release_format_validated(tmp_path: Path) -> None:
    """``'202602'`` is a valid release string (6 digits)."""
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    # No exception; release_request retained.
    assert ds._release_request == "202602"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "bad_release",
    [
        "20260",  # too short
        "2026021",  # too long
        "abc123",  # not all digits
        "20260a",  # mixed
        "",  # empty
    ],
)
def test_invalid_release_format_raises(tmp_path: Path, bad_release: str) -> None:
    with pytest.raises(ValueError, match="release must be"):
        GnafDataSource(release=bad_release, data_dir=tmp_path / "data")


def test_invalid_datum_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="datum must be"):
        GnafDataSource(datum="WGS84", data_dir=tmp_path / "data")  # type: ignore[arg-type]


def test_invalid_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode must be"):
        GnafDataSource(mode="bogus", data_dir=tmp_path / "data")  # type: ignore[arg-type]


# ---- caching / discovery -------------------------------------------------


def test_is_cached_false_when_no_data(tmp_path: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    assert ds.is_cached() is False


def test_is_cached_true_when_release_present(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    assert ds.is_cached() is True


def test_is_cached_ignores_empty_release_dirs(tmp_path: Path) -> None:
    """A YYYYMM directory with no .parquet files isn't 'cached'."""
    data_dir = tmp_path / "data"
    (data_dir / "gnaf" / "202602").mkdir(parents=True)  # empty
    ds = GnafDataSource(release="202602", data_dir=data_dir)
    assert ds.is_cached() is False


# ---- release resolution --------------------------------------------------


def test_resolved_release_uses_explicit_value(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    assert ds.resolved_release == "202602"


def test_resolved_release_picks_highest_when_latest(
    fake_gnaf_data_dir_with_two_releases: Path,
) -> None:
    """``release='latest'`` picks the highest-numbered cached release."""
    ds = GnafDataSource(
        release="latest", data_dir=fake_gnaf_data_dir_with_two_releases
    )
    assert ds.resolved_release == "202602"  # higher of 202511 / 202602


def test_resolved_release_raises_when_latest_no_cache(tmp_path: Path) -> None:
    """``release='latest'`` with no cache raises (S3 listing not implemented)."""
    ds = GnafDataSource(release="latest", data_dir=tmp_path / "data")
    with pytest.raises(RuntimeError, match="cannot be resolved"):
        _ = ds.resolved_release


# ---- DuckDB connection + view -------------------------------------------


def test_open_connection_creates_gnaf_view(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    con = ds.open_connection()
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 5  # synthetic addresses count


def test_open_connection_returns_address_label(fake_gnaf_data_dir: Path) -> None:
    """The view exposes ADDRESS_LABEL — the column Tier 1 will match against."""
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    con = ds.open_connection()
    labels = [r[0] for r in con.execute("SELECT ADDRESS_LABEL FROM gnaf").fetchall()]
    assert "1 GEORGE STREET SYDNEY NSW 2000" in labels


def test_open_connection_returns_mb_code(fake_gnaf_data_dir: Path) -> None:
    """MB_CODE is the field that lets us bypass the spatial join (§7.3)."""
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    con = ds.open_connection()
    row = con.execute(
        "SELECT MB_CODE FROM gnaf WHERE ADDRESS_DETAIL_PID = 'GANSW000000001'"
    ).fetchone()
    assert row is not None
    assert row[0] == "11701132601"


def test_open_connection_caches_connection(fake_gnaf_data_dir: Path) -> None:
    """Repeat calls return the same connection (DuckDB initialisation is
    not free)."""
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    con1 = ds.open_connection()
    con2 = ds.open_connection()
    assert con1 is con2


def test_close_releases_connection(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    ds.open_connection()
    ds.close()
    # Reopen yields a *new* connection, but it still works.
    con2 = ds.open_connection()
    rows = con2.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None and rows[0] == 5


# ---- schema validation ---------------------------------------------------


def test_schema_validation_accepts_minimum_columns(
    fake_gnaf_data_dir: Path,
) -> None:
    """Synthetic fixture has exactly the required minimum schema."""
    ds = GnafDataSource(release="202602", data_dir=fake_gnaf_data_dir)
    ds.open_connection()  # would raise if schema were wrong


def test_schema_validation_fails_on_missing_required_column(
    tmp_path: Path,
) -> None:
    """Drop MB_CODE from the fixture and confirm the open fails clearly."""
    data_dir = tmp_path / "data"
    release_dir = data_dir / "gnaf" / "202602"
    release_dir.mkdir(parents=True)

    # Synthetic Parquet WITHOUT MB_CODE
    table = pa.table(
        {
            "ADDRESS_DETAIL_PID": ["X1"],
            "ADDRESS_LABEL": ["1 NOWHERE LANE NULLVILLE"],
            "LATITUDE": [-33.0],
            "LONGITUDE": [151.0],
            # MB_CODE deliberately absent
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    (release_dir / "addresses.parquet").write_bytes(buf.getvalue())

    ds = GnafDataSource(release="202602", data_dir=data_dir)
    with pytest.raises(RuntimeError, match="missing required columns.*MB_CODE"):
        ds.open_connection()


def test_schema_validation_fails_on_empty_release_dir(tmp_path: Path) -> None:
    """A release directory with no Parquet files fails noisily."""
    data_dir = tmp_path / "data"
    (data_dir / "gnaf" / "202602").mkdir(parents=True)
    # No parquet files written
    ds = GnafDataSource(release="202602", data_dir=data_dir)
    # is_cached returns False (no parquet), so resolved_release falls back to
    # 'latest' resolution which finds nothing — but request is explicit, so it
    # uses the explicit value. Then fetch() raises NotImplementedError because
    # no cached files. So this test checks the right behaviour:
    with pytest.raises(NotImplementedError, match="manually place"):
        ds.open_connection()


# ---- deferred modes raise NotImplementedError ----------------------------


def test_remote_mode_raises_not_implemented(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(
        release="202602", mode="remote", data_dir=fake_gnaf_data_dir
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        ds.open_connection()


def test_official_mode_raises_not_implemented(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(
        release="202602", mode="official", data_dir=fake_gnaf_data_dir
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        ds.open_connection()


def test_fetch_in_cache_mode_with_uncached_release_raises(tmp_path: Path) -> None:
    """If we ask for a release that's not in the cache, S3 download isn't
    yet implemented — should raise with a useful suggestion."""
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    with pytest.raises(NotImplementedError, match="manually place"):
        ds.fetch()
