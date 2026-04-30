"""Common base for ABS ZIP-archive data sources.

Boundaries and DataPacks share an identical streaming download +
atomic-rename + extract dance; this base captures it so adding a new
ABS data source (e.g. a future 2026 Census profile) is a small
subclass.

Subclasses provide:

- ``filename`` property — the deterministic ZIP filename (per spec §4.1
  and §4.2).
- ``_label`` class attribute — used in log messages ("boundary ZIP",
  "DataPack ZIP").

Subclasses also extend with format-specific ``fetch`` / ``load`` methods.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import requests

_log = logging.getLogger(__name__)


class _AbsZipDataSource:
    """Download / extract / cache common machinery for ABS ZIP archives."""

    #: Subclass-specific log label, e.g. ``"boundary ZIP"``.
    _label: str = "ZIP"

    def __init__(
        self,
        *,
        base_url: str,
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 1024 * 1024,
        timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._root = Path(root)
        self._session = session if session is not None else requests.Session()
        self._chunk_size = chunk_size
        self._timeout = timeout

    @property
    def filename(self) -> str:
        """Subclass must override with the deterministic ZIP filename."""
        raise NotImplementedError(
            "subclasses must override `filename` (see spec §4.1, §4.2)"
        )

    @property
    def url(self) -> str:
        return f"{self._base_url}/{self.filename}"

    @property
    def zip_path(self) -> Path:
        return self._root / self.filename

    @property
    def extract_dir(self) -> Path:
        return self._root / self.filename.removesuffix(".zip")

    def _download(self) -> None:
        """Stream-download to a ``.tmp`` file then atomically rename."""
        self._root.mkdir(parents=True, exist_ok=True)
        _log.info("Downloading %s from %s", self._label, self.url)
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
        _log.info("Saved %s to %s", self._label, self.zip_path)

    def _extract(self) -> None:
        """Re-create ``extract_dir`` and unpack ``zip_path`` into it."""
        if self.extract_dir.exists():
            shutil.rmtree(self.extract_dir)
        self.extract_dir.mkdir(parents=True, exist_ok=True)
        _log.info("Extracting %s to %s", self.zip_path, self.extract_dir)
        with zipfile.ZipFile(self.zip_path) as zf:
            zf.extractall(self.extract_dir)
