"""LGA-SA2 spatial correspondence (spec §20.7 Strategy 2).

LGAs do **not** nest into SA2s — they overlap. A single SA2 can be
split across multiple LGAs; a single LGA can span multiple SA2s.
Joining LGA-keyed data onto SA2 rows therefore requires a real spatial
correspondence — for each (SA2, LGA) pair that overlap, compute the
intersection area and the implied weight.

This module builds the correspondence on demand from the two boundary
GeoDataFrames (SA2 + LGA) and caches it to disk as a parquet sidecar,
so the geometric intersection runs at most once per (SA2 release, LGA
release) pair.

**Two weight directions** are computed because the right one depends
on the data's units:

- **SA2's share of an LGA** (``lga_to_sa2_weights[lga_code][sa2_code]``
  → fraction in 0..1): used for **count downscale**. If an LGA reports
  e.g. 500 building approvals, and an SA2 covers 30% of that LGA's
  area, the SA2 gets 500 × 0.30 = 150 approvals.
- **LGA's share of an SA2** (``sa2_to_lga_weights[sa2_code][lga_code]``
  → fraction in 0..1): used for **rate / intensity downscale**. If an
  SA2 is split across two LGAs (60% in LGA A reporting 80 units/km² and
  40% in LGA B reporting 50 units/km²), the SA2's downscaled rate is
  0.60 × 80 + 0.40 × 50 = 68 units/km².

Both directions are derived from the same intersection step and exposed
as separate methods on :class:`LgaSa2Correspondence`. Tiny slivers
(intersections under :data:`_MIN_AREA_M2`) are dropped to suppress
numerical noise from boundary digitisation differences between the SA2
and LGA shapefiles.

Performance: geometric intersection of ~2,500 SA2s × 567 LGAs is the
expensive step (~30 s on a fast machine). Computed once at first
``compute_lga_sa2_correspondence()`` call, then serialised to a parquet
sidecar keyed on the boundary files' mtimes; subsequent calls read the
parquet (instant).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

_log = logging.getLogger(__name__)

# EPSG:3577 = GDA94 / Australian Albers Equal-Area Conic. Standard
# choice for area-preserving spatial work across Australia (~0.01% area
# error across the continent). Required so the intersection-area
# computations produce meaningful weights.
_EQUAL_AREA_CRS = "EPSG:3577"

# Minimum intersection area to keep, in square metres. Boundary files
# from different ABS releases (SA2 vs LGA) digitise the same coastline
# with small differences; that produces sliver intersections of a few
# square metres along shared edges. ~1 m² is well below any meaningful
# correspondence weight (smallest real urban SA2 is ~0.5 km² = 500,000
# m²) so we drop slivers below this threshold.
_MIN_AREA_M2 = 1.0


@dataclass(frozen=True)
class LgaSa2Correspondence:
    """Area-weighted spatial correspondence between LGAs and SA2s.

    Constructed by :func:`compute_lga_sa2_correspondence`. Holds the
    intersection table as a DataFrame keyed on ``(sa2_code, lga_code)``;
    methods derive the two weight directions and provide downscale
    helpers.

    Attributes:
        weights: DataFrame with one row per (SA2, LGA) intersection
            larger than the sliver threshold. Columns:
            - ``sa2_code`` (str), ``lga_code`` (str)
            - ``intersection_area_m2`` (float)
            - ``sa2_area_m2`` (float) — total SA2 area
            - ``lga_area_m2`` (float) — total LGA area
            - ``sa2_share_of_lga`` (float, 0..1) — intersection_area / lga_area
            - ``lga_share_of_sa2`` (float, 0..1) — intersection_area / sa2_area
        sa2_code_column: Name of the SA2 code column in the source GDF.
        lga_code_column: Name of the LGA code column in the source GDF.
    """

    weights: pd.DataFrame
    sa2_code_column: str
    lga_code_column: str

    # ---- Lookups -----------------------------------------------------

    def lgas_for_sa2(self, sa2_code: str) -> dict[str, float]:
        """Return ``{lga_code: lga_share_of_sa2}`` for one SA2.

        Empty dict if the SA2 isn't in the correspondence (e.g. pseudo-
        SA2s without geometry, or SA2s entirely outside the LGA set's
        coverage).
        """
        rows = self.weights[self.weights["sa2_code"] == str(sa2_code)]
        return dict(zip(rows["lga_code"], rows["lga_share_of_sa2"], strict=False))

    def sa2s_for_lga(self, lga_code: str) -> dict[str, float]:
        """Return ``{sa2_code: sa2_share_of_lga}`` for one LGA."""
        rows = self.weights[self.weights["lga_code"] == str(lga_code)]
        return dict(zip(rows["sa2_code"], rows["sa2_share_of_lga"], strict=False))

    # ---- Downscale helpers -------------------------------------------

    def downscale_counts(self, lga_values: dict[str, float]) -> dict[str, float]:
        """Downscale LGA-level counts to SA2s by area share.

        Each SA2 gets the sum, across LGAs that overlap it, of
        ``lga_value * sa2_share_of_lga``. Use when the value is a
        **count** (e.g. building approvals) — the sum over all SA2s in
        an LGA equals the LGA's value.

        SA2s not in the correspondence (no intersection with any LGA in
        ``lga_values``) get ``0.0``. LGAs in ``lga_values`` that aren't
        in the correspondence are silently ignored.
        """
        sub = self.weights[self.weights["lga_code"].isin(lga_values)]
        if sub.empty:
            return {}
        # Element-wise: each row contributes lga_value * sa2_share_of_lga
        # to its SA2's total.
        contribution = sub["lga_code"].map(lga_values).astype("float64") * sub[
            "sa2_share_of_lga"
        ].astype("float64")
        # group by SA2 and sum
        per_sa2 = sub.assign(_contrib=contribution).groupby("sa2_code")["_contrib"].sum()
        # `.to_dict()` is typed dict[Hashable, Any]; coerce explicitly.
        return {str(k): float(v) for k, v in per_sa2.items()}

    def downscale_rates(self, lga_values: dict[str, float]) -> dict[str, float]:
        """Downscale LGA-level rates / intensities to SA2s by area share.

        Each SA2 gets a weighted average, across LGAs that overlap it,
        of ``lga_value`` weighted by ``lga_share_of_sa2``. Use when the
        value is a **rate / intensity** (e.g. per-1,000-population
        figures, percentages) — the within-SA2 average matches the
        within-LGA value when an SA2 is entirely inside one LGA.

        If only some of an SA2's overlapping LGAs are in ``lga_values``,
        the weights are *renormalised* over the covered LGAs (so the
        weighted average is honest about partial coverage rather than
        treating missing-LGA cells as zero). An SA2 with no covered
        LGA is omitted from the result rather than producing NaN.
        """
        sub = self.weights[self.weights["lga_code"].isin(lga_values)]
        if sub.empty:
            return {}
        sub = sub.copy()
        sub["_value"] = sub["lga_code"].map(lga_values).astype("float64")
        sub["_weight"] = sub["lga_share_of_sa2"].astype("float64")
        # Group by SA2; weighted-avg = sum(value * weight) / sum(weight)
        # The denominator handles the partial-coverage case correctly:
        # if only some of an SA2's overlapping LGAs are in lga_values,
        # the weighted average renormalises over just the covered ones.
        out: dict[str, float] = {}
        for sa2_code, rows in sub.groupby("sa2_code"):
            values = rows["_value"].to_numpy()
            weights = rows["_weight"].to_numpy()
            wsum = weights.sum()
            if wsum <= 0:
                continue
            out[str(sa2_code)] = float((values * weights).sum() / wsum)
        return out


# ---- Build -----------------------------------------------------------


def compute_lga_sa2_correspondence(
    *,
    sa2: gpd.GeoDataFrame,
    lga: gpd.GeoDataFrame,
    sa2_code_column: str = "SA2_CODE21",
    lga_code_column: str | None = None,
    min_area_m2: float = _MIN_AREA_M2,
) -> LgaSa2Correspondence:
    """Compute the SA2 ↔ LGA spatial correspondence from boundary GDFs.

    Projects both inputs to EPSG:3577 (equal-area), intersects, drops
    slivers below ``min_area_m2``, and computes both weight directions.

    Args:
        sa2: SA2 boundary GeoDataFrame with a CRS and a code column.
        lga: LGA boundary GeoDataFrame with a CRS and a code column.
        sa2_code_column: SA2 code column name. Defaults to Edition 3.
        lga_code_column: LGA code column name. If ``None``, auto-detects
            the first column matching ``LGA_CODE*``.
        min_area_m2: Drop intersections smaller than this. Defaults to
            1 m² — well below any real correspondence weight, big enough
            to suppress digitisation-difference slivers.

    Returns:
        :class:`LgaSa2Correspondence` with the weights table.

    Raises:
        ValueError: if either GDF lacks a CRS, lacks the expected code
            column, or has any null geometries.
    """
    if sa2.crs is None:
        raise ValueError("SA2 boundary GeoDataFrame must have a CRS")
    if lga.crs is None:
        raise ValueError("LGA boundary GeoDataFrame must have a CRS")
    if sa2_code_column not in sa2.columns:
        raise ValueError(
            f"SA2 code column {sa2_code_column!r} not found in SA2 boundaries; "
            f"got: {list(sa2.columns)}"
        )
    if lga_code_column is None:
        candidates = [c for c in lga.columns if c.startswith("LGA_CODE")]
        if not candidates:
            raise ValueError(
                f"Could not auto-detect LGA code column (looking for LGA_CODE*); "
                f"got: {list(lga.columns)}. Pass lga_code_column= explicitly."
            )
        lga_code_column = candidates[0]
    elif lga_code_column not in lga.columns:
        raise ValueError(
            f"LGA code column {lga_code_column!r} not found in LGA boundaries; "
            f"got: {list(lga.columns)}"
        )

    # Drop rows with null/empty geometry up-front. ABS boundaries
    # include a small number of pseudo-rows (off-shore, migratory) that
    # carry no geometry by design — same handling as
    # compute_sa2_areas_km2 (issue #101). They simply don't participate
    # in the correspondence.
    sa2_real = sa2[sa2.geometry.notna() & ~sa2.geometry.is_empty].copy()
    lga_real = lga[lga.geometry.notna() & ~lga.geometry.is_empty].copy()
    if sa2_real.empty or lga_real.empty:
        raise ValueError(
            "Both SA2 and LGA boundaries must contain at least one row with valid geometry"
        )

    # Project to equal-area for accurate intersection areas. Both inputs
    # may already be in EPSG:3577 — geopandas handles that as a no-op.
    sa2_ea = sa2_real[[sa2_code_column, "geometry"]].to_crs(_EQUAL_AREA_CRS)
    lga_ea = lga_real[[lga_code_column, "geometry"]].to_crs(_EQUAL_AREA_CRS)

    # Per-feature total areas for weight denominators (skip the
    # intersection's own area; that's separate).
    sa2_areas = sa2_ea.assign(_sa2_area_m2=sa2_ea.geometry.area)[[sa2_code_column, "_sa2_area_m2"]]
    lga_areas = lga_ea.assign(_lga_area_m2=lga_ea.geometry.area)[[lga_code_column, "_lga_area_m2"]]

    # The actual geometric intersection. geopandas.overlay does the
    # heavy lifting — it builds an STRtree internally so we don't need
    # to manage spatial index manually. The output has one row per
    # (sa2, lga) intersection polygon.
    _log.info(
        "Computing LGA-SA2 spatial intersection: %d SA2s x %d LGAs",
        len(sa2_ea),
        len(lga_ea),
    )
    intersected = gpd.overlay(sa2_ea, lga_ea, how="intersection", keep_geom_type=True)
    if intersected.empty:
        raise ValueError(
            "SA2 x LGA intersection is empty — the two boundary inputs don't "
            "overlap anywhere. Check that both cover the same country / region."
        )

    intersected["_intersection_area_m2"] = intersected.geometry.area
    # Drop slivers from digitisation noise.
    intersected = intersected[intersected["_intersection_area_m2"] >= min_area_m2].copy()

    # Join in the per-feature totals to compute share weights.
    intersected = intersected.merge(sa2_areas, on=sa2_code_column, how="left")
    intersected = intersected.merge(lga_areas, on=lga_code_column, how="left")
    intersected["sa2_share_of_lga"] = (
        intersected["_intersection_area_m2"] / intersected["_lga_area_m2"]
    )
    intersected["lga_share_of_sa2"] = (
        intersected["_intersection_area_m2"] / intersected["_sa2_area_m2"]
    )

    # Compose the public weights table — dropping the geometry column
    # so callers don't carry shapely objects around unnecessarily.
    weights = pd.DataFrame(
        {
            "sa2_code": intersected[sa2_code_column].astype(str),
            "lga_code": intersected[lga_code_column].astype(str),
            "intersection_area_m2": intersected["_intersection_area_m2"].astype("float64"),
            "sa2_area_m2": intersected["_sa2_area_m2"].astype("float64"),
            "lga_area_m2": intersected["_lga_area_m2"].astype("float64"),
            "sa2_share_of_lga": intersected["sa2_share_of_lga"].astype("float64"),
            "lga_share_of_sa2": intersected["lga_share_of_sa2"].astype("float64"),
        }
    ).reset_index(drop=True)

    _log.info(
        "LGA-SA2 correspondence: %d (SA2, LGA) pairs, covering %d distinct SA2s "
        "and %d distinct LGAs",
        len(weights),
        weights["sa2_code"].nunique(),
        weights["lga_code"].nunique(),
    )

    return LgaSa2Correspondence(
        weights=weights,
        sa2_code_column=sa2_code_column,
        lga_code_column=lga_code_column,
    )


# ---- On-disk cache ---------------------------------------------------


def save_correspondence(corr: LgaSa2Correspondence, path: Path) -> None:
    """Write a correspondence to a parquet sidecar.

    The companion :func:`load_correspondence` reads it back identically.
    Use the boundary files' mtimes as the cache key in calling code.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Include the column names in the parquet metadata so load_correspondence
    # can reconstruct the LgaSa2Correspondence dataclass faithfully.
    corr.weights.attrs["sa2_code_column"] = corr.sa2_code_column
    corr.weights.attrs["lga_code_column"] = corr.lga_code_column
    corr.weights.to_parquet(path, index=False)


def load_correspondence(path: Path) -> LgaSa2Correspondence:
    """Read a correspondence from a parquet sidecar written by
    :func:`save_correspondence`.

    Returns a :class:`LgaSa2Correspondence` with the same shape as the
    one originally computed. Pandas ``DataFrame.attrs`` doesn't survive
    a parquet round-trip in every backend, so column-name metadata
    falls back to sensible defaults when missing.
    """
    df = pd.read_parquet(path)
    # Defaults match the Edition 3 / LGA 2025 conventions, matching what
    # compute_lga_sa2_correspondence picks up by default.
    sa2_col = df.attrs.get("sa2_code_column", "SA2_CODE21")
    lga_col = df.attrs.get("lga_code_column", "LGA_CODE25")
    return LgaSa2Correspondence(
        weights=df,
        sa2_code_column=sa2_col,
        lga_code_column=lga_col,
    )
