"""ASGS SA2 boundary download, extraction, and loading (spec §4.1, §7.3).

Edition support (spec-temporal.md §2, §13): each ABS ASGS edition ships
the SA2 boundary at a different URL with different filenames and DBF
column names. The per-edition variation is captured in
:mod:`~census_augment.data_sources._edition`; this module just
delegates URL / filename / column-name decisions to that spec.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import requests

from ..config import CensusConfig
from ._base import _AbsZipDataSource
from ._edition import BoundaryEditionSpec, edition_spec_for

_log = logging.getLogger(__name__)

# Feather sidecar next to the .shp. Reading the 50 MB ASGS SA2
# shapefile via geopandas/pyogrio takes ~1.3 s on a fast NVMe and
# proportionally more under bind-mounted filesystems (issue #43).
# Reading the same GeoDataFrame back from feather is ~10x faster.
# Keyed on the .shp mtime — refreshing the source bumps the mtime
# and invalidates the cache automatically.
_BOUNDARIES_FEATHER_SUFFIX = ".feather"


class BoundariesDataSource(_AbsZipDataSource):
    """Download, extract, and load the ASGS SA2 boundary Shapefile.

    The download URL, filename, and SA2 DBF column names are taken from
    a :class:`BoundaryEditionSpec` — either built from the supplied
    :class:`CensusConfig` (default) or passed explicitly. Edition 3
    (2021, GDA2020/GDA94) is the historical default; Edition 2 (2016,
    GDA94) is supported once ``census.year=2016`` is set.

    Downloaded ZIPs are cached under ``root`` and extracted into a sibling
    directory named after the ZIP. Re-fetch with ``refresh=True`` per
    spec §9.
    """

    _label = "boundary ZIP"

    def __init__(
        self,
        *,
        census: CensusConfig,
        base_url: str,
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 1024 * 1024,
        timeout: float = 300.0,
        edition_spec: BoundaryEditionSpec | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            root=root,
            session=session,
            chunk_size=chunk_size,
            timeout=timeout,
        )
        self._census = census
        # An explicit spec wins (test injection, future temporal-mode
        # multi-edition orchestrator); otherwise derive from config.
        # ``base_url`` only affects Edition 3 — Edition 2's URL is fixed.
        self._edition: BoundaryEditionSpec = edition_spec or edition_spec_for(
            year=census.year,
            datum=census.datum,
            base_url=base_url,
        )

    @property
    def edition(self) -> BoundaryEditionSpec:
        """The resolved edition spec — URL/filename/columns this source uses."""
        return self._edition

    @property
    def filename(self) -> str:
        return self._edition.sa2_zip_filename

    @property
    def url(self) -> str:
        # Override the base-class ``base_url + filename`` construction.
        # Edition 2's URL isn't ``base_url + filename`` — it's a
        # Lotus Notes openagent query string captured whole in the spec.
        return self._edition.sa2_download_url

    @property
    def shapefile_path(self) -> Path | None:
        """Path to the extracted ``.shp`` file, or ``None`` if not extracted."""
        if not self.extract_dir.exists():
            return None
        for shp in self.extract_dir.rglob("*.shp"):
            return shp
        return None

    def is_cached(self) -> bool:
        return self.shapefile_path is not None

    def fetch(self, refresh: bool = False) -> Path:
        """Download (if needed) and extract; return the ``.shp`` path.

        With ``refresh=True``, re-downloads even if cached. Raises
        ``requests.HTTPError`` on a non-2xx response, or ``RuntimeError``
        if the extracted ZIP contains no Shapefile.
        """
        if not refresh:
            cached = self.shapefile_path
            if cached is not None:
                _log.debug("Using cached boundary at %s", cached)
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
        .shp on first call (see :data:`_BOUNDARIES_FEATHER_SUFFIX`).
        Subsequent calls read the much-faster feather. The .shp mtime
        is the cache key — ``refresh=True`` triggers re-extract, which
        bumps the mtime and so invalidates the cache.
        """
        shp = self.fetch(refresh=refresh)
        feather_path = shp.with_suffix(_BOUNDARIES_FEATHER_SUFFIX)
        cached = _try_read_feather_cache(feather_path, shp)
        if cached is not None:
            return cached
        gdf = gpd.read_file(shp)
        _try_write_feather_cache(feather_path, gdf)
        return gdf


def _try_read_feather_cache(feather_path: Path, shp: Path) -> gpd.GeoDataFrame | None:
    """Return the cached GeoDataFrame if newer than ``shp``; else ``None``.

    Silent failure on any read error — fall back to reading the .shp.
    """
    if not feather_path.exists():
        return None
    try:
        if feather_path.stat().st_mtime < shp.stat().st_mtime:
            return None
        return gpd.read_feather(feather_path)
    except Exception as e:  # noqa: BLE001 — feather/pyarrow can raise many things
        _log.debug("Ignoring boundary feather cache at %s: %s", feather_path, e)
        return None


def _try_write_feather_cache(feather_path: Path, gdf: gpd.GeoDataFrame) -> None:
    """Atomically write the feather sidecar.

    Silent on any write failure — caching is an optimisation, not a
    correctness requirement. Atomic-rename so a partial write never
    gets read back as valid.
    """
    tmp_path = feather_path.with_suffix(feather_path.suffix + ".tmp")
    try:
        gdf.to_feather(tmp_path)
        tmp_path.replace(feather_path)
    except Exception as e:  # noqa: BLE001 — feather/pyarrow can raise many things
        _log.debug("Could not write boundary feather cache to %s: %s", feather_path, e)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
