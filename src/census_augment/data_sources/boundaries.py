"""ASGS SA2 boundary download, extraction, and loading (spec §4.1, §7.3)."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import requests

from ..config import CensusConfig
from ._base import _AbsZipDataSource

_log = logging.getLogger(__name__)

# Feather sidecar next to the .shp. Reading the 50 MB ASGS SA2
# shapefile via geopandas/pyogrio takes ~1.3 s on a fast NVMe and
# proportionally more under bind-mounted filesystems (issue #43).
# Reading the same GeoDataFrame back from feather is ~10x faster.
# Keyed on the .shp mtime — refreshing the source bumps the mtime
# and invalidates the cache automatically.
_BOUNDARIES_FEATHER_SUFFIX = ".feather"


class BoundariesDataSource(_AbsZipDataSource):
    """Download, extract, and load the ASGS boundary Shapefile.

    Filename is constructed deterministically from config (spec §4.1):
    ``{level}_{year}_AUST_SHP_{datum}.zip``, e.g.
    ``SA2_2021_AUST_SHP_GDA2020.zip``. The ``SHP`` token is only on the
    ZIP filename — files inside the ZIP are named
    ``SA2_2021_AUST_GDA2020.{shp,dbf,prj,shx,...}``.

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
    ) -> None:
        super().__init__(
            base_url=base_url,
            root=root,
            session=session,
            chunk_size=chunk_size,
            timeout=timeout,
        )
        self._census = census

    @property
    def filename(self) -> str:
        c = self._census
        return f"{c.level}_{c.year}_AUST_SHP_{c.datum}.zip"

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
    except (OSError, Exception) as e:  # noqa: BLE001 — feather/pyarrow can raise many things
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
    except (OSError, Exception) as e:  # noqa: BLE001 — feather/pyarrow can raise many things
        _log.debug("Could not write boundary feather cache to %s: %s", feather_path, e)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
