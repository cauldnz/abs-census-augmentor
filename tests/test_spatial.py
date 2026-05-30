"""Tests for census_augment.spatial."""

from __future__ import annotations

import math

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from census_augment.spatial import SpatialIndex, compute_sa2_areas_km2

# Points chosen to fall cleanly inside the three fixture polygons:
#   Sydney CBD       : lon 151.20-151.22, lat -33.87 to -33.85
#   North Sydney     : lon 151.19-151.21, lat -33.84 to -33.82
#   Eastern Suburbs  : lon 151.23-151.26, lat -33.89 to -33.86
SYDNEY_CBD_POINT = (-33.86, 151.21)  # (lat, lon)
NORTH_SYDNEY_POINT = (-33.83, 151.20)
EASTERN_SUBURBS_POINT = (-33.875, 151.245)
NYC_POINT = (40.7128, -74.0060)  # nowhere near Australia
RURAL_NSW_POINT = (-30.0, 145.0)  # in NSW but outside our polygons


# ---- construction ---------------------------------------------------------


def test_construct_with_valid_gdf(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    # No exception; sindex built eagerly.
    assert idx is not None


def test_construct_without_crs_raises(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    no_crs = fake_sa2_gdf.copy()
    no_crs = no_crs.set_crs(None, allow_override=True)
    with pytest.raises(ValueError, match="CRS"):
        SpatialIndex(no_crs)


def test_construct_unknown_code_column_raises(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    with pytest.raises(ValueError, match="code column"):
        SpatialIndex(fake_sa2_gdf, code_column="does_not_exist")


def test_construct_unknown_name_column_raises(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    with pytest.raises(ValueError, match="name column"):
        SpatialIndex(fake_sa2_gdf, name_column="does_not_exist")


# ---- lookup_one happy path ------------------------------------------------


def test_lookup_one_inside_sydney_cbd(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    code, name = idx.lookup_one(*SYDNEY_CBD_POINT)
    assert code == "117011326"
    assert name == "Sydney CBD"


def test_lookup_one_inside_north_sydney(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    code, name = idx.lookup_one(*NORTH_SYDNEY_POINT)
    assert code == "117011327"
    assert name == "North Sydney"


def test_lookup_one_inside_eastern_suburbs(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    code, name = idx.lookup_one(*EASTERN_SUBURBS_POINT)
    assert code == "117011328"
    assert name == "Eastern Suburbs"


# ---- lookup_one outside / null cases --------------------------------------


def test_lookup_one_outside_returns_none(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    assert idx.lookup_one(*RURAL_NSW_POINT) == (None, None)


def test_lookup_one_far_away_returns_none(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    assert idx.lookup_one(*NYC_POINT) == (None, None)


@pytest.mark.parametrize(
    "lat,lon",
    [
        (None, 151.21),
        (-33.86, None),
        (None, None),
        (math.nan, 151.21),
        (-33.86, math.nan),
    ],
)
def test_lookup_one_null_inputs(
    fake_sa2_gdf: gpd.GeoDataFrame, lat: float | None, lon: float | None
) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    assert idx.lookup_one(lat, lon) == (None, None)


# ---- lookup_many ----------------------------------------------------------


def test_lookup_many_returns_parallel_lists(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    lats = [SYDNEY_CBD_POINT[0], NORTH_SYDNEY_POINT[0], EASTERN_SUBURBS_POINT[0]]
    lons = [SYDNEY_CBD_POINT[1], NORTH_SYDNEY_POINT[1], EASTERN_SUBURBS_POINT[1]]
    codes, names = idx.lookup_many(lats, lons)
    assert codes == ["117011326", "117011327", "117011328"]
    assert names == ["Sydney CBD", "North Sydney", "Eastern Suburbs"]


def test_lookup_many_mixed_valid_invalid_outside(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    """Order must be preserved across all input categories."""
    idx = SpatialIndex(fake_sa2_gdf)
    lats = [SYDNEY_CBD_POINT[0], None, RURAL_NSW_POINT[0], EASTERN_SUBURBS_POINT[0]]
    lons = [SYDNEY_CBD_POINT[1], 151.0, RURAL_NSW_POINT[1], EASTERN_SUBURBS_POINT[1]]
    codes, names = idx.lookup_many(lats, lons)
    assert codes == ["117011326", None, None, "117011328"]
    assert names == ["Sydney CBD", None, None, "Eastern Suburbs"]


def test_lookup_many_empty_returns_empty(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    codes, names = idx.lookup_many([], [])
    assert codes == []
    assert names == []


def test_lookup_many_all_null_returns_all_none(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    codes, names = idx.lookup_many([None, None], [None, None])
    assert codes == [None, None]
    assert names == [None, None]


def test_lookup_many_length_mismatch_raises(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    idx = SpatialIndex(fake_sa2_gdf)
    with pytest.raises(ValueError, match="equal length"):
        idx.lookup_many([1.0, 2.0], [3.0])


# ---- CRS reprojection -----------------------------------------------------


def test_reprojection_input_4326_against_7844_boundaries(
    fake_sa2_gdf: gpd.GeoDataFrame,
) -> None:
    """Input lat/lon are in EPSG:4326; boundaries are in EPSG:7844.

    For points in Australia, WGS84 → GDA2020 differs by only a few metres,
    so a CBD point looked up via 4326 should still land in the CBD polygon.
    """
    assert fake_sa2_gdf.crs is not None
    assert "GDA2020" in (fake_sa2_gdf.crs.name or "").upper()

    idx = SpatialIndex(fake_sa2_gdf, input_crs="EPSG:4326")
    code, _ = idx.lookup_one(*SYDNEY_CBD_POINT)
    assert code == "117011326"


def test_custom_input_crs(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    """Same coordinates interpreted in EPSG:7844 should also work."""
    idx = SpatialIndex(fake_sa2_gdf, input_crs="EPSG:7844")
    code, _ = idx.lookup_one(*SYDNEY_CBD_POINT)
    assert code == "117011326"


# ---- custom column names --------------------------------------------------


def test_custom_code_and_name_columns() -> None:
    """The defaults assume ABS SA2 names; verify a different schema works."""
    polygons = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
    ]
    custom = gpd.GeoDataFrame(
        {
            "MY_CODE": ["A", "B"],
            "MY_NAME": ["First", "Second"],
            "geometry": polygons,
        },
        crs="EPSG:4326",
    )
    idx = SpatialIndex(
        custom,
        code_column="MY_CODE",
        name_column="MY_NAME",
        input_crs="EPSG:4326",
    )
    code, name = idx.lookup_one(0.5, 0.5)
    assert code == "A"
    assert name == "First"

    code2, _ = idx.lookup_one(0.5, 2.5)
    assert code2 == "B"


# ---- compute_sa2_areas_km2 helper ----------------------------------------


def test_compute_sa2_areas_km2_basic(fake_sa2_gdf: gpd.GeoDataFrame) -> None:
    """Returns a dict of SA2 code → area in km², covering every SA2 in
    the input GeoDataFrame. Areas are positive floats.
    """
    areas = compute_sa2_areas_km2(fake_sa2_gdf, code_column="SA2_CODE21")
    assert len(areas) == len(fake_sa2_gdf)
    assert set(areas.keys()) == set(fake_sa2_gdf["SA2_CODE21"].astype(str))
    for code, area in areas.items():
        assert isinstance(area, float)
        assert area > 0.0, f"non-positive area for {code}: {area}"


def test_compute_sa2_areas_km2_albers_projection_sanity() -> None:
    """A 1° × 1° polygon at Australian latitudes is ~10,400 km². Test
    the function reprojects to EPSG:3577 (Australian Albers) and
    returns an order-of-magnitude-correct value. Precision isn't the
    goal; sanity is.
    """
    polygon = Polygon([(145.0, -32.0), (146.0, -32.0), (146.0, -31.0), (145.0, -31.0)])
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": ["TEST_001"]},
        geometry=[polygon],
        crs="EPSG:4326",
    )
    areas = compute_sa2_areas_km2(boundaries, code_column="SA2_CODE21")
    area = areas["TEST_001"]
    # Allow ±15% for projection distortion at this latitude.
    assert 8_000 < area < 13_000, f"expected ~10,400 km² for 1° box at 32°S, got {area:.0f}"


def test_compute_sa2_areas_km2_unknown_code_column_raises() -> None:
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": ["A"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        crs="EPSG:4326",
    )
    with pytest.raises(ValueError, match="code column 'NOT_THERE' not found"):
        compute_sa2_areas_km2(boundaries, code_column="NOT_THERE")


def test_compute_sa2_areas_km2_no_crs_raises() -> None:
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": ["A"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
    )
    with pytest.raises(ValueError, match="must have a CRS"):
        compute_sa2_areas_km2(boundaries, code_column="SA2_CODE21")


def test_compute_sa2_areas_km2_skips_null_geometry() -> None:
    """Issue #101: real ABS boundary releases include a handful of pseudo-SA2s
    (off-shore, migratory, "No usual address") with no geometry. The helper
    must skip them silently rather than raising ``AttributeError`` on
    ``None.area``. Affected SA2s are simply absent from the returned dict.
    """
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": ["REAL_001", "NULL_001", "REAL_002"]},
        geometry=[
            Polygon([(145.0, -32.0), (146.0, -32.0), (146.0, -31.0), (145.0, -31.0)]),
            None,
            Polygon([(146.0, -32.0), (147.0, -32.0), (147.0, -31.0), (146.0, -31.0)]),
        ],
        crs="EPSG:4326",
    )
    areas = compute_sa2_areas_km2(boundaries, code_column="SA2_CODE21")
    # Null-geometry SA2 omitted; real ones present.
    assert set(areas) == {"REAL_001", "REAL_002"}
    assert areas["REAL_001"] > 0
    assert areas["REAL_002"] > 0


def test_compute_sa2_areas_km2_skips_empty_geometry() -> None:
    """Empty geometries (``Polygon()``) are also skipped — they have ``.area = 0``
    but represent the same "no spatial extent" pseudo-SA2 case as ``None``.
    Letting them through would produce ``0.0`` areas which divide-by-zero
    downstream when computing density.
    """
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": ["REAL_001", "EMPTY_001"]},
        geometry=[
            Polygon([(145.0, -32.0), (146.0, -32.0), (146.0, -31.0), (145.0, -31.0)]),
            Polygon(),
        ],
        crs="EPSG:4326",
    )
    areas = compute_sa2_areas_km2(boundaries, code_column="SA2_CODE21")
    assert "EMPTY_001" not in areas
    assert areas["REAL_001"] > 0


def test_compute_sa2_areas_km2_warns_on_many_nulls(caplog: pytest.LogCaptureFixture) -> None:
    """If a non-trivial fraction of boundaries lack geometry, emit a WARNING.
    Real ABS releases have only ~5-15 pseudo-SA2s out of ~2,300+. Anything
    more suggests a corrupted boundary file or a CRS interaction, and is
    worth surfacing.
    """
    # 100 SA2s, 60 with null geometry — well over the >max(50, total/100) threshold.
    real_poly = Polygon([(145.0, -32.0), (146.0, -32.0), (146.0, -31.0), (145.0, -31.0)])
    codes = [f"SA2_{i:03d}" for i in range(100)]
    geoms: list[object] = [real_poly if i < 40 else None for i in range(100)]
    boundaries = gpd.GeoDataFrame(
        {"SA2_CODE21": codes},
        geometry=geoms,
        crs="EPSG:4326",
    )
    with caplog.at_level("WARNING", logger="census_augment.spatial"):
        areas = compute_sa2_areas_km2(boundaries, code_column="SA2_CODE21")
    assert len(areas) == 40
    assert any("null/empty geometry" in rec.message for rec in caplog.records)
