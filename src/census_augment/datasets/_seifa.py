"""SEIFA 2021 fetcher (spec §20, dataset id ``seifa_2021``).

ABS publishes the Socio-Economic Indexes for Areas as a single XLSX
workbook per geographic level, structured as a multi-sheet workbook:

- **Contents** — table of contents (skipped).
- **Table 1** — summary view. All four indexes' ``Score`` + Australia
  ``Decile`` in one table, plus URP. Convenient to parse but limited
  to score+decile per index.
- **Tables 2–5** — one per index in the order IRSD, IRSAD, IER, IEO.
  Full flavour set (Score, Aus Rank/Decile/Percentile, State
  Rank/Decile/Percentile, SA1 min/max score, % URP without score).
- **Table 6** — Excluded SA2s (skipped).
- **Explanatory Notes** — methodology (skipped).

We pull the SA2 file (~150 KB compressed), parse Tables 1–5 into a
single wide DataFrame, and expose the union of fields per the spec
(``datasets/seifa_2021.md``).

The file URL is direct-link (no scraping needed for the default
release). Subsequent releases would land as their own dataset spec.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import requests

from ._xlsx_base import _AbsXlsxDataset

_log = logging.getLogger(__name__)

# Direct-link to the SA2 SEIFA 2021 workbook on the ABS site.
# Confirmed via the latest-release landing page on 2026-05-09.
DEFAULT_SEIFA_2021_URL = (
    "https://www.abs.gov.au/statistics/people/people-and-communities/"
    "socio-economic-indexes-areas-seifa-australia/2021/"
    "Statistical%20Area%20Level%202%2C%20Indexes%2C%20SEIFA%202021.xlsx"
)


# Sheet → which index it details. Position-indexed because sheet names
# are generic ("Table 2" / "Table 3" / ...). The order is per ABS's
# convention, confirmed against the 2021 release.
_INDEX_SHEETS: list[tuple[str, str]] = [
    ("Table 2", "irsd"),
    ("Table 3", "irsad"),
    ("Table 4", "ier"),
    ("Table 5", "ieo"),
]

# Tables 2–5 share a fixed-position layout. Column index → output field.
# Column 4 (blank) and column 8 (blank) are spacers in the source.
_INDEX_TABLE_COLS: dict[str, int] = {
    "sa2_code": 0,
    "sa2_name": 1,
    "urp": 2,
    "score": 3,
    "aus_rank": 5,
    "aus_decile": 6,
    "aus_percentile": 7,
    "state_abbreviation": 9,
    "state_rank": 10,
    "state_decile": 11,
    "state_percentile": 12,
    "sa1_min": 13,
    "sa1_max": 14,
    "pct_urp_no_score": 15,
}

# Table 1 (summary) layout. Column index → output field.
_SUMMARY_TABLE_COLS: dict[str, int] = {
    "sa2_code": 0,
    "sa2_name": 1,
    "irsd_score": 2,
    "irsd_aus_decile": 3,
    "irsad_score": 4,
    "irsad_aus_decile": 5,
    "ier_score": 6,
    "ier_aus_decile": 7,
    "ieo_score": 8,
    "ieo_aus_decile": 9,
    "urp": 10,
}

# Header row index — Tables 1–5 all use row 5 (zero-indexed) as the
# data header. Data starts at row 6. We sanity-check this by looking
# for the SA2-code header text rather than trusting the row number,
# but the fallback default is row 5.
_DEFAULT_HEADER_ROW = 5
_SA2_CODE_HEADER_FRAGMENTS = (
    "Statistical Area Level 2 (SA2) 9-Digit",
    "SA2 9-Digit",
    "SA2 9 Digit",
)


class SeifaDataSource(_AbsXlsxDataset):
    """Fetch + load the SEIFA 2021 SA2 XLSX (spec §20, dataset id ``seifa_2021``).

    Implements the :class:`DatasetFetcher` Protocol via the shared
    :class:`_AbsXlsxDataset` base. Files land at
    ``<root>/seifa-{release}.xlsx``; parsed parquet alongside as
    ``seifa-{release}.parquet`` (so subsequent loads skip the XLSX
    parse).
    """

    _label = "SEIFA 2021 SA2 workbook"
    _cache_glob = "seifa-*.xlsx"

    def __init__(
        self,
        *,
        release: str = "2021",
        root: Path,
        url: str | None = None,
        session: requests.Session | None = None,
        chunk_size: int = 256 * 1024,
        timeout: float = 60.0,
    ) -> None:
        if release != "2021":
            # Future releases land as their own dataset spec.
            raise ValueError(
                f"SEIFA release must be '2021' for now (got {release!r}). "
                "When ABS publishes 2026, register it as a separate "
                "dataset spec."
            )
        super().__init__(
            release=release,
            root=root,
            session=session,
            chunk_size=chunk_size,
            timeout=timeout,
        )
        # SEIFA has a static URL (no landing-page scrape), so we
        # populate the resolved attrs eagerly in __init__ — the
        # base's _resolve_release() then becomes a no-op early-exit.
        self._resolved_release = release
        self._resolved_url = url or DEFAULT_SEIFA_2021_URL

    # ---- hooks ---------------------------------------------------------

    def _filename_stem(self, release: str) -> str:
        return f"seifa-{release}"

    def _resolve_release(self) -> None:
        # No-op: __init__ populated both attributes eagerly. The base's
        # fetch()/resolved_release pathway expects this to be idempotent
        # and a no-op when already resolved is exactly that.
        return

    # ---- parsing --------------------------------------------------------

    @staticmethod
    def _parse_xlsx(xlsx_path: Path) -> pd.DataFrame:
        """Parse Tables 1–5 of the SEIFA workbook into a single DataFrame.

        Strategy: parse each detail sheet (Tables 2–5, one per index)
        by fixed column positions. The summary table (Table 1) is
        redundant with Tables 2–5 (Table 1's Score + AusDecile cells
        are subsets of Tables 2–5) so we skip it. Each detail sheet's
        columns get prefixed with the index name to produce
        ``irsd_score``, ``ier_aus_decile``, etc.

        Returns a DataFrame indexed by ``sa2_code_2021`` with columns
        per the spec at ``datasets/seifa_2021.md``.
        """
        import openpyxl  # noqa: PLC0415 — lazy import keeps cold start cheap

        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)

        per_index_dfs: list[pd.DataFrame] = []
        sheet_names = set(wb.sheetnames)
        for sheet_name, prefix in _INDEX_SHEETS:
            if sheet_name not in sheet_names:
                _log.warning(
                    "Expected sheet %r missing from %s; skipping %s",
                    sheet_name,
                    xlsx_path,
                    prefix,
                )
                continue
            sheet = wb[sheet_name]
            df_one = SeifaDataSource._parse_index_sheet(sheet, prefix)
            per_index_dfs.append(df_one)

        if not per_index_dfs:
            wb.close()
            raise RuntimeError(f"No SEIFA index sheets (Tables 2-5) found in {xlsx_path}")

        wb.close()

        # Outer-join all four index frames on the SA2 code (some SA2s
        # may be excluded from one index but not another — the spec
        # documents this). The first frame contributes URP and
        # state_abbreviation since those are common to all four.
        merged = per_index_dfs[0]
        for df_more in per_index_dfs[1:]:
            # Drop the duplicate URP / state cols from the joiner.
            df_more = df_more.drop(
                columns=[c for c in ("urp", "state_abbreviation") if c in df_more.columns],
                errors="ignore",
            )
            merged = merged.join(df_more, how="outer")

        return merged

    @staticmethod
    def _parse_index_sheet(sheet: object, prefix: str) -> pd.DataFrame:
        """Parse a single index detail sheet (Table 2/3/4/5).

        Returns a DataFrame indexed by sa2_code_2021 with columns
        named ``{prefix}_{flavour}`` (e.g. ``irsd_score``,
        ``irsd_aus_decile``). URP and state_abbreviation are passed
        through bare (unprefixed) since they're shared across indexes.
        """
        # Read all rows — the sheet is small enough (~2400 rows × 16 cols).
        raw: list[list[object]] = []
        for row in sheet.iter_rows(values_only=True):  # type: ignore[attr-defined]
            raw.append(list(row))

        # Confirm the header row by looking for the SA2-code text in
        # the expected row; fall back to default if it's not where we
        # expect (so a small layout shift doesn't break us).
        header_row_idx = SeifaDataSource._find_header_row(raw)
        data_start = header_row_idx + 1

        records: list[dict[str, object]] = []
        for row in raw[data_start:]:
            if len(row) <= _INDEX_TABLE_COLS["sa2_code"]:
                continue
            sa2_raw = row[_INDEX_TABLE_COLS["sa2_code"]]
            sa2_code = "" if sa2_raw is None else str(sa2_raw).strip()
            if not (len(sa2_code) == 9 and sa2_code.isdigit()):
                continue  # skip aggregates / footers / blanks

            rec: dict[str, object] = {"sa2_code_2021": sa2_code}
            for field, col_idx in _INDEX_TABLE_COLS.items():
                if field == "sa2_code":
                    continue
                if col_idx >= len(row):
                    rec[_field_name(prefix, field)] = None
                    continue
                value = SeifaDataSource._coerce(row[col_idx])
                rec[_field_name(prefix, field)] = value
            records.append(rec)

        if not records:
            raise RuntimeError(
                f"No data rows found below the header in sheet for prefix {prefix!r}"
            )

        df = pd.DataFrame.from_records(records)
        return df.set_index("sa2_code_2021")

    @staticmethod
    def _find_header_row(raw: list[list[object]]) -> int:
        """Locate the header row by scanning for SA2-code header text.

        Returns the row index, defaulting to ``_DEFAULT_HEADER_ROW``
        if the scan misses (some releases shift the preamble length by
        a row or two).
        """
        for i in range(min(15, len(raw))):
            row_text = " ".join("" if c is None else str(c) for c in raw[i])
            for fragment in _SA2_CODE_HEADER_FRAGMENTS:
                if fragment in row_text:
                    return i
        return _DEFAULT_HEADER_ROW

    @staticmethod
    def _coerce(cell: object) -> object:
        """Coerce a cell into the right Python type.

        - None / empty / 'np' / 'NA' / '-' / similar → None
        - Numeric (int or float) values stay as-is
        - Otherwise convert to str

        ABS uses different null sentinels per release — ``np`` (not
        published) in 2016, ``NA`` in 2021, sometimes ``-`` or blank.
        """
        if cell is None:
            return None
        if isinstance(cell, (int, float)):
            return cell
        s = str(cell).strip()
        if not s or s.lower() in ("np", "na", "n/a", "-", "nan", "null", "..", "."):
            return None
        # Int?
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        # Float?
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
        return s


def _field_name(prefix: str, field: str) -> str:
    """Apply the index prefix to per-flavour fields, leaving shared
    fields (URP, state_abbreviation) bare.
    """
    if field in ("urp", "state_abbreviation"):
        return field
    return f"{prefix}_{field}"
