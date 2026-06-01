"""Spatial join: lat/lon → SA2 code/name (spec §7.3).

Wraps a SA2-boundary GeoDataFrame with a spatial index so per-point
lookups are fast. Input coordinates are assumed to be EPSG:4326 (WGS84)
per spec §14 #7; they are reprojected to the boundary CRS (typically
EPSG:7844 / GDA2020) before the join.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

_log = logging.getLogger(__name__)


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


# ---- SA2 area lookup ------------------------------------------------------
#
# Australia-wide equal-area projection used for area calculations:
# Albers Equal Area Conic (EPSG:3577 / GDA94 Australian Albers). Standard
# choice for area-preserving Australia-scale geo work — distorts shape but
# preserves area within ~0.01% across the continent. Geopandas reprojects
# to this CRS before computing geometry .area, which returns square metres.


def compute_sa2_areas_km2(
    boundaries: gpd.GeoDataFrame,
    *,
    code_column: str = "SA2_CODE21",
) -> dict[str, float]:
    """Build an SA2-code → area-in-km² lookup from a boundary GeoDataFrame.

    Reprojects to EPSG:3577 (Australian Albers Equal Area Conic) so the
    geometry ``.area`` is in square metres and area-preserving across the
    continent. Squared metres are converted to km².

    Args:
        boundaries: GeoDataFrame containing SA2 polygons + a CRS + a code
            column. Typically the same boundary GDF a :class:`SpatialIndex`
            is built from.
        code_column: Column name holding the SA2 code. Defaults to the
            current ASGS Edition 3 convention; pass ``SA2_MAIN16`` for
            Edition 2 boundaries or ``SA2_MAIN11`` for Edition 1.

    Returns:
        Dict mapping SA2 code (as ``str``) to area in km² (``float``).
        Areas range from <1 km² (inner-city SA2s) to >50,000 km² (remote
        SA2s); the function preserves the full range without bucketing.

    SA2s with null geometry are omitted from the returned mapping. Real
    ABS boundary releases include a handful of pseudo-SA2s — off-shore,
    migratory, "No usual address" rows — that carry no geometry by
    design (issue #101). Density downstream falls back to NaN for those
    codes, which is the right behaviour (no area → no density).

    Used by :class:`ErpDataSource` to compute
    ``ERP.population_density_per_km2`` = ``population_total / area_km2``.
    """
    if code_column not in boundaries.columns:
        raise ValueError(
            f"code column {code_column!r} not found in boundaries; got: {list(boundaries.columns)}"
        )
    if boundaries.crs is None:
        raise ValueError("boundaries GeoDataFrame must have a CRS")
    # EPSG:3577 — GDA94 / Australian Albers. Equal-area projection.
    in_equal_area = boundaries.to_crs("EPSG:3577")

    areas: dict[str, float] = {}
    null_codes: list[str] = []
    for code, geom in zip(in_equal_area[code_column], in_equal_area.geometry, strict=False):
        if geom is None or getattr(geom, "is_empty", False):
            null_codes.append(str(code))
            continue
        areas[str(code)] = float(geom.area / 1_000_000.0)

    if null_codes:
        total = len(in_equal_area)
        # ABS typically has ~5-15 pseudo-SA2s per edition out of ~2,300-2,500.
        # Warn loudly if it's a lot more than that — likely a corrupted boundary
        # file or a wrong CRS interaction, not just the usual pseudo-rows.
        if total and len(null_codes) > max(50, total // 100):
            _log.warning(
                "compute_sa2_areas_km2: %d/%d boundaries had null/empty geometry "
                "(sample: %s) — that's a higher fraction than the usual ABS pseudo-SA2s. "
                "Worth checking the boundary file.",
                len(null_codes),
                total,
                null_codes[:5],
            )
        else:
            _log.debug(
                "compute_sa2_areas_km2: skipped %d SA2(s) with null/empty geometry "
                "(sample: %s) — expected for off-shore / migratory / 'No usual "
                "address' pseudo-SA2s in real ABS boundary releases.",
                len(null_codes),
                null_codes[:5],
            )
    return areas


# ---- SA2 parent-geography lookup -----------------------------------------
#
# ABS Edition 3 SA2 boundaries carry the ASGS hierarchy as attribute columns
# alongside SA2_CODE21/SA2_NAME21: SA3_CODE21, SA4_CODE21, GCC_CODE21,
# STE_CODE21, and the matching _NAME variants. Every SA2 belongs to exactly
# one parent at each level, so we can build cheap O(1) SA2 -> parent lookup
# dicts straight from the boundary attribute table — no separate boundary
# fetch needed.
#
# Used by the cross-level downscale pattern: when a dataset is published at
# SA3 / SA4 / GCC / STE level, look up the SA2's parent code via these
# dicts and join. Every SA2 inside the same parent inherits the parent's
# value (no within-parent variation, no weighting needed because SA3/SA4/
# GCC/STE are strict aggregations of SA2 in ASGS).
#
# LGAs are NOT in the ASGS hierarchy and DO cross SA2 boundaries — they need
# a separate spatial-intersection correspondence (handled in
# `correspondence.py`, not here).


def compute_sa2_parent_codes(
    boundaries: gpd.GeoDataFrame,
    *,
    sa2_code_column: str = "SA2_CODE21",
    parent_code_columns: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Build SA2 -> parent-code lookup dicts from a boundary GeoDataFrame.

    The ABS boundary file already carries the ASGS hierarchy (SA3 / SA4 /
    GCC / STE) as attribute columns. This helper turns those columns into
    per-level ``{sa2_code: parent_code}`` dicts so cross-level data can be
    joined onto SA2 rows by looking up the SA2's parent code at the level
    the source dataset is keyed at.

    Args:
        boundaries: GeoDataFrame containing SA2 polygons + a code column +
            the parent-geography columns named by ``parent_code_columns``.
        sa2_code_column: Column holding the SA2 code. Defaults to the
            ASGS Edition 3 convention.
        parent_code_columns: Mapping from a parent-level label (e.g.
            ``"SA3"``, ``"SA4"``, ``"GCC"``, ``"STE"``) to the boundary
            column that holds that level's code. Defaults to the four
            Edition 3 attributes: ``{"SA3": "SA3_CODE21", "SA4":
            "SA4_CODE21", "GCC": "GCC_CODE21", "STE": "STE_CODE21"}``.
            Pass an empty dict to short-circuit and return ``{}``.

    Returns:
        A dict of per-level dicts: ``{"SA3": {sa2_code: sa3_code, ...},
        "SA4": {...}, ...}``. SA2s with a null parent code are omitted from
        the inner dict for that level (real ABS data has a handful — the
        same pseudo-SA2s that lack geometry).

    Raises:
        ValueError: if ``sa2_code_column`` or any value in
        ``parent_code_columns`` is missing from the boundary GDF.

    Used by the cross-level downscale pattern in dataset enrichers (see
    e.g. ``AihwMentalHealthPrescriptionsDataSource``, which keys on SA4).
    """
    if parent_code_columns is None:
        # Edition 3 defaults — matches the SA2_2021_AUST_SHP_GDA2020 file
        # attribute schema. Older editions use _MAIN16 / _MAIN11 suffixes;
        # callers handling 2016 / 2011 boundaries pass the right names.
        parent_code_columns = {
            "SA3": "SA3_CODE21",
            "SA4": "SA4_CODE21",
            "GCC": "GCC_CODE21",
            "STE": "STE_CODE21",
        }

    if sa2_code_column not in boundaries.columns:
        raise ValueError(
            f"SA2 code column {sa2_code_column!r} not found in boundaries; "
            f"got: {list(boundaries.columns)}"
        )

    missing = [c for c in parent_code_columns.values() if c not in boundaries.columns]
    if missing:
        raise ValueError(
            f"parent code column(s) {missing!r} not found in boundaries; "
            f"got: {list(boundaries.columns)}. For ABS Edition 3 boundary "
            f"files the attributes are SA3_CODE21 / SA4_CODE21 / GCC_CODE21 "
            f"/ STE_CODE21; older editions use the matching _MAIN16 / _MAIN11 "
            f"columns."
        )

    out: dict[str, dict[str, str]] = {label: {} for label in parent_code_columns}
    sa2_series = boundaries[sa2_code_column]
    for level, col in parent_code_columns.items():
        parent_series = boundaries[col]
        level_dict = out[level]
        for sa2_code, parent_code in zip(sa2_series, parent_series, strict=False):
            if sa2_code is None or parent_code is None:
                continue
            sa2_str = str(sa2_code)
            parent_str = str(parent_code)
            if not sa2_str or not parent_str or parent_str.lower() == "nan":
                continue
            level_dict[sa2_str] = parent_str
    return out
