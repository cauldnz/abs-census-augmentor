"""ASGS Local Government Area (LGA) boundary download and loading.

LGA boundaries are part of the ASGS "non-ABS structures" set. Unlike
SA2/SA3/SA4 boundaries (which update with each Census), LGA boundaries
update **annually** — ABS publishes LGA_2021, LGA_2022, ..., LGA_2025
as separate releases that reflect local-government amalgamations and
boundary changes that happen continuously through the inter-Census
period.

LGAs are **not** part of the ASGS hierarchy that SA2/SA3/SA4 form. They
overlap SA2 boundaries (a single LGA can span multiple SA2s; a single
SA2 can be split across multiple LGAs). Joining LGA-keyed data onto
SA2 rows therefore requires a real spatial correspondence — see
``census_augment.correspondence``.

Real-data probe (live-fetched 2026-06-01 via
``tools/probe_new_datasets.py``): the LGA 2025 shapefile contains 567
LGAs in EPSG:7844 (GDA2020 geographic) with columns ``LGA_CODE25``,
``LGA_NAME25``, ``STE_CODE21``, ``STE_NAME21``, ``AUS_CODE21``,
``AUS_NAME21``, ``AREASQKM``, ``geometry``. The filename pattern is
``LGA_<YYYY>_AUST_GDA2020.zip`` (no ``_SHP_`` infix, unlike the SA2
files).
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import requests

from ._base import _AbsZipDataSource

_log = logging.getLogger(__name__)

# Same feather-sidecar pattern as SA2 boundaries — reading the 55 MB
# LGA shapefile via geopandas is ~1 s; re-reading from feather is much
# faster.
_LGA_FEATHER_SUFFIX = ".feather"

# Default ABS base URL for the ASGS Edition 3 boundary files. The LGA
# product lives under the same path as SA2/SA3/SA4.
DEFAULT_LGA_BASE_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files"
)

# LGA boundary releases. ABS publishes annually; entries here are the
# years we've live-confirmed via HEAD probe. ``"latest"`` resolves to
# the highest known year.
KNOWN_LGA_YEARS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)


class LgaBoundariesDataSource(_AbsZipDataSource):
    """Download, extract, and load an ABS LGA boundary shapefile.

    ``year`` selects which annual LGA release to fetch (defaults to the
    most recent in :data:`KNOWN_LGA_YEARS`, currently 2025). The
    filename pattern is ``LGA_<YYYY>_AUST_GDA2020.zip``; the extracted
    ``.shp`` has 567 LGAs in EPSG:7844 (GDA2020 geographic) with the
    columns documented in this module's docstring.

    Caching mirrors :class:`BoundariesDataSource`: ZIP under ``root``,
    extracted into a sibling directory, feather sidecar next to the
    ``.shp`` for fast reload.
    """

    _label = "LGA boundary ZIP"

    def __init__(
        self,
        *,
        year: int | str = "latest",
        root: Path,
        base_url: str = DEFAULT_LGA_BASE_URL,
        session: requests.Session | None = None,
        chunk_size: int = 1024 * 1024,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            root=root,
            session=session,
            chunk_size=chunk_size,
            timeout=timeout,
        )
        self._year: int = self._resolve_year(year)

    @staticmethod
    def _resolve_year(year: int | str) -> int:
        if isinstance(year, str):
            if year.lower() == "latest":
                return max(KNOWN_LGA_YEARS)
            try:
                year_int = int(year)
            except ValueError as e:
                raise ValueError(f"LGA year must be 'latest' or an integer; got {year!r}") from e
        else:
            year_int = year
        if year_int not in KNOWN_LGA_YEARS:
            raise ValueError(
                f"LGA year {year_int} not in known set {KNOWN_LGA_YEARS}. "
                f"ABS publishes LGA boundaries annually; if a new year is "
                f"out, add it to KNOWN_LGA_YEARS in lga_boundaries.py "
                f"after HEAD-probing the URL."
            )
        return year_int

    @property
    def year(self) -> int:
        return self._year

    @property
    def filename(self) -> str:
        # Real ABS pattern (live-probed 2026-06-01): LGA_2025_AUST_GDA2020.zip
        # Note: NO ``_SHP_`` infix, unlike the SA2 boundary files which use
        # SA2_2021_AUST_SHP_GDA2020.zip. This was a live-probe finding —
        # don't guess the filename, the patterns differ.
        return f"LGA_{self._year}_AUST_GDA2020.zip"

    @property
    def code_column(self) -> str:
        """The LGA code column in the extracted DBF (e.g. ``LGA_CODE25``)."""
        # Two-digit year suffix matching the release year
        return f"LGA_CODE{str(self._year)[-2:]}"

    @property
    def name_column(self) -> str:
        return f"LGA_NAME{str(self._year)[-2:]}"

    @property
    def shapefile_path(self) -> Path | None:
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
                _log.debug("Using cached LGA boundary at %s", cached)
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

    def load(self, refresh: bool = False) -> gpd.GeoDataFrame:
        """Fetch (if needed) and load as a GeoDataFrame.

        Caches the loaded GeoDataFrame as a feather sidecar next to the
        .shp. The .shp mtime is the cache key — ``refresh=True`` triggers
        re-extract and invalidates the cache.
        """
        shp = self.fetch(refresh=refresh)
        feather_path = shp.with_suffix(_LGA_FEATHER_SUFFIX)
        cached = _try_read_feather_cache(feather_path, shp)
        if cached is not None:
            return cached
        gdf = gpd.read_file(shp)
        _try_write_feather_cache(feather_path, gdf)
        return gdf


def _try_read_feather_cache(feather_path: Path, shp: Path) -> gpd.GeoDataFrame | None:
    """Same shape as boundaries.py's helper — copied to avoid creating
    an import cycle for one function. Both are 5 lines.
    """
    if not feather_path.exists():
        return None
    try:
        if feather_path.stat().st_mtime < shp.stat().st_mtime:
            return None
        return gpd.read_feather(feather_path)
    except Exception as e:  # noqa: BLE001
        _log.debug("Ignoring LGA feather cache at %s: %s", feather_path, e)
        return None


def _try_write_feather_cache(feather_path: Path, gdf: gpd.GeoDataFrame) -> None:
    try:
        gdf.to_feather(feather_path)
    except Exception as e:  # noqa: BLE001
        _log.debug("Failed to write LGA feather cache to %s: %s", feather_path, e)
