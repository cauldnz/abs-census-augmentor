"""Shared test fixtures used across the suite.

These are pytest fixtures (auto-discovered by pytest from a top-level
``conftest.py``); test files reference them by name without imports.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon


# Three synthetic SA2 polygons covering parts of inner Sydney. Coordinates
# are in EPSG:7844 (GDA2020) to match the real ABS boundary CRS.
_FAKE_SA2_RECORDS = [
    {
        "SA2_CODE21": "117011326",
        "SA2_NAME21": "Sydney CBD",
        "polygon": [
            (151.20, -33.87),
            (151.22, -33.87),
            (151.22, -33.85),
            (151.20, -33.85),
        ],
    },
    {
        "SA2_CODE21": "117011327",
        "SA2_NAME21": "North Sydney",
        "polygon": [
            (151.19, -33.84),
            (151.21, -33.84),
            (151.21, -33.82),
            (151.19, -33.82),
        ],
    },
    {
        "SA2_CODE21": "117011328",
        "SA2_NAME21": "Eastern Suburbs",
        "polygon": [
            (151.23, -33.89),
            (151.26, -33.89),
            (151.26, -33.86),
            (151.23, -33.86),
        ],
    },
]


@pytest.fixture
def fake_sa2_gdf() -> gpd.GeoDataFrame:
    """Three-polygon synthetic SA2 GeoDataFrame in EPSG:7844 (GDA2020)."""
    return gpd.GeoDataFrame(
        {
            "SA2_CODE21": [r["SA2_CODE21"] for r in _FAKE_SA2_RECORDS],
            "SA2_NAME21": [r["SA2_NAME21"] for r in _FAKE_SA2_RECORDS],
            "geometry": [Polygon(r["polygon"]) for r in _FAKE_SA2_RECORDS],
        },
        crs="EPSG:7844",
    )


@pytest.fixture
def fake_boundary_zip_bytes(
    tmp_path: Path, fake_sa2_gdf: gpd.GeoDataFrame
) -> bytes:
    """In-memory ZIP containing a single fake SA2 GeoPackage."""
    gpkg_path = tmp_path / "_fixture_boundary.gpkg"
    fake_sa2_gdf.to_file(gpkg_path, driver="GPKG")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(gpkg_path, arcname="SA2_2021_AUST_GDA2020.gpkg")
    return buf.getvalue()
