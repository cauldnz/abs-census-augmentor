"""Tests for the Small Area Labour Markets (SALM) fetcher (spec §20).

Synthetic CSV fixtures mirror the live DEWR smoothed-SA2 file probed
firsthand on 2026-06-10: a top note row, a blank row, the header at row
3, three ``Data Item`` rows per SA2, wide quarter columns, ``-`` for
suppressed cells, and — critically — **thousands separators** in the
larger counts (``"2,318"``). The comma is the bug the real data exposed
(naive ``to_numeric`` nulls 98% of labour-force values), so the fixtures
carry it deliberately.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._salm import _SALM_URLS_BY_RELEASE, SalmDataSource

_2025Q4_URL = _SALM_URLS_BY_RELEASE["2025-Q4"]

# (data_item, sa2_name, sa2_code, [value per quarter])
_Row = tuple[str, str, str, list[object]]


def _make_salm_csv(
    *,
    rows: list[_Row],
    quarters: tuple[str, ...] = ("Sep-25", "Dec-25"),
    note: bool = True,
) -> bytes:
    """Build a synthetic SALM CSV mirroring the real layout (top note,
    blank row, header at row 3). Cells containing commas are CSV-quoted,
    so the thousands-separator counts survive a round-trip."""
    lines: list[str] = []
    if note:
        lines.append('"Note: a dash (-) indicates data are unavailable."')
        lines.append(",,,,")
    header = [
        "Data Item",
        "Statistical Area Level 2 (SA2) (2021 ASGS)",
        "SA2 Code (2021 ASGS)",
        *quarters,
    ]
    lines.append(",".join(header))
    for item, name, code, vals in rows:
        cells = [item, name, code, *[str(v) for v in vals]]
        lines.append(",".join(f'"{c}"' if "," in c else c for c in cells))
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _sa2_block(
    name: str,
    code: str,
    *,
    unemployment: list[object],
    labour_force: list[object],
    rate: list[object],
) -> list[_Row]:
    """The three Data Item rows for one SA2."""
    return [
        ("Smoothed unemployment (persons)", name, code, unemployment),
        ("Smoothed labour force (persons)", name, code, labour_force),
        ("Smoothed unemployment rate (%)", name, code, rate),
    ]


@pytest.fixture
def salm_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "salm-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(salm_data_dir: Path) -> None:
    ds = SalmDataSource(release="latest", root=salm_data_dir)
    assert ds.resolved_release == "2025-Q4"


def test_resolve_unknown_release_raises(salm_data_dir: Path) -> None:
    ds = SalmDataSource(release="2099-Q1", root=salm_data_dir)
    with pytest.raises(RuntimeError, match="not in the registry"):
        _ = ds.resolved_release


# ---- end-to-end load -----------------------------------------------------


@responses.activate
def test_load_surfaces_latest_quarter_and_strips_commas(salm_data_dir: Path) -> None:
    """The signature test: surface the latest quarter (Dec-25), and strip
    the thousands separators from the labour-force / large counts (the
    real-data bug)."""
    rows = [
        *_sa2_block(
            "Braidwood",
            "101021007",
            unemployment=[50, 57],
            labour_force=["2,200", "2,318"],  # comma!
            rate=[2.3, 2.5],
        ),
        *_sa2_block(
            "Big SA2",
            "206011117",
            unemployment=["1,050", "1,100"],  # big-SA2 unemployment also has a comma
            labour_force=["48,000", "50,000"],
            rate=[2.1, 2.2],
        ),
    ]
    responses.add(responses.GET, _2025Q4_URL, body=_make_salm_csv(rows=rows), status=200)

    ds = SalmDataSource(root=salm_data_dir)
    df = ds.load()

    assert set(df.index) == {"101021007", "206011117"}
    assert df.loc["101021007", "smoothed_unemployment_count"] == 57
    assert df.loc["101021007", "smoothed_labour_force_count"] == 2318  # comma stripped
    assert df.loc["101021007", "smoothed_unemployment_rate"] == 2.5
    # Big-SA2 comma-stripped on both counts.
    assert df.loc["206011117", "smoothed_unemployment_count"] == 1100
    assert df.loc["206011117", "smoothed_labour_force_count"] == 50000
    assert (df["reference_period"] == "2025-Q4").all()
    # Counts are nullable ints; the rate is a (nullable) float.
    assert pd.api.types.is_integer_dtype(df["smoothed_unemployment_count"])
    assert pd.api.types.is_float_dtype(df["smoothed_unemployment_rate"])


@responses.activate
def test_load_suppressed_dash_is_null(salm_data_dir: Path) -> None:
    rows = _sa2_block(
        "Sparse SA2",
        "101021610",
        unemployment=[8, "-"],  # suppressed in the latest quarter
        labour_force=["1,000", "-"],
        rate=[1.0, "-"],
    )
    responses.add(responses.GET, _2025Q4_URL, body=_make_salm_csv(rows=rows), status=200)
    ds = SalmDataSource(root=salm_data_dir)
    df = ds.load()
    assert pd.isna(df.loc["101021610", "smoothed_unemployment_count"])
    assert pd.isna(df.loc["101021610", "smoothed_labour_force_count"])
    assert pd.isna(df.loc["101021610", "smoothed_unemployment_rate"])


@responses.activate
def test_load_raises_on_stale_quarter(salm_data_dir: Path) -> None:
    """If the downloaded file's latest quarter doesn't match the requested
    release, the hardcoded URL is stale — fail loud."""
    rows = _sa2_block(
        "Braidwood",
        "101021007",
        unemployment=[50, 55],
        labour_force=["2,200", "2,300"],
        rate=[2.3, 2.4],
    )
    # File's latest quarter is Sep-25 (-> 2025-Q3), not the 2025-Q4 release.
    body = _make_salm_csv(rows=rows, quarters=("Jun-25", "Sep-25"))
    responses.add(responses.GET, _2025Q4_URL, body=body, status=200)
    ds = SalmDataSource(root=salm_data_dir)
    with pytest.raises(RuntimeError, match="2025-Q4"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_missing_header(salm_data_dir: Path) -> None:
    body = b"some,unrelated,csv\n1,2,3\n"
    responses.add(responses.GET, _2025Q4_URL, body=body, status=200)
    ds = SalmDataSource(root=salm_data_dir)
    with pytest.raises(RuntimeError, match="Data Item"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(salm_data_dir: Path) -> None:
    rows = _sa2_block(
        "Braidwood",
        "101021007",
        unemployment=[50, 57],
        labour_force=["2,200", "2,318"],
        rate=[2.3, 2.5],
    )
    responses.add(responses.GET, _2025Q4_URL, body=_make_salm_csv(rows=rows), status=200)
    ds = SalmDataSource(root=salm_data_dir)
    df1 = ds.load()
    ds2 = SalmDataSource(root=salm_data_dir)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
