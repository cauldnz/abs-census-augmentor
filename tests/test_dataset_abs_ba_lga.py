"""Tests for the ABS Building Approvals LGA dataset fetcher (spec §20).

Synthetic XLSX fixtures mirror the live ABS LGA cube schema probed on
2026-06-01:

- Sheet ``Table 1`` (with **space**, NOT ``Table_1`` like the SA2 cube)
- Row 4 = column headers (identical to SA2 cube)
- Row 5 = units (identical)
- Row 6 onwards = data rows, column A holds 5-digit LGA codes
  (NSW = 10000-19999, VIC = 20000-29999, etc.)

The fetcher's LGA-keyed parse is hermetic-tested here; the SA2 downscale
exercise uses a synthetic :class:`LgaSa2Correspondence` constructed by
hand for predictable area weights.
"""

from __future__ import annotations

import io
from pathlib import Path

import geopandas as gpd
import openpyxl
import pandas as pd
import pytest
import responses
from shapely.geometry import Polygon

from census_augment.correspondence import (
    LgaSa2Correspondence,
    compute_lga_sa2_correspondence,
)
from census_augment.datasets._abs_ba import ABS_BA_LANDING_URL
from census_augment.datasets._abs_ba_lga import AbsBaLgaDataSource


def _make_lga_state_xlsx(
    *,
    lga_records: list[tuple[str, dict[str, int | float | str]]],
    state_aggregate_row: tuple[str, str] | None = None,
) -> bytes:
    """Build one per-state ABS BA LGA cube. Critical detail: sheet name
    must be ``"Table 1"`` (with space), matching the real ABS layout.
    """
    wb = openpyxl.Workbook()
    ws_contents = wb.active
    ws_contents.title = "Contents"
    ws_contents.append(["Contents"])

    # Real ABS uses "Table 1" with a space — NOT "Table_1" like SA2 cubes.
    ws = wb.create_sheet("Table 1")
    ws.append([])
    ws.append(["87310DO004_synthetic Building Approvals (synthetic)"])
    ws.append(["Released (synthetic)"])
    ws.append(["Table 1. NSW, LGA excel data cube"])
    ws.append(
        [
            "",
            "",
            "New houses",
            "New other residential building",
            "Total dwellings",
            "Value of new houses",
            "Value of new other residential building",
            "Value of alterations & additions including conversions",
            "Value of total residential building",
            "Value of non-residential building",
            "Value of total building",
        ]
    )
    ws.append(["", "", "no.", "no.", "no.", "$'000", "$'000", "$'000", "$'000", "$'000", "$'000"])

    # State-level aggregate row (single digit code, e.g. "1" for NSW) —
    # should be filtered out by the 5-digit-code guard.
    if state_aggregate_row is not None:
        code, name = state_aggregate_row
        ws.append(
            [
                code,
                name,
                9999,
                9999,
                9999,
                9_999_999,
                9_999_999,
                9_999_999,
                9_999_999,
                9_999_999,
                9_999_999,
            ]
        )

    output_columns = [
        "new_houses_count",
        "new_other_residential_building_count",
        "total_dwellings_count",
        "value_new_houses",
        "value_new_other_residential_building",
        "value_alterations_additions_conversions",
        "value_total_residential_building",
        "value_non_residential_building",
        "value_total_building",
    ]
    for lga_code, fields in lga_records:
        row: list[object] = [lga_code, f"LGA {lga_code}"]
        for col in output_columns:
            row.append(fields.get(col, 0))
        ws.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_landing_html(yyyymm: str) -> str:
    """Landing page HTML carrying both the SA2 and LGA series URLs for
    the given month — only the LGA products (do004, do008, ...) are
    needed by this fetcher.
    """
    month_path = f"mar-{yyyymm[:4]}"
    base = (
        f"/statistics/industry/building-and-construction/building-approvals-australia/{month_path}/"
    )
    # All 16 LGA products (8 states × 2 series)
    products = [
        "do004",
        "do005",
        "do008",
        "do009",
        "do012",
        "do013",
        "do016",
        "do017",
        "do020",
        "do021",
        "do024",
        "do025",
        "do028",
        "do029",
        "do032",
        "do033",
    ]
    links = "\n".join(f'<a href="{base}87310{p}_{yyyymm}.xlsx">{p}</a>' for p in products)
    return f"<html><body>{links}</body></html>"


