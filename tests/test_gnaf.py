"""Tests for census_augment.data_sources.gnaf.

Phase 2 (cache-mode plumbing) + Phase 8 (anonymous S3 fetch + listing,
hermetic-tested via ``moto``).
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from census_augment.data_sources.gnaf import GnafDataSource


# ---- moto helpers ---------------------------------------------------------


_TEST_BUCKET = "minus34.com"
_TEST_BASE = f"s3://{_TEST_BUCKET}/opendata"


def _make_public_bucket() -> Any:
    """Create the test bucket with public-read ACL (mirrors gnaf-loader prod).

    Returns the (signed) admin S3 client so callers can put objects on it.
    Must be called inside an ``@mock_aws`` context.
    """
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=_TEST_BUCKET, ObjectOwnership="ObjectWriter")
    s3.delete_public_access_block(Bucket=_TEST_BUCKET)
    s3.put_bucket_acl(Bucket=_TEST_BUCKET, ACL="public-read")
    return s3


def _populate_mock_bucket(
    *,
    releases: dict[str, dict[str, bytes]],
) -> None:
    """Set up a moto S3 bucket mirroring the gnaf-loader layout.

    ``releases`` maps a release YYYYMM (e.g. "202602") to a dict of
    parquet filename -> bytes. Files are written under
    ``opendata/geoscape-{YYYYMM}/geoparquet/{filename}`` with
    ``public-read`` ACLs so the unsigned client in
    :class:`GnafDataSource` (which mirrors real anonymous-S3 access)
    can read them.

    Must be called inside an ``@mock_aws`` context.
    """
    s3 = _make_public_bucket()
    for release, files in releases.items():
        for filename, body in files.items():
            s3.put_object(
                Bucket=_TEST_BUCKET,
                Key=f"opendata/geoscape-{release}/geoparquet/{filename}",
                Body=body,
                ACL="public-read",
            )


def _gnaf_parquet_bytes() -> bytes:
    """Tiny valid G-NAF Core parquet (matches the conftest fixture's schema)."""
    table = pa.table(
        {
            "ADDRESS_DETAIL_PID": ["GANSW000000001"],
            "ADDRESS_LABEL": ["1 GEORGE STREET SYDNEY NSW 2000"],
            "LATITUDE": [-33.864],
            "LONGITUDE": [151.211],
            "MB_CODE": ["11701132601"],
            "POSTCODE": ["2000"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


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


@mock_aws
def test_resolved_release_raises_when_latest_no_cache_and_no_s3_releases(
    tmp_path: Path,
) -> None:
    """``release='latest'`` with empty cache *and* empty S3 raises clearly."""
    _make_public_bucket()  # bucket exists but has no geoscape-*/ prefixes

    ds = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    with pytest.raises(RuntimeError, match="cannot be resolved"):
        _ = ds.resolved_release


@mock_aws
def test_resolved_release_falls_through_to_s3_when_no_local_cache(
    tmp_path: Path,
) -> None:
    """``release='latest'`` with empty cache resolves from S3."""
    _populate_mock_bucket(
        releases={
            "202508": {"addresses.parquet": _gnaf_parquet_bytes()},
            "202602": {"addresses.parquet": _gnaf_parquet_bytes()},
        }
    )
    ds = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    # Highest YYYYMM on S3 wins.
    assert ds.resolved_release == "202602"


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


def test_layout_detection_fails_on_empty_release_dir(tmp_path: Path) -> None:
    """The local layout detector fails noisily on an empty release dir."""
    rel_dir = tmp_path / "data" / "gnaf" / "202602"
    rel_dir.mkdir(parents=True)
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    with pytest.raises(RuntimeError, match="No G-NAF parquet files found"):
        ds._detect_local_layout(rel_dir)  # type: ignore[attr-defined]


# ---- deferred modes raise NotImplementedError ----------------------------


def test_official_mode_raises_not_implemented(fake_gnaf_data_dir: Path) -> None:
    ds = GnafDataSource(
        release="202602", mode="official", data_dir=fake_gnaf_data_dir
    )
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        ds.open_connection()


def test_fetch_raises_in_remote_mode(fake_gnaf_data_dir: Path) -> None:
    """fetch() in remote mode is meaningless — should raise loudly."""
    ds = GnafDataSource(
        release="202602", mode="remote", data_dir=fake_gnaf_data_dir
    )
    with pytest.raises(RuntimeError, match="meaningless in remote mode"):
        ds.fetch()


def test_build_object_url_default_aws_virtual_hosted(tmp_path: Path) -> None:
    """Default URL construction (dot-less bucket) is virtual-hosted AWS style."""
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    assert ds._build_object_url(  # type: ignore[attr-defined]
        "my-gnaf", "opendata/x.parquet"
    ) == "https://my-gnaf.s3.amazonaws.com/opendata/x.parquet"


def test_build_object_url_dotted_bucket_uses_regional_path_style(
    tmp_path: Path,
) -> None:
    """Bucket names with dots can't use virtual-hosted (TLS cert mismatch
    against ``*.s3.amazonaws.com``). Per #17, they also can't use the
    *global* path-style endpoint because that returns 301 to the
    regional one and DuckDB httpfs doesn't follow redirects. We
    resolve the bucket's region via boto3 and use the regional
    endpoint directly.
    """
    ds = GnafDataSource(release="202602", data_dir=tmp_path / "data")
    # Skip the boto3 round-trip: pre-cache the region.
    ds._resolved_bucket_region = "ap-southeast-2"  # type: ignore[attr-defined]
    assert ds._build_object_url(  # type: ignore[attr-defined]
        "minus34.com", "opendata/x.parquet"
    ) == "https://s3.ap-southeast-2.amazonaws.com/minus34.com/opendata/x.parquet"


def test_build_object_url_with_endpoint_override(tmp_path: Path) -> None:
    """A configured endpoint switches to path-style addressing."""
    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_https_endpoint="http://localhost:5000",
    )
    assert ds._build_object_url(  # type: ignore[attr-defined]
        "minus34.com", "opendata/x.parquet"
    ) == "http://localhost:5000/minus34.com/opendata/x.parquet"


def test_build_object_url_endpoint_trailing_slash_stripped(
    tmp_path: Path,
) -> None:
    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_https_endpoint="http://localhost:5000/",  # trailing slash
    )
    assert ds._build_object_url(  # type: ignore[attr-defined]
        "minus34.com", "opendata/x.parquet"
    ) == "http://localhost:5000/minus34.com/opendata/x.parquet"


@mock_aws
def test_fetch_in_cache_mode_downloads_from_s3_on_cache_miss(
    tmp_path: Path,
) -> None:
    """A cache miss triggers an anonymous S3 download to the local cache dir."""
    parquet_bytes = _gnaf_parquet_bytes()
    _populate_mock_bucket(
        releases={"202602": {"addresses.parquet": parquet_bytes}}
    )

    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    rel_dir = ds.fetch()

    # Download landed in the right place with the right content.
    assert rel_dir == tmp_path / "data" / "gnaf" / "202602"
    files = sorted(rel_dir.glob("*.parquet"))
    assert len(files) == 1
    assert files[0].name == "addresses.parquet"
    assert files[0].read_bytes() == parquet_bytes


@mock_aws
def test_fetch_raises_when_release_does_not_exist_on_s3(
    tmp_path: Path,
) -> None:
    """Asking for a release the S3 bucket doesn't have produces a clear error."""
    _make_public_bucket()  # no releases populated

    ds = GnafDataSource(
        release="999999",  # explicit, doesn't exist on S3
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    with pytest.raises(RuntimeError, match="No .parquet files found"):
        ds.fetch()


@mock_aws
def test_fetch_skips_files_already_present_locally(tmp_path: Path) -> None:
    """Re-running fetch after a partial download resumes (no re-download)."""
    parquet_bytes = _gnaf_parquet_bytes()
    _populate_mock_bucket(
        releases={
            "202602": {
                "a.parquet": parquet_bytes,
                "b.parquet": parquet_bytes,
            }
        }
    )

    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )

    # Pretend a previous run got partway: 'a.parquet' is already on disk.
    rel_dir = tmp_path / "data" / "gnaf" / "202602"
    rel_dir.mkdir(parents=True)
    (rel_dir / "a.parquet").write_bytes(parquet_bytes)

    # is_cached() should now be true (one parquet file present), so fetch()
    # short-circuits and never even hits S3 — that's also fine. Force an
    # explicit S3 download by passing refresh=True.
    ds.fetch(refresh=True)

    # Both files should be present.
    assert (rel_dir / "a.parquet").exists()
    assert (rel_dir / "b.parquet").exists()
    # No leftover .tmp files.
    assert not list(rel_dir.glob("*.tmp"))


@mock_aws
def test_fetch_with_refresh_re_resolves_latest_from_s3(tmp_path: Path) -> None:
    """``refresh=True`` + ``release='latest'`` re-checks S3 for newer releases.

    Even if a local cache exists, calling fetch(refresh=True) when the
    user asked for 'latest' should pick up newer releases from S3.
    """
    parquet_bytes = _gnaf_parquet_bytes()
    _populate_mock_bucket(
        releases={
            "202508": {"addresses.parquet": parquet_bytes},
            "202602": {"addresses.parquet": parquet_bytes},  # newer
        }
    )

    # Pre-populate cache with the older release only.
    rel_dir_old = tmp_path / "data" / "gnaf" / "202508"
    rel_dir_old.mkdir(parents=True)
    (rel_dir_old / "addresses.parquet").write_bytes(parquet_bytes)

    ds = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )

    # Without refresh, latest resolves from cache (202508).
    assert ds.resolved_release == "202508"

    # Re-do the test with a fresh data source and refresh=True.
    ds2 = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    new_dir = ds2.fetch(refresh=True)
    assert ds2.resolved_release == "202602"
    assert new_dir.name == "202602"
    assert (new_dir / "addresses.parquet").exists()


@mock_aws
def test_fetch_atomic_no_tmp_files_after_success(tmp_path: Path) -> None:
    """Successful download leaves no ``.tmp`` files behind."""
    _populate_mock_bucket(
        releases={"202602": {"addresses.parquet": _gnaf_parquet_bytes()}}
    )
    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    rel_dir = ds.fetch()
    assert not list(rel_dir.glob("*.tmp"))


@mock_aws
def test_list_releases_on_s3_returns_sorted_yyyymm(tmp_path: Path) -> None:
    """Direct unit test for the listing helper — sorted, no dupes."""
    _populate_mock_bucket(
        releases={
            "202602": {"x.parquet": b"a"},
            "202508": {"x.parquet": b"a"},
            "202511": {"x.parquet": b"a"},
        }
    )
    ds = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    assert ds._list_releases_on_s3() == ["202508", "202511", "202602"]  # type: ignore[attr-defined]


@mock_aws
def test_list_releases_on_s3_ignores_non_geoscape_prefixes(
    tmp_path: Path,
) -> None:
    """Other ``opendata/`` siblings (``geoscape-foo/``, ``census-/``) are skipped."""
    s3 = _make_public_bucket()
    for key in (
        "opendata/geoscape-202602/geoparquet/x.parquet",
        "opendata/geoscape-foobar/geoparquet/x.parquet",
        "opendata/census-202602/x.parquet",
    ):
        s3.put_object(Bucket=_TEST_BUCKET, Key=key, Body=b"a", ACL="public-read")

    ds = GnafDataSource(
        release="latest",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    assert ds._list_releases_on_s3() == ["202602"]  # type: ignore[attr-defined]


@mock_aws
def test_open_connection_works_after_s3_fetch(tmp_path: Path) -> None:
    """End-to-end: cache miss -> S3 download -> DuckDB opens cleanly."""
    _populate_mock_bucket(
        releases={"202602": {"addresses.parquet": _gnaf_parquet_bytes()}}
    )
    ds = GnafDataSource(
        release="202602",
        data_dir=tmp_path / "data",
        s3_base_url=_TEST_BASE,
    )
    con = ds.open_connection()
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 1


# ---- s3 url parsing -----------------------------------------------------


def test_parse_s3_url_with_prefix(tmp_path: Path) -> None:
    from census_augment.data_sources.gnaf import _parse_s3_url

    assert _parse_s3_url("s3://bucket/key/prefix") == ("bucket", "key/prefix")
    assert _parse_s3_url("s3://bucket/key/prefix/") == ("bucket", "key/prefix")
    assert _parse_s3_url("s3://bucket") == ("bucket", "")
    assert _parse_s3_url("s3://bucket/") == ("bucket", "")


def test_parse_s3_url_rejects_non_s3(tmp_path: Path) -> None:
    from census_augment.data_sources.gnaf import _parse_s3_url

    with pytest.raises(ValueError, match="s3://"):
        _parse_s3_url("https://example.com/x")


# ---- remote mode (DuckDB httpfs) ---------------------------------------
#
# These exercise the end-to-end remote-mode path against a moto S3 server.
# DuckDB's httpfs reads parquet over HTTP via libcurl, so moto's
# ThreadedMotoServer (which speaks real HTTP) is the right fixture --
# unlike the pure-boto3 tests which can use ``@mock_aws`` (in-process).
#
# httpfs is a DuckDB extension. ``INSTALL httpfs`` will hit DuckDB's
# extension repo on first call and cache locally; subsequent calls are
# no-ops. CI fresh containers do incur this one-time download.


@pytest.fixture(scope="module")
def moto_s3_server() -> Any:
    """Module-scoped moto S3 server. Yields the endpoint URL.

    Listens on 127.0.0.1 specifically (the moto default 0.0.0.0 is a
    bind-only address — you can't *connect* to it on Windows).

    Module-scoped because spinning up/tearing down a moto server is
    relatively expensive (Flask app start). Tests isolate themselves
    by using unique bucket names rather than relying on a clean server
    — moto's S3 state persists across ThreadedMotoServer restarts in
    the same Python process anyway, so a per-test server wouldn't help.
    """
    from moto.server import ThreadedMotoServer  # noqa: PLC0415

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    _, port = server.get_host_and_port()
    endpoint = f"http://127.0.0.1:{port}"
    try:
        yield endpoint
    finally:
        server.stop()


def _unique_bucket(test_name: str) -> tuple[str, str]:
    """Generate a unique bucket name + s3 base URL for a given test.

    Bucket names must be globally unique within the moto server's
    lifetime (which spans the test module).
    """
    bucket = f"test-bucket-{test_name}"
    return bucket, f"s3://{bucket}/opendata"


def _populate_moto_server(
    endpoint: str,
    bucket: str,
    releases: dict[str, dict[str, bytes]],
) -> None:
    """Write parquet objects to the named bucket on the running moto server."""
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket, ObjectOwnership="ObjectWriter")
    s3.delete_public_access_block(Bucket=bucket)
    s3.put_bucket_acl(Bucket=bucket, ACL="public-read")
    for release, files in releases.items():
        for filename, body in files.items():
            s3.put_object(
                Bucket=bucket,
                Key=f"opendata/geoscape-{release}/geoparquet/{filename}",
                Body=body,
                ACL="public-read",
            )


def test_remote_mode_open_connection_streams_via_httpfs(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """End-to-end remote: list S3, build URLs, DuckDB reads parquet via HTTP."""
    bucket, base = _unique_bucket("open-streams")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={"202602": {"addresses.parquet": _gnaf_parquet_bytes()}},
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    con = ds.open_connection()

    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 1

    # Spot-check a column to confirm projection works through httpfs.
    label = con.execute("SELECT ADDRESS_LABEL FROM gnaf LIMIT 1").fetchone()
    assert label is not None
    assert label[0] == "1 GEORGE STREET SYDNEY NSW 2000"


def test_remote_mode_resolves_latest_from_s3_ignoring_local_cache(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Remote mode skips local cache and always lists S3 for ``latest``."""
    bucket, base = _unique_bucket("resolves-latest")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202508": {"addresses.parquet": _gnaf_parquet_bytes()},
            "202602": {"addresses.parquet": _gnaf_parquet_bytes()},
        },
    )

    # Pre-populate local cache with an older release. Cache mode would
    # prefer this; remote mode must ignore it.
    rel_dir_old = tmp_path / "data" / "gnaf" / "202508"
    rel_dir_old.mkdir(parents=True)
    (rel_dir_old / "addresses.parquet").write_bytes(_gnaf_parquet_bytes())

    ds = GnafDataSource(
        release="latest",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    assert ds.resolved_release == "202602"


def test_remote_mode_raises_when_release_does_not_exist(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Asking for a release the bucket doesn't have produces a clear error."""
    bucket, base = _unique_bucket("missing-release")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={"202602": {"addresses.parquet": _gnaf_parquet_bytes()}},
    )

    ds = GnafDataSource(
        release="999999",  # not on S3
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    with pytest.raises(RuntimeError, match="No G-NAF parquet files found"):
        ds.open_connection()


def test_remote_mode_validates_schema(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Remote mode raises if the parquet schema is wrong."""
    bucket, base = _unique_bucket("validates-schema")
    # Build a parquet missing MB_CODE.
    bad_table = pa.table(
        {
            "ADDRESS_DETAIL_PID": ["X1"],
            "ADDRESS_LABEL": ["1 NOWHERE"],
            "LATITUDE": [-33.0],
            "LONGITUDE": [151.0],
            "POSTCODE": ["2000"],
            # MB_CODE deliberately absent
        }
    )
    buf = io.BytesIO()
    pq.write_table(bad_table, buf)

    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={"202602": {"bad.parquet": buf.getvalue()}},
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    with pytest.raises(RuntimeError, match="missing required columns.*MB_CODE"):
        ds.open_connection()


# ---- parquet filter (issue #8: gnaf-loader bucket co-locates ABS bdys) --


def test_listing_default_filter_excludes_subdir_parquets(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Default rule: only flat parquets directly under ``geoparquet/``.

    The gnaf-loader bucket publishes G-NAF Core flat at the root of
    ``geoparquet/`` and ABS / OSM boundary tables in named
    subdirectories. Without filtering, schema validation chokes on
    the first non-G-NAF parquet DuckDB sees.
    """
    bucket, base = _unique_bucket("filter-default")
    parquet_bytes = _gnaf_parquet_bytes()
    # Mix one good (flat) and several bad (in subdirs) parquets.
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "addresses.parquet": parquet_bytes,  # G-NAF Core ✓
                "abs_2016_gccsa/part-00000-aaa.snappy.parquet": parquet_bytes,
                "abs_2016_gccsa/part-00001-bbb.snappy.parquet": parquet_bytes,
                "osm_amenities/part-00000.parquet": parquet_bytes,
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    objs = ds._list_parquet_objects_on_s3("202602")  # type: ignore[attr-defined]
    keys = [k for k, _ in objs]
    assert keys == ["opendata/geoscape-202602/geoparquet/addresses.parquet"]


def test_listing_custom_filter_overrides_default(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Override regex matches the relative key (post-``geoparquet/``)."""
    bucket, base = _unique_bucket("filter-custom")
    parquet_bytes = _gnaf_parquet_bytes()
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "addresses.parquet": parquet_bytes,
                "lookup/locality.parquet": parquet_bytes,  # in subdir, but G-NAF
                "abs_2016_gccsa/part-00000.parquet": parquet_bytes,
            }
        },
    )

    # Match anything that doesn't start with 'abs_'.
    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        parquet_filter=r"^(?!abs_)",
    )
    objs = ds._list_parquet_objects_on_s3("202602")  # type: ignore[attr-defined]
    keys = sorted(k.rsplit("/", 1)[-1] for k, _ in objs)
    # Both G-NAF parquets included, ABS one filtered out.
    assert keys == ["addresses.parquet", "locality.parquet"]


def test_remote_mode_end_to_end_with_mixed_bucket_contents(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Issue #8 reproduction: bucket has ABS boundaries alongside G-NAF
    Core; remote mode should query just the G-NAF parquets cleanly."""
    bucket, base = _unique_bucket("issue-8-mixed")
    # Build an ABS-shaped parquet (no MB_CODE / ADDRESS_LABEL etc.) that
    # would fail schema validation if it slipped past the filter.
    abs_bytes_buf = io.BytesIO()
    pq.write_table(
        pa.table(
            {
                "gid": [1],
                "gcc_16code": ["1GSYD"],
                "gcc_16name": ["Greater Sydney"],
                "area_sqm": [12345.0],
                "geom": ["MULTIPOLYGON(...)"],
                "state": ["NSW"],
            }
        ),
        abs_bytes_buf,
    )
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "addresses.parquet": _gnaf_parquet_bytes(),
                # Same partitioned-subdirectory layout as the real bucket.
                "abs_2016_gccsa/part-00000-aaa.snappy.parquet": (
                    abs_bytes_buf.getvalue()
                ),
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
    )
    con = ds.open_connection()  # would fail without filter
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 1


def test_parquet_filter_param_compiles_lazily_on_construction(
    tmp_path: Path,
) -> None:
    """Bad regex passed to constructor surfaces immediately, not at use time."""
    with pytest.raises(re.error):
        GnafDataSource(
            release="202602",
            data_dir=tmp_path / "data",
            parquet_filter=r"[unclosed-class",  # invalid regex
        )


def test_invalid_census_year_raises(tmp_path: Path) -> None:
    """Implausible census_year produces a clear ValueError, not silent corruption."""
    with pytest.raises(ValueError, match="census_year"):
        GnafDataSource(
            release="202602",
            data_dir=tmp_path / "data",
            census_year=42,
        )


# ---- gnaf-loader layout (issues #12 + #17: real bucket layout) ----------


def _gnaf_loader_parquet_bytes(_census_year: int = 2021) -> bytes:
    """Synthetic parquet matching gnaf-loader's ``address_principals/`` schema.

    Mirrors the production columns (verified against the live
    ``minus34.com`` bucket, May 2026): one row per address with the
    full set of components the view-builder needs, including the
    address split (street portion only) and locality / state separately
    so the ``CONCAT_WS`` in :meth:`_gnaf_loader_view_select` produces
    a normalised ADDRESS_LABEL.

    The boundary subdirectories (``address_principal_admin_boundaries/``,
    ``address_principal_census_<year>_boundaries/``) carry only
    boundary-ID columns — see :func:`_admin_boundaries_parquet_bytes` /
    :func:`_census_boundaries_parquet_bytes` for the regression test in
    issue #17.
    """
    table = pa.table(
        {
            "gnaf_pid": ["GANSW000000001", "GANSW000000002"],
            # The address column in production is just the street portion;
            # locality / state / postcode are separate, and the view
            # concatenates them.
            "address": ["1 GEORGE STREET", "100 PITT STREET"],
            "locality_name": ["SYDNEY", "SYDNEY"],
            "state": ["NSW", "NSW"],
            "latitude": [-33.864, -33.866],
            "longitude": [151.211, 151.211],
            "postcode": ["2000", "2000"],
            "mb_2016_code": ["11701132601", "11701132602"],
            "mb_2021_code": ["11701132601", "11701132602"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _admin_boundaries_parquet_bytes() -> bytes:
    """Synthetic parquet matching gnaf-loader's
    ``address_principal_admin_boundaries/`` schema — gnaf_pid + admin
    boundary IDs only. **No address / lat / lon.** Used by the issue #17
    regression test to ensure the parser doesn't pick this subdirectory
    as the geocoder source.
    """
    table = pa.table(
        {
            "gnaf_pid": ["GANSW000000001"],
            "lga_code_2021": ["LGA-NSW-001"],
            "lga_name_2021": ["Sydney"],
            "poa_code_2021": ["2000"],
            "ra_code_2021": ["1"],
            "state_code_2021": ["1"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _census_boundaries_parquet_bytes(year: int = 2021) -> bytes:
    """Synthetic parquet matching gnaf-loader's
    ``address_principal_census_<year>_boundaries/`` schema — gnaf_pid +
    census boundary IDs only (MB / SA1-4 / GCCSA / etc.). **No
    address / lat / lon.** Used by issue #17 regression test.
    """
    table = pa.table(
        {
            "gnaf_pid": ["GANSW000000001"],
            f"mb_code_{year}": ["11701132601"],
            f"sa1_code_{year}": ["117011326010"],
            f"sa2_code_{year}": ["117011326"],
            f"sa3_code_{year}": ["11701"],
            f"sa4_code_{year}": ["117"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def test_remote_mode_with_gnaf_loader_layout(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Issue #12 / #17 reproduction: bucket has the real gnaf-loader
    layout (multiple subdirectories under ``geoparquet/``). The
    ``address_principals/`` subdirectory carries the columns we need;
    the parser must auto-detect that one and ignore the boundary-only
    siblings.
    """
    bucket, base = _unique_bucket("issue-12-loader")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                # The right source — has gnaf_pid, address, lat/lon,
                # postcode, mb_*_code.
                "address_principals/part-00000.parquet": (
                    _gnaf_loader_parquet_bytes()
                ),
                # Mix in non-G-NAF subdirectories (mirrors the real bucket).
                "abs_2016_gccsa/part-00000.parquet": _gnaf_parquet_bytes(),
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2021,
    )
    con = ds.open_connection()

    # The view exposes the uppercase columns (aliased from lowercase).
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 2

    row = con.execute(
        "SELECT ADDRESS_LABEL, MB_CODE FROM gnaf WHERE "
        "ADDRESS_DETAIL_PID = 'GANSW000000001'"
    ).fetchone()
    assert row is not None
    label, mb = row
    assert label == "1 GEORGE STREET SYDNEY NSW 2000"
    assert mb == "11701132601"


def test_remote_mode_picks_correct_mb_year_column(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """``census_year`` selects which ``mb_<year>_code`` column to alias
    as ``MB_CODE``. The ``address_principals`` table carries both
    2016 and 2021 MB codes; the SELECT clause picks one based on
    ``census_year``.
    """
    bucket, base = _unique_bucket("issue-12-year")
    table = pa.table(
        {
            "gnaf_pid": ["GANSW000000001"],
            "address": ["1 GEORGE STREET"],
            "locality_name": ["SYDNEY"],
            "state": ["NSW"],
            "latitude": [-33.864],
            "longitude": [151.211],
            "postcode": ["2000"],
            "mb_2016_code": ["MB16"],
            "mb_2021_code": ["MB21"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    parquet_bytes = buf.getvalue()

    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "address_principals/part-00000.parquet": parquet_bytes,
            }
        },
    )

    # census_year=2016 → mb_2016_code → MB_CODE = "MB16"
    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2016,
    )
    row = ds.open_connection().execute(
        "SELECT MB_CODE FROM gnaf WHERE ADDRESS_DETAIL_PID = 'GANSW000000001'"
    ).fetchone()
    assert row is not None
    assert row[0] == "MB16"


def test_remote_mode_ignores_boundaries_siblings_issue_17(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Issue #17 regression: the production bucket has *multiple*
    sibling subdirectories under ``geoparquet/`` whose names start
    with ``address_principal_*_boundaries/`` (admin / census 2016 /
    census 2021). None of those carry the ``address`` column or
    lat/lon — they're boundary-ID join tables only. The geocoder
    source is ``address_principals/``.

    The parser must pick ``address_principals/`` even though the
    boundary siblings sort lexicographically before / between it.
    """
    bucket, base = _unique_bucket("issue-17-boundaries")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                # Primary source — what we expect to be picked up.
                "address_principals/part-00000.parquet": (
                    _gnaf_loader_parquet_bytes()
                ),
                # Sibling boundary tables — no address column;
                # picking one of these would produce the
                # BinderException reported in #17.
                "address_principal_admin_boundaries/part-00000.parquet": (
                    _admin_boundaries_parquet_bytes()
                ),
                "address_principal_census_2016_boundaries/part-00000.parquet": (
                    _census_boundaries_parquet_bytes(2016)
                ),
                "address_principal_census_2021_boundaries/part-00000.parquet": (
                    _census_boundaries_parquet_bytes(2021)
                ),
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2021,
    )
    # Should pick address_principals/ and resolve cleanly. Before #17
    # this failed with: BinderException 'address' not found in FROM
    # clause; candidates: lga_code_2021, poa_code_2021, ra_code_2021,
    # state_code_2021.
    con = ds.open_connection()
    row = con.execute(
        "SELECT ADDRESS_LABEL, MB_CODE FROM gnaf WHERE "
        "ADDRESS_DETAIL_PID = 'GANSW000000001'"
    ).fetchone()
    assert row is not None
    label, mb = row
    assert label == "1 GEORGE STREET SYDNEY NSW 2000"
    assert mb == "11701132601"


def test_remote_mode_explicit_failure_when_only_boundaries_present(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """If the bucket genuinely has only boundary subdirectories (no
    ``address_principals/``), fail loudly rather than try to use the
    boundary table as a G-NAF source. The user should get a clear
    "no G-NAF parquet files" error pointing at the layout we couldn't
    find, not a buried DuckDB BinderException.
    """
    bucket, base = _unique_bucket("issue-17-only-boundaries")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "address_principal_admin_boundaries/part-00000.parquet": (
                    _admin_boundaries_parquet_bytes()
                ),
                "address_principal_census_2021_boundaries/part-00000.parquet": (
                    _census_boundaries_parquet_bytes(2021)
                ),
                # No address_principals/.
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2021,
    )
    with pytest.raises(RuntimeError, match="No G-NAF parquet files"):
        ds.open_connection()


def test_cache_mode_with_gnaf_loader_layout(tmp_path: Path) -> None:
    """Cache mode auto-detects the gnaf-loader subdirectory layout."""
    data_dir = tmp_path / "data"
    rel_dir = data_dir / "gnaf" / "202602"
    loader_dir = rel_dir / "address_principals"
    loader_dir.mkdir(parents=True)
    (loader_dir / "part-00000.parquet").write_bytes(
        _gnaf_loader_parquet_bytes()
    )

    ds = GnafDataSource(
        release="202602",
        mode="cache",
        data_dir=data_dir,
        census_year=2021,
    )
    con = ds.open_connection()
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 2

    # Spot-check column aliasing: ADDRESS_LABEL came from the
    # `address` source column.
    labels = [
        r[0] for r in con.execute("SELECT ADDRESS_LABEL FROM gnaf").fetchall()
    ]
    assert "1 GEORGE STREET SYDNEY NSW 2000" in labels


def test_layout_detection_prefers_gnaf_loader_over_legacy(
    tmp_path: Path,
) -> None:
    """If both layouts coexist in the cache, gnaf-loader wins.

    This handles the migration case where a user might have
    populated the cache for both layouts — the gnaf-loader one is
    the canonical source.
    """
    data_dir = tmp_path / "data"
    rel_dir = data_dir / "gnaf" / "202602"
    rel_dir.mkdir(parents=True)
    # Legacy flat parquet at the root.
    (rel_dir / "addresses.parquet").write_bytes(_gnaf_parquet_bytes())
    # gnaf-loader subdir with different content (only 2 rows vs 5).
    loader_dir = rel_dir / "address_principals"
    loader_dir.mkdir()
    (loader_dir / "part-00000.parquet").write_bytes(
        _gnaf_loader_parquet_bytes()
    )

    ds = GnafDataSource(
        release="202602",
        mode="cache",
        data_dir=data_dir,
        census_year=2021,
    )
    layout = ds._detect_local_layout(rel_dir)  # type: ignore[attr-defined]
    assert layout.style == "gnaf-loader"
    # Confirm the resulting view reads the gnaf-loader one (2 rows),
    # not the legacy one (5 rows).
    con = ds.open_connection()
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    assert rows[0] == 2


def test_cache_mode_download_preserves_gnaf_loader_subdir_structure(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """When downloading a gnaf-loader layout, the subdirectory structure
    is preserved on disk so the next run's local layout detection finds
    it. (Without this, downloads would land flat in the release dir and
    the gnaf-loader layout would silently downgrade to legacy.)"""
    bucket, base = _unique_bucket("issue-12-download")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={
            "202602": {
                "address_principals/part-00000.parquet": (
                    _gnaf_loader_parquet_bytes()
                ),
                "address_principals/part-00001.parquet": (
                    _gnaf_loader_parquet_bytes()
                ),
            }
        },
    )

    ds = GnafDataSource(
        release="202602",
        mode="cache",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2021,
    )
    rel_dir = ds.fetch()
    principals = rel_dir / "address_principals"
    assert principals.is_dir()
    assert sorted(p.name for p in principals.glob("*.parquet")) == [
        "part-00000.parquet",
        "part-00001.parquet",
    ]


def test_remote_mode_falls_back_to_legacy_when_gnaf_loader_subdir_missing(
    tmp_path: Path, moto_s3_server: str
) -> None:
    """Buckets without the gnaf-loader subdir (e.g. user-built mirrors
    with a single pre-joined parquet) still work via the legacy path."""
    bucket, base = _unique_bucket("issue-12-fallback")
    _populate_moto_server(
        moto_s3_server,
        bucket,
        releases={"202602": {"addresses.parquet": _gnaf_parquet_bytes()}},
    )

    ds = GnafDataSource(
        release="202602",
        mode="remote",
        data_dir=tmp_path / "data",
        s3_base_url=base,
        s3_https_endpoint=moto_s3_server,
        census_year=2021,
    )
    con = ds.open_connection()
    rows = con.execute("SELECT COUNT(*) FROM gnaf").fetchone()
    assert rows is not None
    # _gnaf_parquet_bytes() (legacy fixture) writes 1 row.
    assert rows[0] == 1
