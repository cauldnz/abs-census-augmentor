"""Spatial join: lat/lon → SA2 code/name (spec §7.3).

Wraps a SA2-boundary GeoDataFrame with a spatial index so per-point
lookups are fast. Input coordinates are assumed to be EPSG:4326 (WGS84)
per spec §14 #7; they are reprojected to the boundary CRS (typically
EPSG:7844 / GDA2020) before the join.
"""

from __future__ import annotations

from collections.abc import Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point


class SpatialIndex:
    """Point-in-polygon SA2 lookup over a boundary GeoDataFrame.

    Build once per run (the index is materialised in the constructor),
    then call :meth:`lookup_one` or :meth:`lookup_many` for each point /
    batch.

    Args:
        boundaries: GeoDataFrame with a CRS and the columns named by
            ``code_column`` / ``name_column`` (defaults match real ABS
            SA2 shapefiles).
        code_column: Column holding the SA2 code (e.g. ``SA2_CODE21``).
        name_column: Column holding the SA2 name (e.g. ``SA2_NAME21``).
        input_crs: CRS of the lat/lon values passed to lookup methods.
            Default ``EPSG:4326`` (WGS84) per spec §14 #7.
    """

    def __init__(
        self,
        boundaries: gpd.GeoDataFrame,
        *,
        code_column: str = "SA2_CODE21",
        name_column: str = "SA2_NAME21",
        input_crs: str = "EPSG:4326",
    ) -> None:
        if boundaries.crs is None:
            raise ValueError("boundaries GeoDataFrame must have a CRS")
        if code_column not in boundaries.columns:
            raise ValueError(
                f"code column {code_column!r} not found in boundaries; "
                f"got: {list(boundaries.columns)}"
            )
        if name_column not in boundaries.columns:
            raise ValueError(
                f"name column {name_column!r} not found in boundaries; "
                f"got: {list(boundaries.columns)}"
            )
        # Slice to just the columns we need to keep memory + sjoin output
        # tidy. Geopandas' .sindex is built on first access; force it now
        # so the cost is paid once at construction time.
        self._boundaries: gpd.GeoDataFrame = boundaries[
            [code_column, name_column, "geometry"]
        ].copy()
        _ = self._boundaries.sindex
        self._code_col = code_column
        self._name_col = name_column
        self._input_crs = input_crs

    def lookup_one(self, lat: float | None, lon: float | None) -> tuple[str | None, str | None]:
        """Single-point lookup. Returns ``(None, None)`` for null inputs or
        points outside any SA2."""
        codes, names = self.lookup_many([lat], [lon])
        return codes[0], names[0]

    def lookup_many(
        self,
        lats: Sequence[float | None],
        lons: Sequence[float | None],
    ) -> tuple[list[str | None], list[str | None]]:
        """Vectorised lookup. Returns parallel ``(codes, names)`` lists.

        Null inputs (``None`` / ``NaN``) and points outside any SA2 both
        produce ``None`` in both output positions, preserving the input
        order.
        """
        if len(lats) != len(lons):
            raise ValueError(
                f"lats and lons must have equal length; got {len(lats)} and {len(lons)}"
            )

        n = len(lats)
        codes_out: list[str | None] = [None] * n
        names_out: list[str | None] = [None] * n
        if n == 0:
            return codes_out, names_out

        valid_indices: list[int] = []
        valid_points: list[tuple[float, float]] = []
        for i in range(n):
            lat = lats[i]
            lon = lons[i]
            if lat is None or lon is None:
                continue
            if pd.isna(lat) or pd.isna(lon):
                continue
            valid_indices.append(i)
            valid_points.append((float(lat), float(lon)))

        if not valid_points:
            return codes_out, names_out

        points_gdf = gpd.GeoDataFrame(
            {"_idx": list(range(len(valid_points)))},
            geometry=[Point(lon, lat) for lat, lon in valid_points],
            crs=self._input_crs,
        ).to_crs(self._boundaries.crs)

        joined = (
            points_gdf.sjoin(self._boundaries, how="left", predicate="within")
            # SA2s are non-overlapping, but a point on a shared edge could
            # in principle match both — keep the first deterministically.
            .drop_duplicates(subset="_idx", keep="first")
            .sort_values("_idx")
            .reset_index(drop=True)
        )

        for i, orig_idx in enumerate(valid_indices):
            row = joined.iloc[i]
            code = row[self._code_col]
            name = row[self._name_col]
            if pd.isna(code):
                continue  # leave (None, None) — point outside any SA2
            codes_out[orig_idx] = str(code)
            names_out[orig_idx] = str(name)

        return codes_out, names_out