def _make_synthetic_correspondence(
    lga_to_sa2_shares: dict[str, list[tuple[str, float]]],
) -> LgaSa2Correspondence:
    """Build a :class:`LgaSa2Correspondence` directly from a mapping of
    ``{lga_code: [(sa2_code, sa2_share_of_lga), ...]}``. Bypasses the
    geometric intersection; the unit tests can specify exact area
    weights.
    """
    rows: list[dict[str, object]] = []
    for lga_code, sa2_shares in lga_to_sa2_shares.items():
        total_share = sum(s for _, s in sa2_shares)
        # Compute synthetic intersection / sa2 / lga area values that
        # reproduce the desired weights. lga_area = 100; intersection =
        # 100 * sa2_share_of_lga; sa2_area chosen so lga_share_of_sa2
        # comes out as half (arbitrary; the fetcher only reads
        # sa2_share_of_lga via downscale_counts).
        for sa2_code, share in sa2_shares:
            inter = 100.0 * share
            sa2_area = inter * 2.0  # arbitrary; sa2_share_of_lga is what matters
            lga_area = 100.0
            rows.append(
                {
                    "sa2_code": sa2_code,
                    "lga_code": lga_code,
                    "intersection_area_m2": inter,
                    "sa2_area_m2": sa2_area,
                    "lga_area_m2": lga_area,
                    "sa2_share_of_lga": share,
                    "lga_share_of_sa2": inter / sa2_area,
                }
            )
        # Sanity check: weights sum to at most 1 per LGA (allows partial
        # coverage if the SA2 set is incomplete).
        assert total_share <= 1.0 + 1e-9, (
            f"LGA {lga_code!r} sa2_share_of_lga sum {total_share} > 1.0"
        )
    weights = pd.DataFrame(rows)
    return LgaSa2Correspondence(
        weights=weights,
        sa2_code_column="SA2_CODE21",
        lga_code_column="LGA_CODE25",
    )


@pytest.fixture
def ba_lga_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "abs-ba-lga-cache"


# ---- release resolution --------------------------------------------------


