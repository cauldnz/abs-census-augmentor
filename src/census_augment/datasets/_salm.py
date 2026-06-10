"""Small Area Labour Markets (SALM) fetcher (spec §20, dataset id
``salm_labour_force``).

Jobs and Skills Australia (DEWR) publishes the SALM **smoothed SA2**
datafile quarterly — a model-smoothed unemployment count, unemployment
rate, and labour-force estimate for every SA2. SA2-native (no downscale).
The augmentor surfaces the **latest quarter** in the downloaded file.

Real-data findings (live-probed 2026-06-10, December quarter 2025 file):

- A single CSV. Row 1 is an explanatory note ("a dash (-) indicates data
  are unavailable"), row 2 is blank, the **header is row 3**:
  ``Data Item, Statistical Area Level 2 (SA2) (2021 ASGS),
  SA2 Code (2021 ASGS), Dec-10, Mar-11, … Dec-25`` (plus a trailing
  empty column from a stray comma).
- **Long on Data Item, wide on quarter**: 2,336 SA2s × 3 Data Items
  (``Smoothed unemployment (persons)``, ``Smoothed labour force
  (persons)``, ``Smoothed unemployment rate (%)``) × ~61 quarter columns.
- ``-`` marks a suppressed / unavailable cell — parsed to null.
- The note is at the top; there are no trailing footnote rows.

The DEWR download URL embeds a rotating asset id per quarter — hardcoded
per release (no HTML scrape); a new quarter needs a new entry in
``_SALM_URLS_BY_RELEASE``. (The DEWR server is occasionally slow to first
byte; ``retry_stream_get`` covers the transient case.)
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import pandas as pd
import requests

from .._http_retry import retry_stream_get

_log = logging.getLogger(__name__)

# Release id (YYYY-Qn) -> the quarterly download URL. The asset id in the
# path rotates each quarter; add a new entry per release.
_SALM_URLS_BY_RELEASE: dict[str, str] = {
    "2025-Q4": (
        "https://www.dewr.gov.au/download/17068/"
        "salm-smoothed-sa2-datafiles-asgs-2021-december-quarter-2025/42403/"
        "salm-smoothed-sa2-datafiles-asgs-2021-december-quarter-2025/csv"
    ),
}

# Source "Data Item" label -> output column.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Smoothed unemployment (persons)": "smoothed_unemployment_count",
    "Smoothed labour force (persons)": "smoothed_labour_force_count",
    "Smoothed unemployment rate (%)": "smoothed_unemployment_rate",
}
# Integer-valued measures; the rate stays float.
_COUNT_COLUMNS: tuple[str, ...] = (
    "smoothed_unemployment_count",
    "smoothed_labour_force_count",
)

_DATA_ITEM_COL = "Data Item"
_SA2_CODE_COL = "SA2 Code (2021 ASGS)"
# Quarter column labels look like "Dec-25" / "Mar-11".
_QUARTER_RE = re.compile(r"^[A-Za-z]{3}-\d{2}$")
_MONTH_TO_QUARTER = {"mar": "Q1", "jun": "Q2", "sep": "Q3", "dec": "Q4"}


def _normalize_quarter(label: str) -> str | None:
    """``"Dec-25"`` -> ``"2025-Q4"`` (SALM data is all 2010+)."""
    m = re.fullmatch(r"([A-Za-z]{3})-(\d{2})", label.strip())
    if not m:
        return None
    quarter = _MONTH_TO_QUARTER.get(m.group(1).lower())
    if quarter is None:
        return None
    return f"20{m.group(2)}-{quarter}"


class SalmDataSource:
    """Fetch + load SALM smoothed SA2 labour-market estimates.

    Implements the :class:`DatasetFetcher` Protocol. SA2-native — no
    downscale. Surfaces the latest quarter present in the downloaded
    file; parquet-cached after the first parse.

    Args:
        release: Quarter id (``"2025-Q4"``) or ``"latest"``.
        root: Cache directory for the CSV + parquet sidecar.
        session: Optional ``requests.Session`` (tests pass a hermetic one).
        chunk_size / timeout: As per the other fetchers.
    """

    def __init__(
        self,
        *,
        release: str = "latest",
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 256 * 1024,
        timeout: float = 60.0,
    ) -> None:
        self._release_request = str(release)
        self._root = Path(root)
        self._session = session if session is not None else requests.Session()
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._resolved_release: str | None = None
        self._resolved_url: str | None = None

    # ---- DatasetFetcher protocol --------------------------------------

    @property
    def resolved_release(self) -> str:
        if self._resolved_release is None:
            self._resolve_release()
        assert self._resolved_release is not None
        return self._resolved_release

    @property
    def is_cached(self) -> bool:
        if self._resolved_release is not None:
            return self._csv_path.exists()
        return self._root.exists() and any(self._root.glob("salm-*.csv"))

    @property
    def _csv_path(self) -> Path:
        return self._root / f"salm-{self.resolved_release}.csv"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"salm-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the SALM CSV for the resolved release."""
        self._resolve_release()
        if self._csv_path.exists() and not refresh:
            _log.debug("SALM cached at %s", self._csv_path)
            return self._csv_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._csv_path.with_suffix(self._csv_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info("Downloading SALM (%s) from %s", self.resolved_release, url)
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label="SALM",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._csv_path)
        _log.info("Saved SALM to %s", self._csv_path)
        return self._csv_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021`` with the latest
        quarter's smoothed unemployment count / rate / labour force.
        """
        if self._resolved_release is not None and self._parquet_path.exists():
            return pd.read_parquet(self._parquet_path).set_index("sa2_code_2021")

        csv_path = self.fetch()
        df = self._parse_csv(csv_path, expected_release=self.resolved_release)
        df = df.set_index("sa2_code_2021")
        df.reset_index().to_parquet(self._parquet_path, index=False)
        return df

    # ---- release resolution -------------------------------------------

    def _resolve_release(self) -> None:
        if self._resolved_release is not None:
            return

        if self._release_request == "latest":
            picked = max(_SALM_URLS_BY_RELEASE)
        elif self._release_request in _SALM_URLS_BY_RELEASE:
            picked = self._release_request
        else:
            raise RuntimeError(
                f"SALM release {self._release_request!r} not in the registry. "
                f"Available: {sorted(_SALM_URLS_BY_RELEASE)}. New quarters need "
                f"an entry in _SALM_URLS_BY_RELEASE in "
                f"src/census_augment/datasets/_salm.py."
            )
        self._resolved_release = picked
        self._resolved_url = _SALM_URLS_BY_RELEASE[picked]
        _log.info("Resolved SALM release=%s, url=%s", picked, self._resolved_url)

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_csv(csv_path: Path, *, expected_release: str) -> pd.DataFrame:
        """Parse the SALM CSV -> DataFrame with one row per SA2 and the
        latest quarter's three measures.
        """
        text = csv_path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        header_idx = next(
            (i for i, ln in enumerate(lines) if ln.lstrip('"').startswith("Data Item")),
            None,
        )
        if header_idx is None:
            raise RuntimeError(
                f"SALM CSV {csv_path} has no 'Data Item' header row in its first "
                f"lines — layout may have changed; re-probe."
            )

        raw = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), dtype=str)
        raw = raw.rename(columns=lambda c: str(c).strip())
        for required in (_DATA_ITEM_COL, _SA2_CODE_COL):
            if required not in raw.columns:
                raise RuntimeError(
                    f"SALM CSV {csv_path} is missing the {required!r} column; got "
                    f"{list(raw.columns)[:6]}. Upstream layout may have changed."
                )

        quarter_cols = [c for c in raw.columns if _QUARTER_RE.fullmatch(str(c))]
        if not quarter_cols:
            raise RuntimeError(
                f"SALM CSV {csv_path} has no quarter columns (e.g. 'Dec-25'). "
                f"Columns seen: {list(raw.columns)[:8]}."
            )
        latest_col = quarter_cols[-1]
        latest_release = _normalize_quarter(latest_col)
        if latest_release != expected_release:
            raise RuntimeError(
                f"SALM CSV {csv_path} latest quarter {latest_col!r} normalises to "
                f"{latest_release!r}, but the requested release is "
                f"{expected_release!r}. The hardcoded URL for {expected_release} "
                f"may point at the wrong (stale) quarterly file."
            )

        sub = raw[[_DATA_ITEM_COL, _SA2_CODE_COL, latest_col]].copy()
        sub[_SA2_CODE_COL] = sub[_SA2_CODE_COL].astype(str).str.strip()
        sub = sub[sub[_SA2_CODE_COL].str.fullmatch(r"\d{9}")]

        unknown = set(sub[_DATA_ITEM_COL].dropna().unique()) - set(_MEASURE_TO_COLUMN)
        if unknown:
            _log.warning(
                "SALM CSV contains unrecognised Data Item labels %r; dropped. "
                "Update _MEASURE_TO_COLUMN in _salm.py if Jobs and Skills "
                "Australia added a measure we want.",
                sorted(unknown),
            )

        pivoted = sub.pivot_table(
            index=_SA2_CODE_COL,
            columns=_DATA_ITEM_COL,
            values=latest_col,
            aggfunc="first",
        )
        renamed = pivoted.rename(columns=_MEASURE_TO_COLUMN)
        renamed = renamed.reindex(columns=list(_MEASURE_TO_COLUMN.values()))

        # Coerce: "-" / blanks -> NaN; counts -> nullable Int64; rate float.
        # The large counts (labour force, big-SA2 unemployment) carry
        # thousands separators in the source (e.g. "2,318") — strip them
        # before numeric coercion or 98% of labour-force values null out.
        for col in renamed.columns:
            stripped = renamed[col].astype("string").str.replace(",", "", regex=False)
            renamed[col] = pd.to_numeric(stripped, errors="coerce")
        for count_col in _COUNT_COLUMNS:
            if count_col in renamed.columns:
                renamed[count_col] = renamed[count_col].astype("Int64")

        out = renamed.reset_index().rename(columns={_SA2_CODE_COL: "sa2_code_2021"})
        out["reference_period"] = expected_release
        return out


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> SalmDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return SalmDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("salm_labour_force", _build_fetcher)


_register()
