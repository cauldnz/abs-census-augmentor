"""Tests for census_augment.correspondence (LGA-SA2 spatial cross-walk).

Uses synthetic axis-aligned rectangle geometries so the area weights are
easy to reason about by hand. Real ABS boundaries are smoke-tested via
the live-data probe (``tools/probe_new_datasets.py``); for hermetic CI
testing, fabricated rectangles with known overlaps are simpler and
faster.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from census_augment.correspondence import (
    LgaSa2Correspondence,
    compute_lga_sa2_correspondence,
    load_correspondence,
    save_correspondence,
)

# Use a near-equatorial UTM zone (EPSG:32755) so axis-aligned rectangles
# in metres-like units have predictable, no-distortion intersection
# areas. The correspondence helper reprojects to EPSG:3577 internally,
# but at these latitudes the area change is sub-percent so the assertions
# can use the input-space areas directly.
_TEST_CRS = "EPSG:32755"


def _rect(xmin: float, ymin: float, xmax: float, ymax: float) -> Polygon:
    return Polygon([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)])


def _sa2_gdf(rows: list[tuple[str, Polygon]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"SA2_CODE21": [r[0] for r in rows]},
        geometry=[r[1] for r in rows],
        crs=_TEST_CRS,
    )


def _lga_gdf(rows: list[tuple[str, Polygon]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"LGA_CODE25": [r[0] for r in rows]},
        geometry=[r[1] for r in rows],
        crs=_TEST_CRS,
    )


# ---- Construction ---------------------------------------------------------


def test_compute_correspondence_one_to_one_nested() -> None:
    """An SA2 entirely inside one LGA: both weight directions are 1.0
    for that pair (modulo tiny CRS reprojection rounding).
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])  # large enclosing LGA
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # One intersection
    assert len(corr.weights) == 1
    row = corr.weights.iloc[0]
    assert row["sa2_code"] == "S1"
    assert row["lga_code"] == "L1"
    # SA2 entirely inside LGA: lga_share_of_sa2 == 1
    assert row["lga_share_of_sa2"] == pytest.approx(1.0, abs=1e-4)
    # SA2 is much smaller than LGA: sa2_share_of_lga is small
    assert row["sa2_share_of_lga"] < 0.01

    # Lookup helpers
    assert corr.lgas_for_sa2("S1") == pytest.approx({"L1": 1.0}, abs=1e-4)
    sa2_share = corr.sa2s_for_lga("L1")
    assert set(sa2_share) == {"S1"}
    assert sa2_share["S1"] < 0.01