@responses.activate
def test_resolve_latest_picks_most_recent_yyyymm(ba_lga_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaLgaDataSource(release="latest", root=ba_lga_data_dir)
    # March 2026 release (yyyymm=202603) covers FY 2024-25 complete.
    assert ds.resolved_release == "2024-25"


@responses.activate
def test_resolve_specific_ytd(ba_lga_data_dir: Path) -> None:
    """Asking for the FYTD picks the do005-series cubes."""
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaLgaDataSource(release="2025-26", root=ba_lga_data_dir)
    assert ds.resolved_release == "2025-26"


@responses.activate
def test_resolve_unknown_release_raises(ba_lga_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    ds = AbsBaLgaDataSource(release="2019-20", root=ba_lga_data_dir)
    with pytest.raises(RuntimeError, match="not available"):
        _ = ds.resolved_release


@responses.activate
def test_resolve_no_links_raises(ba_lga_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body="<html><body>nothing here</body></html>",
        status=200,
    )
    ds = AbsBaLgaDataSource(release="latest", root=ba_lga_data_dir)
    with pytest.raises(RuntimeError, match="Could not find"):
        _ = ds.resolved_release


# ---- correspondence guard ------------------------------------------------


def test_load_without_correspondence_raises_with_guidance(
    ba_lga_data_dir: Path,
) -> None:
    """Without an attached correspondence, load() must refuse loudly."""
    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    with pytest.raises(RuntimeError, match="attach_correspondence"):
        _ = ds.load()


def test_attach_correspondence_rejects_non_correspondence(
    ba_lga_data_dir: Path,
) -> None:
    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    with pytest.raises(TypeError, match="LgaSa2Correspondence"):
        ds.attach_correspondence({"some": "dict"})


# ---- parse + downscale ---------------------------------------------------


@responses.activate
def test_load_downscales_lga_values_to_sa2(ba_lga_data_dir: Path) -> None:
    """End-to-end with synthetic per-state cubes + a synthetic
    correspondence. Verifies:

    - State-aggregate rows (1-digit codes) are filtered out
    - 5-digit LGA codes pass through
    - downscale_counts preserves the per-LGA sum across SA2s
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    # One LGA per state (8 total). NSW has LGA 10500, VIC 20100, etc.
    # Each LGA reports 100 new houses; the synthetic correspondence
    # splits each LGA's value across 2 SA2s with 60/40 area shares.
    state_lga_map = {
        "NSW": ("do004", "10500"),
        "VIC": ("do008", "20100"),
        "QLD": ("do012", "30100"),
        "SA": ("do016", "40100"),
        "WA": ("do020", "50100"),
        "TAS": ("do024", "60100"),
        "NT": ("do028", "70100"),
        "ACT": ("do032", "80100"),
    }
    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    for state, (product, lga_code) in state_lga_map.items():
        xlsx_bytes = _make_lga_state_xlsx(
            lga_records=[
                (
                    lga_code,
                    {
                        "new_houses_count": 100,
                        "new_other_residential_building_count": 50,
                        "total_dwellings_count": 150,
                        "value_new_houses": 60_000,
                        "value_total_building": 100_000,
                    },
                ),
            ],
            state_aggregate_row=("1" if state == "NSW" else "2", state),
        )
        responses.add(
            responses.GET,
            f"{base}87310{product}_202603.xlsx",
            body=xlsx_bytes,
            status=200,
        )

    # Each LGA splits 60/40 across two SA2s. Build a correspondence:
    correspondence_map: dict[str, list[tuple[str, float]]] = {}
    sa2_pairs: dict[str, tuple[str, str]] = {}
    for i, (_, (_, lga_code)) in enumerate(state_lga_map.items()):
        sa2_a = f"{lga_code}{i:04d}"  # synthetic 9-digit-ish codes
        sa2_b = f"{lga_code}{i + 1000:04d}"
        correspondence_map[lga_code] = [(sa2_a, 0.6), (sa2_b, 0.4)]
        sa2_pairs[lga_code] = (sa2_a, sa2_b)

    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    ds.attach_correspondence(_make_synthetic_correspondence(correspondence_map))
    df = ds.load()

    # 8 LGAs × 2 SA2s each = 16 SA2 records
    assert len(df) == 16
    assert df.index.name == "sa2_code_2021"

    # Spot-check NSW LGA 10500: SA2_a gets 60% of values, SA2_b gets 40%
    nsw_sa2_a, nsw_sa2_b = sa2_pairs["10500"]
    assert df.loc[nsw_sa2_a, "new_houses_count"] == pytest.approx(60.0, abs=1e-6)
    assert df.loc[nsw_sa2_b, "new_houses_count"] == pytest.approx(40.0, abs=1e-6)
    # Per-LGA sum invariant: 60 + 40 = 100
    assert (
        df.loc[nsw_sa2_a, "new_houses_count"] + df.loc[nsw_sa2_b, "new_houses_count"]
    ) == pytest.approx(100.0, abs=1e-6)

    # Value column also downscales by the same shares
    assert df.loc[nsw_sa2_a, "value_total_building"] == pytest.approx(60_000.0, abs=1e-2)
    assert df.loc[nsw_sa2_b, "value_total_building"] == pytest.approx(40_000.0, abs=1e-2)

    # Reference FY attached
    assert (df["reference_financial_year"] == "2024-25").all()


@responses.activate
def test_load_filters_state_aggregate_rows(ba_lga_data_dir: Path) -> None:
    """The single state-level row (1-digit code) at the top of each
    cube must be skipped; only 5-digit LGA codes are kept.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    # Only NSW has real data
    nsw_bytes = _make_lga_state_xlsx(
        lga_records=[
            ("10500", {"new_houses_count": 100, "total_dwellings_count": 100}),
        ],
        state_aggregate_row=("1", "New South Wales"),
    )
    responses.add(responses.GET, f"{base}87310do004_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do008", "do012", "do016", "do020", "do024", "do028", "do032"):
        empty_bytes = _make_lga_state_xlsx(lga_records=[], state_aggregate_row=("2", "stub"))
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    ds.attach_correspondence(_make_synthetic_correspondence({"10500": [("206011001", 1.0)]}))
    df = ds.load()
    # Single SA2 record from the single LGA's full-share entry
    assert list(df.index) == ["206011001"]
    # State aggregate code "1" must not be in the index
    assert "1" not in df.index


@responses.activate
def test_load_warns_on_source_lga_not_in_correspondence(
    ba_lga_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An LGA in the ABS cube that isn't covered by the correspondence
    (e.g. recent boundary change) should warn loudly but not crash.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    # NSW has both 10500 (in correspondence) and 19999 (not in it).
    nsw_bytes = _make_lga_state_xlsx(
        lga_records=[
            ("10500", {"new_houses_count": 100}),
            ("19999", {"new_houses_count": 50}),
        ],
    )
    responses.add(responses.GET, f"{base}87310do004_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do008", "do012", "do016", "do020", "do024", "do028", "do032"):
        empty_bytes = _make_lga_state_xlsx(lga_records=[])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    ds.attach_correspondence(_make_synthetic_correspondence({"10500": [("206011001", 1.0)]}))
    with caplog.at_level("WARNING", logger="census_augment.datasets._abs_ba_lga"):
        df = ds.load()
    # 19999's value didn't contribute to any SA2; 10500's did
    assert df.loc["206011001", "new_houses_count"] == pytest.approx(100.0, abs=1e-6)
    assert any("not in the LGA-SA2 correspondence" in rec.message for rec in caplog.records)


@responses.activate
def test_parquet_cache_round_trip(ba_lga_data_dir: Path) -> None:
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    nsw_bytes = _make_lga_state_xlsx(
        lga_records=[
            ("10500", {"new_houses_count": 100, "total_dwellings_count": 100}),
        ],
    )
    responses.add(responses.GET, f"{base}87310do004_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do008", "do012", "do016", "do020", "do024", "do028", "do032"):
        empty_bytes = _make_lga_state_xlsx(lga_records=[])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    corr = _make_synthetic_correspondence({"10500": [("206011001", 1.0)]})
    ds.attach_correspondence(corr)
    df1 = ds.load()

    # Re-load — should hit parquet sidecar
    ds2 = AbsBaLgaDataSource(root=ba_lga_data_dir)
    ds2.attach_correspondence(corr)
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())


# ---- integration with real LgaSa2Correspondence -------------------------


@responses.activate
def test_load_with_real_compute_correspondence(ba_lga_data_dir: Path) -> None:
    """End-to-end smoke using a real ``compute_lga_sa2_correspondence``
    call (not a synthetic one) — exercises the integration with the
    correspondence module's actual geometric intersection.
    """
    responses.add(
        responses.GET,
        ABS_BA_LANDING_URL,
        body=_make_landing_html("202603"),
        status=200,
    )

    base = (
        "https://www.abs.gov.au/statistics/industry/building-and-construction/"
        "building-approvals-australia/mar-2026/"
    )
    nsw_bytes = _make_lga_state_xlsx(
        lga_records=[
            ("10500", {"new_houses_count": 100, "value_total_building": 50_000}),
        ],
    )
    responses.add(responses.GET, f"{base}87310do004_202603.xlsx", body=nsw_bytes, status=200)
    for product in ("do008", "do012", "do016", "do020", "do024", "do028", "do032"):
        empty_bytes = _make_lga_state_xlsx(lga_records=[])
        responses.add(
            responses.GET, f"{base}87310{product}_202603.xlsx", body=empty_bytes, status=200
        )

    # Build real geometries — LGA 10500 contains two SA2s split 70/30.
    test_crs = "EPSG:32755"
    sa2_gdf = gpd.GeoDataFrame(
        {"SA2_CODE21": ["206011001", "206011002"]},
        geometry=[
            Polygon([(0, 0), (70, 0), (70, 100), (0, 100)]),  # 70%
            Polygon([(70, 0), (100, 0), (100, 100), (70, 100)]),  # 30%
        ],
        crs=test_crs,
    )
    lga_gdf = gpd.GeoDataFrame(
        {"LGA_CODE25": ["10500"]},
        geometry=[Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])],
        crs=test_crs,
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2_gdf, lga=lga_gdf)

    ds = AbsBaLgaDataSource(root=ba_lga_data_dir)
    ds.attach_correspondence(corr)
    df = ds.load()

    # SA2 206011001 gets 70 new houses (70% of 100), 206011002 gets 30.
    assert df.loc["206011001", "new_houses_count"] == pytest.approx(70.0, abs=1e-3)
    assert df.loc["206011002", "new_houses_count"] == pytest.approx(30.0, abs=1e-3)
    # Per-LGA sum invariant for value too
    assert (
        df.loc["206011001", "value_total_building"] + df.loc["206011002", "value_total_building"]
    ) == pytest.approx(50_000.0, abs=1e-1)
