"""ABS Estimated Resident Population by SA2 fetcher (spec §20, dataset ``erp_by_sa2``).

ABS publishes annual ERP estimates as a multi-sheet XLSX (catalogue
3218.0). The augmentor pulls the SA2-level long-history file
(``32180DS0003_*.xlsx``) since it carries the full 2001-onwards series
on the current ASGS edition — useful both as a denominator for the
latest year and as input to growth-rate features.

The download URL is constructed from the ABS Regional Population
landing page, which itself encodes the latest reference period in the
URL path (e.g. ``/regional-population/2024-25/``). We scrape the
landing page once to discover that path, then download the relevant
DS0003 file. A subsequent release would re-publish under a new
period directory; we re-detect each time ``release="latest"``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import requests

_log = logging.getLogger(__name__)

ERP_LANDING_URL = (
    "https://www.abs.gov.au/statistics/people/population/"
    "regional-population/latest-release"
)

# DS-product number on the ABS download page; this is the long-history
# SA2 ERP file. The same page also has DS0005 (2021-onwards SA2) and
# higher levels (SA3/SA4/etc.); we want the long-history SA2 version.
_LONG_HISTORY_SA2_FRAGMENT = "32180DS0003"

# Column positions in Table 1 of DS0003 (zero-indexed). The header
# is split across two rows in current releases:
#   * Row 4 has the year integers in cols 10+ (cols 0-9 blank).
#   * Row 5 has the geography labels in cols 0-9 and "no." (units)
#     in cols 10+.
# Data starts at row 6. We scan the first ~10 rows for the row that
# contains year integers in the year-position range, so a small layout
# shift between releases doesn't break us.
_TABLE1_SA2_CODE_COL = 8
_TABLE1_SA2_NAME_COL = 9
_TABLE1_FIRST_YEAR_COL = 10


class ErpDataSource:
    """Fetch + load ABS Regional Population (SA2 long-history series).

    Implements the :class:`DatasetFetcher` Protocol. ``release`` is the
    *reference year* (e.g. ``2024`` for the 2024-25 release covering
    populations through 30 June 2024). ``"latest"`` resolves the
    landing page's most recent release.
    """

    _label = "ABS Regional Population SA2 (long history)"

    def __init__(
        self,
        *,
        release: str | int = "latest",
        root: Path,
        landing_url: str = ERP_LANDING_URL,
        session: requests.Session | None = None,
        chunk_size: int = 256 * 1024,
        timeout: float = 60.0,
    ) -> None:
        self._release_request = str(release)
        self._root = Path(root)
        self._landing_url = landing_url
        self._session = (
            session if session is not None else requests.Session()
        )
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._resolved_release: str | None = None
        self._resolved_url: str | None = None

    # ---- protocol -------------------------------------------------------

    @property
    def resolved_release(self) -> str:
        if self._resolved_release is None:
            self._resolve_release()
        assert self._resolved_release is not None
        return self._resolved_release

    @property
    def is_cached(self) -> bool:
        if self._resolved_release is not None:
            return self._xlsx_path.exists()
        return self._root.exists() and any(
            self._root.glob("erp-*.xlsx")
        )

    @property
    def _xlsx_path(self) -> Path:
        return self._root / f"erp-sa2-{self.resolved_release}.xlsx"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"erp-sa2-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        self._resolve_release()
        if self._xlsx_path.exists() and not refresh:
            _log.debug("ERP cached at %s", self._xlsx_path)
            return self._xlsx_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._xlsx_path.with_suffix(self._xlsx_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info(
            "Downloading %s (%s) from %s",
            self._label,
            self.resolved_release,
            url,
        )
        with self._session.get(
            url, stream=True, timeout=self._timeout
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(
                    chunk_size=self._chunk_size
                ):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._xlsx_path)
        _log.info("Saved %s to %s", self._label, self._xlsx_path)
        return self._xlsx_path

    def load(self) -> pd.DataFrame:
        """Return SA2-keyed DataFrame.

        Columns:

        - ``population_total`` — latest available year's ERP.
        - ``reference_year`` — the year ``population_total`` is for.
        - ``population_history_<year>`` — population estimates per year
          back to 2001 (one column per year).
        - ``state_abbreviation`` — copied from the source's S/T name.
        """
        if self._resolved_release is not None and self._parquet_path.exists():
            return pd.read_parquet(self._parquet_path).set_index(
                "sa2_code_2021"
            )

        xlsx = self.fetch()
        df = self._parse_xlsx(xlsx)
        df.reset_index().to_parquet(self._parquet_path, index=False)
        return df

    # ---- release resolution --------------------------------------------

    def _resolve_release(self) -> None:
        if self._resolved_release is not None:
            return

        # Fetch the landing page and pull the latest period directory
        # from any DS0003 link.
        _log.debug("Resolving ERP release via %s", self._landing_url)
        resp = self._session.get(
            self._landing_url, timeout=self._timeout
        )
        resp.raise_for_status()
        html = resp.text

        # Match links like /regional-population/2024-25/32180DS0003_2001-25.xlsx
        pattern = re.compile(
            r'href="([^"]*regional-population/'
            r"(?P<period>\d{4}-\d{2,4})/"
            r'[^"]*' + _LONG_HISTORY_SA2_FRAGMENT + r'[^"]*\.xlsx)"',
            re.IGNORECASE,
        )

        all_matches = pattern.finditer(html)
        candidates: list[tuple[str, str]] = []
        for m in all_matches:
            href = m.group(1)
            period = m.group("period")
            release_year = period.split("-", 1)[0]
            url = href if href.startswith("http") else (
                "https://www.abs.gov.au" + (href if href.startswith("/") else "/" + href)
            )
            candidates.append((release_year, url))

        if not candidates:
            raise RuntimeError(
                f"Could not find any {_LONG_HISTORY_SA2_FRAGMENT} link on "
                f"{self._landing_url}. Page layout may have changed."
            )

        if self._release_request == "latest":
            picked = max(candidates, key=lambda t: t[0])
        else:
            matching = [c for c in candidates if c[0] == self._release_request]
            if not matching:
                raise RuntimeError(
                    f"ERP release {self._release_request!r} not found. "
                    f"Available: {sorted({c[0] for c in candidates}, reverse=True)}"
                )
            picked = matching[0]

        self._resolved_release = picked[0]
        self._resolved_url = picked[1]
        _log.info(
            "Resolved ERP release=%s, url=%s",
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_xlsx(xlsx_path: Path) -> pd.DataFrame:
        import openpyxl  # noqa: PLC0415

        wb = openpyxl.load_workbook(
            xlsx_path, read_only=True, data_only=True
        )
        if "Table 1" not in wb.sheetnames:
            wb.close()
            raise RuntimeError(
                f"ERP workbook {xlsx_path} has no 'Table 1' sheet. "
                f"Sheets: {wb.sheetnames}"
            )
        ws = wb["Table 1"]
        rows: list[list[object]] = []
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
        wb.close()

        # Locate the year-header row. Cells at column index
        # _TABLE1_FIRST_YEAR_COL onwards should be year integers.
        # Sometimes the layout has years on the same row as the
        # geography labels; sometimes on the row above. Scan the
        # first ~10 rows.
        year_header_row_idx = -1
        year_cols: dict[int, int] = {}
        for ridx in range(min(10, len(rows))):
            r = rows[ridx]
            if len(r) <= _TABLE1_FIRST_YEAR_COL:
                continue
            candidate: dict[int, int] = {}
            for col_idx in range(_TABLE1_FIRST_YEAR_COL, len(r)):
                cell = r[col_idx]
                if isinstance(cell, int) and 1900 < cell < 2100:
                    candidate[int(cell)] = col_idx
                elif isinstance(cell, str) and cell.strip().isdigit():
                    yr = int(cell.strip())
                    if 1900 < yr < 2100:
                        candidate[yr] = col_idx
            if candidate:
                year_header_row_idx = ridx
                year_cols = candidate
                break

        if not year_cols or year_header_row_idx < 0:
            raise RuntimeError(
                "No year columns found in any ERP header row. Inspected "
                f"first {min(10, len(rows))} rows."
            )

        # Data starts at the row *after* the geography-labels row,
        # which is right below the year-header row in the current
        # releases. (Row 4 = years, row 5 = labels+units, row 6 =
        # data.) Skip ahead until we find a row whose SA2-code cell
        # is actually a 9-digit code.
        data_start = year_header_row_idx + 1
        while data_start < len(rows):
            r = rows[data_start]
            if len(r) > _TABLE1_SA2_CODE_COL:
                cell = r[_TABLE1_SA2_CODE_COL]
                if cell is not None:
                    s = str(cell).strip()
                    if len(s) == 9 and s.isdigit():
                        break
            data_start += 1

        latest_year = max(year_cols)

        records: list[dict[str, object]] = []
        for row in rows[data_start:]:
            if len(row) <= _TABLE1_SA2_CODE_COL:
                continue
            sa2_raw = row[_TABLE1_SA2_CODE_COL]
            sa2 = "" if sa2_raw is None else str(sa2_raw).strip()
            if not (len(sa2) == 9 and sa2.isdigit()):
                continue
            state_name = (
                str(row[1]).strip() if row[1] is not None else ""
            )
            rec: dict[str, object] = {
                "sa2_code_2021": sa2,
                "state_abbreviation": _state_to_abbreviation(state_name),
                "reference_year": latest_year,
            }
            # Latest year's population goes to a stable column name.
            if latest_year in year_cols:
                rec["population_total"] = _coerce_number(
                    row[year_cols[latest_year]]
                )
            # Full year history.
            for year, col_idx in year_cols.items():
                key = f"population_history_{year}"
                rec[key] = (
                    _coerce_number(row[col_idx])
                    if col_idx < len(row)
                    else None
                )
            records.append(rec)

        if not records:
            raise RuntimeError(
                f"No SA2 data rows in {xlsx_path}"
            )

        df = pd.DataFrame.from_records(records)
        return df.set_index("sa2_code_2021")


# ---- helpers ------------------------------------------------------------


_STATE_NAMES = {
    "new south wales": "NSW",
    "victoria": "VIC",
    "queensland": "QLD",
    "south australia": "SA",
    "western australia": "WA",
    "tasmania": "TAS",
    "northern territory": "NT",
    "australian capital territory": "ACT",
    "other territories": "OT",
}


def _state_to_abbreviation(name: str) -> str:
    return _STATE_NAMES.get(name.lower().strip(), name.strip()[:3].upper())


def _coerce_number(cell: object) -> object:
    """Coerce a numeric-looking cell to int / float / None."""
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return cell
    s = str(cell).strip()
    if not s or s.lower() in ("np", "na", "n/a", "-", "..", "."):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return None