def test_compute_correspondence_one_sa2_split_across_two_lgas() -> None:
    """An SA2 split 60/40 between two LGAs (along an internal LGA
    boundary). Both lga_share_of_sa2 weights are exact area fractions.
    """
    # SA2 is a 100×100 rectangle. LGA A covers the western 60×100,
    # LGA B covers the eastern 40×100. They don't overlap each other.
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf(
        [
            ("LA", _rect(0, 0, 60, 100)),
            ("LB", _rect(60, 0, 100, 100)),
        ]
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # Two intersection rows for S1
    assert len(corr.weights) == 2
    sa2_to_lga = corr.lgas_for_sa2("S1")
    assert sa2_to_lga["LA"] == pytest.approx(0.6, abs=1e-3)
    assert sa2_to_lga["LB"] == pytest.approx(0.4, abs=1e-3)
    # The LGAs are exactly covered by S1, so sa2_share_of_lga is 1.0 each.
    assert corr.sa2s_for_lga("LA") == pytest.approx({"S1": 1.0}, abs=1e-3)
    assert corr.sa2s_for_lga("LB") == pytest.approx({"S1": 1.0}, abs=1e-3)


def test_compute_correspondence_one_lga_split_across_two_sa2s() -> None:
    """An LGA containing parts of two SA2s. Tests the opposite weight
    direction — sa2_share_of_lga should sum to 1 within the LGA.
    """
    sa2 = _sa2_gdf(
        [
            ("SA", _rect(0, 0, 100, 100)),
            ("SB", _rect(100, 0, 200, 100)),
        ]
    )
    # LGA covers the eastern 60 of SA + the western 80 of SB.
    lga = _lga_gdf([("L1", _rect(40, 0, 180, 100))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # Weight check: SA contributes 60×100 of L1's 140×100 area = 60/140;
    # SB contributes 80×100 of L1 = 80/140. Sum to 1.
    sa2_in_l1 = corr.sa2s_for_lga("L1")
    assert sa2_in_l1["SA"] == pytest.approx(60 / 140, abs=1e-3)
    assert sa2_in_l1["SB"] == pytest.approx(80 / 140, abs=1e-3)
    assert sum(sa2_in_l1.values()) == pytest.approx(1.0, abs=1e-3)


def test_compute_correspondence_drops_slivers() -> None:
    """Two boundaries that share a near-coincident edge with tiny numerical
    overlap should not produce a spurious correspondence row. (The
    default min_area_m2 is 1 m².)
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    # LGA shares the EAST edge of S1 but extends slightly INTO it by a
    # nanometre-ish amount (a 0.001 m wide sliver). At min_area_m2=1.0
    # this should be dropped.
    lga = _lga_gdf(
        [
            ("L_main", _rect(-500, -500, 50, 500)),  # large overlap
            ("L_sliver", _rect(99.999, 0, 200, 100)),  # 0.001 × 100 = 0.1 m² overlap
        ]
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    # Sliver dropped — only the large-overlap LGA remains
    assert set(corr.lgas_for_sa2("S1")) == {"L_main"}


def test_compute_correspondence_auto_detects_lga_code_column() -> None:
    """When lga_code_column is None (the default), auto-detect from
    columns starting with ``LGA_CODE`` — handles LGA_CODE21..LGA_CODE25.
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = gpd.GeoDataFrame(
        {"LGA_CODE23": ["L1"]},  # 2023 boundary release
        geometry=[_rect(-1000, -1000, 1000, 1000)],
        crs=_TEST_CRS,
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    assert corr.lga_code_column == "LGA_CODE23"
    assert corr.lgas_for_sa2("S1") == pytest.approx({"L1": 1.0}, abs=1e-4)


# ---- Error paths ---------------------------------------------------------


def test_compute_correspondence_missing_crs_raises() -> None:
    sa2 = gpd.GeoDataFrame(
        {"SA2_CODE21": ["S1"]},
        geometry=[_rect(0, 0, 100, 100)],
    )  # no CRS
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    with pytest.raises(ValueError, match="SA2 boundary GeoDataFrame must have a CRS"):
        compute_lga_sa2_correspondence(sa2=sa2, lga=lga)


def test_compute_correspondence_missing_sa2_code_column_raises() -> None:
    sa2 = gpd.GeoDataFrame(
        {"some_other_col": ["S1"]},
        geometry=[_rect(0, 0, 100, 100)],
        crs=_TEST_CRS,
    )
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    with pytest.raises(ValueError, match="SA2 code column 'SA2_CODE21' not found"):
        compute_lga_sa2_correspondence(sa2=sa2, lga=lga)


def test_compute_correspondence_unrecognised_lga_columns_raises() -> None:
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = gpd.GeoDataFrame(
        {"PROVINCE_CODE": ["L1"]},  # not LGA_CODE*
        geometry=[_rect(-1000, -1000, 1000, 1000)],
        crs=_TEST_CRS,
    )
    with pytest.raises(ValueError, match="Could not auto-detect LGA code column"):
        compute_lga_sa2_correspondence(sa2=sa2, lga=lga)


def test_compute_correspondence_drops_null_geometry() -> None:
    """SA2s or LGAs with null geometry (the pseudo-rows in real ABS data)
    are dropped silently — they simply don't participate in the
    correspondence. Mirrors the handling in compute_sa2_areas_km2.
    """
    sa2 = gpd.GeoDataFrame(
        {"SA2_CODE21": ["S_real", "S_null"]},
        geometry=[_rect(0, 0, 100, 100), None],
        crs=_TEST_CRS,
    )
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    # Only the real SA2 is in the correspondence.
    assert set(corr.weights["sa2_code"]) == {"S_real"}


def test_compute_correspondence_no_overlap_raises() -> None:
    """When the two inputs don't overlap anywhere, raise loudly — that's
    almost certainly a CRS or country mismatch.
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf([("L1", _rect(10000, 10000, 11000, 11000))])  # far away
    with pytest.raises(ValueError, match="intersection is empty"):
        compute_lga_sa2_correspondence(sa2=sa2, lga=lga)


# ---- Downscale helpers ---------------------------------------------------


def test_downscale_counts_sums_correctly_within_lga() -> None:
    """For a count value, the sum across an LGA's SA2s should equal the
    LGA's value (modulo edge slivers).
    """
    # LGA covers two SA2s: SA gets 70%, SB gets 30% (by area).
    sa2 = _sa2_gdf(
        [
            ("SA", _rect(0, 0, 70, 100)),
            ("SB", _rect(70, 0, 100, 100)),
        ]
    )
    lga = _lga_gdf([("L1", _rect(0, 0, 100, 100))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # LGA reports 500 building approvals.
    downscaled = corr.downscale_counts({"L1": 500.0})
    # SA gets 500 * 0.70 = 350, SB gets 500 * 0.30 = 150. Sum = 500.
    assert downscaled["SA"] == pytest.approx(350.0, abs=1e-2)
    assert downscaled["SB"] == pytest.approx(150.0, abs=1e-2)
    assert sum(downscaled.values()) == pytest.approx(500.0, abs=1e-2)


def test_downscale_counts_ignores_unknown_lga_keys() -> None:
    """LGAs in the values dict that aren't in the correspondence are
    silently ignored. Useful when the user's LGA dataset covers a
    state we don't have boundaries for.
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    out = corr.downscale_counts({"L1": 100.0, "L_unknown": 999_999.0})
    assert out["S1"] == pytest.approx(100.0 * corr.weights.iloc[0]["sa2_share_of_lga"], abs=1e-2)


def test_downscale_counts_empty_when_no_lga_matches() -> None:
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    out = corr.downscale_counts({"L_unknown": 999.0})
    assert out == {}


def test_downscale_rates_weighted_average_for_split_sa2() -> None:
    """For a rate value, an SA2 split across two LGAs gets the weighted
    average of those LGAs' values, weighted by lga_share_of_sa2.
    """
    # SA2 split 60/40 between LGA A and LGA B (same as the earlier test).
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf(
        [
            ("LA", _rect(0, 0, 60, 100)),
            ("LB", _rect(60, 0, 100, 100)),
        ]
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # LGA A reports 80 per 1000; LGA B reports 50 per 1000.
    out = corr.downscale_rates({"LA": 80.0, "LB": 50.0})
    # Weighted average: 0.6 * 80 + 0.4 * 50 = 68.
    assert out["S1"] == pytest.approx(68.0, abs=1e-2)


def test_downscale_rates_renormalises_under_partial_coverage() -> None:
    """If only some of an SA2's overlapping LGAs are in the values dict,
    weights renormalise over the covered LGAs (no implicit zero-fill).
    """
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf(
        [
            ("LA", _rect(0, 0, 60, 100)),
            ("LB", _rect(60, 0, 100, 100)),
        ]
    )
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)

    # Only LGA A has a value. Renormalise: SA2's downscaled value =
    # LGA A's value (no inferred zero for LGA B).
    out = corr.downscale_rates({"LA": 80.0})
    assert out["S1"] == pytest.approx(80.0, abs=1e-2)


def test_downscale_rates_empty_when_no_match() -> None:
    sa2 = _sa2_gdf([("S1", _rect(0, 0, 100, 100))])
    lga = _lga_gdf([("L1", _rect(-1000, -1000, 1000, 1000))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    out = corr.downscale_rates({"L_unknown": 80.0})
    assert out == {}


# ---- Disk cache round-trip ------------------------------------------------


def test_save_load_round_trip(tmp_path: Path) -> None:
    """``save_correspondence`` followed by ``load_correspondence`` returns
    a structurally-equivalent correspondence object — downscale results
    match.
    """
    sa2 = _sa2_gdf(
        [
            ("SA", _rect(0, 0, 70, 100)),
            ("SB", _rect(70, 0, 100, 100)),
        ]
    )
    lga = _lga_gdf([("L1", _rect(0, 0, 100, 100))])
    corr = compute_lga_sa2_correspondence(sa2=sa2, lga=lga)
    out_path = tmp_path / "lga-sa2.parquet"
    save_correspondence(corr, out_path)
    assert out_path.exists()

    loaded = load_correspondence(out_path)
    assert isinstance(loaded, LgaSa2Correspondence)
    # Both directions still work identically.
    downscaled = loaded.downscale_counts({"L1": 500.0})
    assert downscaled["SA"] == pytest.approx(350.0, abs=1e-2)
    assert downscaled["SB"] == pytest.approx(150.0, abs=1e-2)
