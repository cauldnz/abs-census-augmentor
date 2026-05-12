"""Mesh Block → SA2 correspondence (spec §7.3 fast path, §19.4, §15.1).

Resolves the §15.1 open question: there is **no** standalone MB→SA2
correspondence file on the ABS correspondences page (that page hosts
*change files* between ASGS editions — e.g. 2016→2021, not within-edition
hierarchy lookups).

The MB→SA2 mapping is the **attribute table of the Mesh Block shapefile**.
We download `MB_{year}_AUST_SHP_{datum}.zip` from the same digital
boundary files endpoint used for SA2 (§4.1), then read just the .dbf
columns (no geometry) to build the lookup dict.

Used by the pipeline's §7.3 fast path: when the G-NAF geocoder returns
an `mb_code`, we resolve `mb_code → (sa2_code, sa2_name)` here in O(1)
rather than going through the spatial-join fallback path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ._base import _AbsZipDataSource

_log = logging.getLogger(__name__)

_DEFAULT_DATUM = "GDA2020"

# Patterns we use to find the right ABS column names. Two conventions
# coexist in ABS data:
#
#   * Shapefiles (10-char DBF column limit) — ``MB_CODE21``, ``SA2_CODE21``,
#     ``SA2_NAME21``. This is the form the real Mesh Block shapefile
#     ships with.
#   * DataPack CSVs — ``MB_CODE_2021``, ``SA2_MAINCODE_2021``,
#     ``SA2_NAME_2021`` (no length limit).
#
# We accept both. Year suffix is captured so we can pick the
# highest-year-suffixed column when 2026 vintage data appears alongside
# 2021 (spec §13 extensibility hook).
_MB_CODE_PATTERNS = [r"^MB_CODE_?\d{2,4}$"]
_SA2_CODE_PATTERNS = [
    r"^SA2_MAINCODE_\d{4}$",
    r"^SA2_MAIN\d{2}$",
    r"^SA2_CODE_\d{4}$",
    r"^SA2_CODE\d{2}$",
]
_SA2_NAME_PATTERNS = [r"^SA2_NAME_?\d{2,4}$"]


@dataclass(frozen=True)
class MbInfo:
    """SA-hierarchy info for one Mesh Block."""

    mb_code: str
    sa2_code: str
    sa2_name: str


class MbCorrespondenceDataSource(_AbsZipDataSource):
    """Downloads the ABS Mesh Block shapefile and exposes the MB→SA2 lookup.

    Filename pattern: ``MB_{year}_AUST_SHP_{datum}.zip`` (matches SA2
    boundaries; same digital boundary files endpoint per §4.1).
    The shapefile is large (~100 MB) but we only read the .dbf attribute
    table — no geometry loaded.
    """

    _label = "Mesh Block correspondence ZIP"

    def __init__(
        self,
        *,
        year: int = 2021,
        datum: str = _DEFAULT_DATUM,
        base_url: str,
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 1024 * 1024,
        timeout: float = 600.0,  # MB shapefile is bigger than SA2
    ) -> None:
        if datum not in ("GDA2020", "GDA94"):
            raise ValueError(f"datum must be 'GDA2020' or 'GDA94'; got {datum!r}")
        super().__init__(
            base_url=base_url,
            root=root,
            session=session,
            chunk_size=chunk_size,
            timeout=timeout,
        )
        self._year = year
        self._datum = datum

    @property
    def filename(self) -> str:
        return f"MB_{self._year}_AUST_SHP_{self._datum}.zip"

    @property
    def shapefile_path(self) -> Path | None:
        """Path to the extracted ``.shp`` file (the .dbf is its sidecar)."""
        if not self.extract_dir.exists():
            return None
        for shp in self.extract_dir.rglob("*.shp"):
            return shp
        return None

    def is_cached(self) -> bool:
        return self.shapefile_path is not None

    def fetch(self, refresh: bool = False) -> Path:
        """Download (if needed) and extract; return the ``.shp`` path."""
        if not refresh:
            cached = self.shapefile_path
            if cached is not None:
                _log.debug("Using cached MB correspondence at %s", cached)
                return cached
        self._download()
        self._extract()
        shp = self.shapefile_path
        if shp is None:
            raise RuntimeError(
                f"No .shp file found in {self.extract_dir} after extracting "
                f"{self.zip_path}; ABS may have changed the ZIP layout."
            )
        return shp

    def load_correspondence(self, refresh: bool = False) -> dict[str, MbInfo]:
        """Build and return the ``MB_CODE → MbInfo`` dict.

        Reads only the .dbf columns we need (no geometry). The first call
        downloads + extracts if necessary; the dict itself is built fresh
        each call so callers control caching at their layer.
        """
        shp_path = self.fetch(refresh=refresh)
        df = self._read_dbf_attributes(shp_path)
        return self._build_lookup(df, source=shp_path)

    # ---- internals ------------------------------------------------------

    @staticmethod
    def _read_dbf_attributes(shp_path: Path) -> pd.DataFrame:
        """Read just the attribute table from a shapefile, skipping geometry.

        Uses pyogrio (a geopandas backend) which exposes ``read_geometry=False``
        for cheap attribute-only reads.
        """
        import pyogrio  # imported lazily to keep top-level import surface small

        return pyogrio.read_dataframe(  # type: ignore[no-any-return]
            str(shp_path), read_geometry=False
        )

    @classmethod
    def _build_lookup(cls, df: pd.DataFrame, *, source: Any) -> dict[str, MbInfo]:
        mb_col = cls._detect_column(df.columns, _MB_CODE_PATTERNS, source)
        sa2_code_col = cls._detect_column(df.columns, _SA2_CODE_PATTERNS, source)
        sa2_name_col = cls._detect_column(df.columns, _SA2_NAME_PATTERNS, source)

        lookup: dict[str, MbInfo] = {}
        for mb_code, sa2_code, sa2_name in zip(
            df[mb_col].astype(str),
            df[sa2_code_col].astype(str),
            df[sa2_name_col].astype(str),
            strict=True,
        ):
            lookup[mb_code] = MbInfo(mb_code=mb_code, sa2_code=sa2_code, sa2_name=sa2_name)
        _log.info(
            "Loaded MB→SA2 correspondence: %d mesh blocks from %s",
            len(lookup),
            source,
        )
        return lookup

    @staticmethod
    def _detect_column(columns: Any, patterns: list[str], source: Any) -> str:
        """Pick the highest-year-suffixed column matching any pattern.

        ABS column names carry an explicit year suffix (e.g. ``SA2_NAME_2021``).
        When 2026 columns appear alongside 2021 ones, the highest year wins
        — matching what the spec §13 extensibility hook for "new census year"
        promises.
        """
        candidates: list[str] = []
        for col in columns:
            if any(re.match(p, str(col)) for p in patterns):
                candidates.append(str(col))
        if not candidates:
            raise RuntimeError(
                f"None of the patterns {patterns} matched any column in "
                f"{source}. Got columns: {sorted(map(str, columns))}"
            )
        return sorted(candidates)[-1]
