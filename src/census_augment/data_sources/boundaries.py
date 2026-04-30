"""ASGS SA2 boundary download, extraction, and loading (spec §4.1, §7.3)."""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

from ..config import CensusConfig

_log = logging.getLogger(__name__)


class BoundariesDataSource:
    """Download, extract, and load the ASGS boundary GeoPackage.

    Filename is constructed deterministically from config (spec §4.1):
    ``{level}_{year}_AUST_{datum}.zip``, e.g. ``SA2_2021_AUST_GDA2020.zip``.
    Downloaded ZIPs are cached under ``root`` and extracted into a sibling
    directory named after the ZIP. Re-fetch with ``refresh=True`` per
    spec §9.
    """

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
        self._census = census
        self._base_url = base_url.rstrip("/")
        self._root = Path(root)
        self._session = session if session is not None else requests.Session()
        self._chunk_size = chunk_size
        self._timeout = timeout

    @property
    def filename(self) -> str:
        c = self._census
        return f"{c.level}_{c.year}_AUST_{c.datum}.zip"

    @property
    def url(self) -> str:
        return f"{self._base_url}/{self.filename}"

    @property
    def zip_path(self) -> Path:
        return self._root / self.filename

    @property
    def extract_dir(self) -> Path:
        return self._root / self.filename.removesuffix(".zip")

    @property
    def gpkg_path(self) -> Path | None:
        """Path to the extracted ``.gpkg`` file, or ``None`` if not extracted."""
        if not self.extract_dir.exists():
            return None
        for gpkg in self.extract_dir.rglob("*.gpkg"):
            return gpkg
        return None

    def is_cached(self) -> bool:
        return self.gpkg_path is not None

    def fetch(self, refresh: bool = False) -> Path:
        """Download (if needed) and extract; return the ``.gpkg`` path.

        With ``refresh=True``, re-downloads even if cached. Raises
        ``requests.HTTPError`` on a non-2xx response, or ``RuntimeError``
        if the extracted ZIP contains no GeoPackage.
        """
        if not refresh:
            cached = self.gpkg_path
            if cached is not None:
                _log.debug("Using cached boundary at %s", cached)
                return cached
        self._download()
        self._extract()
        gpkg = self.gpkg_path
        if gpkg is None:
            raise RuntimeError(
                f"No .gpkg file found in {self.extract_dir} after extracting "
                f"{self.zip_path}; ABS may have changed the ZIP layout."
            )
        return gpkg

    def load(self, refresh: bool = False) -> gpd.GeoDataFrame:
        """Fetch (if needed) and load as a GeoDataFrame."""
        gpkg = self.fetch(refresh=refresh)
        return gpd.read_file(gpkg)

    def _download(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        _log.info("Downloading boundary ZIP from %s", self.url)
        with self._session.get(
            self.url, stream=True, timeout=self._timeout
        ) as response:
            response.raise_for_status()
            tmp = self._root / (self.filename + ".tmp")
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
            tmp.replace(self.zip_path)
        _log.info("Saved boundary ZIP to %s", self.zip_path)

    def _extract(self) -> None:
        if self.extract_dir.exists():
            shutil.rmtree(self.extract_dir)
        self.extract_dir.mkdir(parents=True, exist_ok=True)
        _log.info("Extracting %s to %s", self.zip_path, self.extract_dir)
        with zipfile.ZipFile(self.zip_path) as zf:
            zf.extractall(self.extract_dir)
