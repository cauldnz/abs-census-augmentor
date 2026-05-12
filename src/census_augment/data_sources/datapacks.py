"""Census DataPack download, extraction, and metadata parsing (spec §4.2)."""

from __future__ import annotations

import logging
import pickle
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from ..config import CensusConfig
from ._base import _AbsZipDataSource

_log = logging.getLogger(__name__)

# Pickle suffix for the per-xlsx parsed-metadata cache.
# Parsing the 119-table descriptor sheet via openpyxl takes ~1.8 s on a
# fast NVMe and proportionally more under bind-mounted filesystems
# (issue #43). The parsed result is a small (~6 kB) dict of dataclasses
# that pickles in ~50 ms, so we cache it next to the xlsx and re-use on
# any subsequent load. The xlsx mtime is the cache key — bumping it
# (via `fetch(refresh=True)`) invalidates the cache automatically.
_METADATA_CACHE_SUFFIX = ".parsed.pkl"

# Filename pattern matchers for table CSVs (e.g. G01, G02, G09A)
_TABLE_ID_PATTERN = re.compile(r"^G\d+[A-Z]?$")

# Common column names in ABS DataPack CSVs for the SA2 code
_SA2_CODE_CANDIDATES = ("SA2_CODE_2021", "SA2_CODE21", "SA2_MAINCODE_2021")

# Pattern matching the descriptor xlsx among the ~3 metadata files in the ZIP
_METADATA_FILENAME_PATTERN = re.compile(r"Metadata.*GCP.*DataPack.*\.xlsx$", re.IGNORECASE)

# Real ABS sheet names — match case- and whitespace-insensitively
_DESCRIPTOR_SHEET_CANDIDATES = (
    "Cell Descriptors Information",
    "Cell descriptors information",
    "CellDescriptors",
)
_TABLE_SHEET_CANDIDATES = (
    "Table Number, Name, Population",
    "Table Number Name Population",
)

# Required column markers used to find the descriptor sheet's header row.
# Matched against (lowercased, whitespace-stripped) cell values.
_DESCRIPTOR_HEADER_MARKERS = frozenset({"short", "long", "datapackfile"})

# Map config descriptor mode → metadata column whose value is the *code*
# referenced from a config variable (spec §6.2)
_DESCRIPTOR_MODE_TO_COLUMN = {
    "short-header": "Short",
    "long-header": "Long",
    "sequential": "Sequential",
}

# Column in the descriptor sheet whose value we expose as the human description
_DESCRIPTION_COLUMN = "Columnheadingdescriptioninprofile"
_DATAPACKFILE_COLUMN = "DataPackfile"


@dataclass(frozen=True)
class ColumnMetadata:
    """Description of a single DataPack column."""

    table_id: str
    code: str
    description: str


@dataclass(frozen=True)
class TableMetadata:
    """Description of a single DataPack table."""

    table_id: str
    name: str
    columns: dict[str, ColumnMetadata]


@dataclass(frozen=True)
class DataPackMetadata:
    """Full DataPack metadata: maps table IDs to table descriptions."""

    tables: dict[str, TableMetadata]

    def has_table(self, table_id: str) -> bool:
        return table_id in self.tables

    def has_column(self, table_id: str, column_code: str) -> bool:
        return self.has_table(table_id) and column_code in self.tables[table_id].columns

    def describe(self, table_id: str, column_code: str) -> str | None:
        if not self.has_column(table_id, column_code):
            return None
        return self.tables[table_id].columns[column_code].description

    def all_columns(self) -> Iterator[ColumnMetadata]:
        for table in self.tables.values():
            yield from table.columns.values()


def extract_table_id(filename: str) -> str | None:
    """Return the ``G##`` table ID from ``filename``, or ``None`` if absent."""
    stem = Path(filename).stem
    for part in re.split(r"[_\-\s.]", stem):
        if _TABLE_ID_PATTERN.match(part):
            return part
    return None


class DataPacksDataSource(_AbsZipDataSource):
    """Download, extract, and parse ABS Census DataPacks (spec §4.2).

    Filename: ``{year}_{profile}_{level}_for_{region}_{descriptor}.zip``
    (e.g. ``2021_GCP_SA2_for_AUS_short-header.zip``).

    Real DataPack layout (verified against 2021 GCP):
    - CSVs live in a long-named subdirectory; we discover them by ``rglob``.
    - Multiple ``.xlsx`` files exist in ``Metadata/``; only the descriptor
      file (``Metadata_*GCP*DataPack*.xlsx``) is parsed.
    - The descriptor sheet has ~10 rows of title/blank padding above the
      header — we auto-detect the header row.
    - ``Columnheadingdescriptioninprofile`` is the human description (not
      ``Long``, which is an underscored almost-sentence).
    - The code column used (``Sequential`` / ``Short`` / ``Long``) depends
      on ``census.descriptor``.

    See ``tools/verify_real_parsers.py`` for a real-data smoke check.
    """

    _label = "DataPack ZIP"

    def __init__(
        self,
        *,
        census: CensusConfig,
        base_url: str,
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 1024 * 1024,
        timeout: float = 600.0,
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
        return f"{c.year}_{c.profile}_{c.level}_for_{c.region}_{c.descriptor}.zip"

    def is_cached(self) -> bool:
        return bool(self._table_csvs())

    def fetch(self, refresh: bool = False) -> Path:
        if not refresh and self.is_cached():
            _log.debug("Using cached DataPack at %s", self.extract_dir)
            return self.extract_dir
        self._download()
        self._extract()
        if not self.is_cached():
            raise RuntimeError(
                f"No table CSVs (G##.csv) found in {self.extract_dir}; "
                f"DataPack layout may have changed."
            )
        return self.extract_dir

    def list_tables(self, refresh: bool = False) -> list[str]:
        self.fetch(refresh=refresh)
        return sorted(self._table_csvs().keys())

    def load_table(self, table_id: str, refresh: bool = False) -> pd.DataFrame:
        self.fetch(refresh=refresh)
        csvs = self._table_csvs()
        if table_id not in csvs:
            raise KeyError(
                f"Table {table_id!r} not found in DataPack; available: {sorted(csvs.keys())}"
            )
        df = pd.read_csv(csvs[table_id], dtype={c: str for c in _SA2_CODE_CANDIDATES})
        sa2_col = self._detect_sa2_column(df)
        return df.set_index(sa2_col)

    def load_metadata(self, refresh: bool = False) -> DataPackMetadata:
        """Load the DataPack metadata Excel as a :class:`DataPackMetadata`.

        Uses the descriptor mode from ``self._census.descriptor`` to choose
        which descriptor-sheet column maps to the user-facing code.

        Caches the parsed result to ``<xlsx>.parsed.pkl`` next to the
        descriptor xlsx so subsequent loads skip the openpyxl parse — see
        :data:`_METADATA_CACHE_SUFFIX` for the rationale. ``refresh=True``
        re-runs ``fetch`` which bumps the xlsx mtime and so invalidates
        the cache.
        """
        self.fetch(refresh=refresh)
        xlsx = self._metadata_xlsx()
        if xlsx is None:
            raise RuntimeError(
                f"No metadata .xlsx file (matching {_METADATA_FILENAME_PATTERN.pattern}) "
                f"found in {self.extract_dir}; DataPack layout may have changed."
            )
        descriptor = self._census.descriptor
        cached = _load_metadata_cache(xlsx, descriptor=descriptor)
        if cached is not None:
            return cached
        metadata = _parse_metadata_xlsx(xlsx, descriptor=descriptor)
        _save_metadata_cache(xlsx, descriptor=descriptor, metadata=metadata)
        return metadata

    # ---- internals -------------------------------------------------------

    def _table_csvs(self) -> dict[str, Path]:
        if not self.extract_dir.exists():
            return {}
        result: dict[str, Path] = {}
        for csv_path in self.extract_dir.rglob("*.csv"):
            tid = extract_table_id(csv_path.name)
            if tid is not None and tid not in result:
                result[tid] = csv_path
        return result

    def _metadata_xlsx(self) -> Path | None:
        if not self.extract_dir.exists():
            return None
        for xlsx in self.extract_dir.rglob("*.xlsx"):
            if _METADATA_FILENAME_PATTERN.search(xlsx.name):
                return xlsx
        return None

    @staticmethod
    def _detect_sa2_column(df: pd.DataFrame) -> str:
        for candidate in _SA2_CODE_CANDIDATES:
            if candidate in df.columns:
                return candidate
        raise RuntimeError(
            f"No SA2 code column found; expected one of "
            f"{list(_SA2_CODE_CANDIDATES)}; got: {list(df.columns)}"
        )


# ---- metadata parsing -----------------------------------------------------


def _parse_metadata_xlsx(xlsx: Path, descriptor: str) -> DataPackMetadata:
    """Parse the descriptor sheet of a DataPack metadata Excel file."""
    if descriptor not in _DESCRIPTOR_MODE_TO_COLUMN:
        raise ValueError(
            f"Unknown descriptor mode {descriptor!r}; "
            f"expected one of {list(_DESCRIPTOR_MODE_TO_COLUMN)}"
        )
    code_column_name = _DESCRIPTOR_MODE_TO_COLUMN[descriptor]

    all_sheets = pd.read_excel(xlsx, sheet_name=None, header=None)

    descriptor_raw = _select_sheet(
        all_sheets, _DESCRIPTOR_SHEET_CANDIDATES, label="descriptor", xlsx=xlsx
    )
    descriptor_df = _slice_at_header(descriptor_raw, _DESCRIPTOR_HEADER_MARKERS, xlsx=xlsx)

    col_lookup = {_norm(str(col)): col for col in descriptor_df.columns if pd.notna(col)}
    code_col = col_lookup.get(_norm(code_column_name))
    desc_col = col_lookup.get(_norm(_DESCRIPTION_COLUMN))
    table_col = col_lookup.get(_norm(_DATAPACKFILE_COLUMN))
    missing = [
        n
        for n, c in (
            (code_column_name, code_col),
            (_DESCRIPTION_COLUMN, desc_col),
            (_DATAPACKFILE_COLUMN, table_col),
        )
        if c is None
    ]
    if missing:
        raise RuntimeError(
            f"Descriptor sheet in {xlsx} missing required columns: {missing}; "
            f"got: {list(descriptor_df.columns)}"
        )
    assert code_col is not None and desc_col is not None and table_col is not None

    tables_cols: dict[str, dict[str, ColumnMetadata]] = {}
    for _, row in descriptor_df.iterrows():
        tid = _str_or_none(row[table_col])
        code = _str_or_none(row[code_col])
        desc = _str_or_none(row[desc_col]) or ""
        if tid is None or code is None:
            continue
        tables_cols.setdefault(tid, {})[code] = ColumnMetadata(
            table_id=tid, code=code, description=desc
        )

    table_names = _try_read_table_names(all_sheets)

    return DataPackMetadata(
        tables={
            tid: TableMetadata(
                table_id=tid,
                name=table_names.get(tid, ""),
                columns=cols,
            )
            for tid, cols in tables_cols.items()
        }
    )


def _metadata_cache_path(xlsx: Path, descriptor: str) -> Path:
    """Pickle cache path next to the descriptor xlsx.

    Keyed by descriptor mode in the filename — switching from
    ``short-header`` to ``long-header`` picks a different code column,
    so the parsed result is mode-specific. Keeping the mode in the
    cache filename means each mode gets its own cache and we never
    have to worry about cross-mode invalidation.
    """
    return xlsx.with_name(xlsx.name + f".{descriptor}{_METADATA_CACHE_SUFFIX}")


def _load_metadata_cache(xlsx: Path, *, descriptor: str) -> DataPackMetadata | None:
    """Return cached metadata if newer than ``xlsx``; else ``None``.

    Silent failure: a corrupt pickle, a pickle from an older incompatible
    schema, or any unpickling error just returns ``None`` and lets the
    caller re-parse from xlsx. The cache is regenerated lazily.
    """
    cache_path = _metadata_cache_path(xlsx, descriptor)
    if not cache_path.exists():
        return None
    try:
        if cache_path.stat().st_mtime < xlsx.stat().st_mtime:
            # xlsx newer than cache — cache is stale, ignore.
            return None
        with cache_path.open("rb") as fh:
            obj = pickle.load(fh)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError) as e:
        _log.debug("Ignoring corrupt metadata cache at %s: %s", cache_path, e)
        return None
    if not isinstance(obj, DataPackMetadata):
        _log.debug(
            "Ignoring metadata cache at %s: expected DataPackMetadata, got %s",
            cache_path,
            type(obj).__name__,
        )
        return None
    return obj


def _save_metadata_cache(xlsx: Path, *, descriptor: str, metadata: DataPackMetadata) -> None:
    """Atomically write the metadata cache next to ``xlsx``.

    Atomic-rename so a partially written cache never gets read back as
    valid. Silent on any write failure — the cache is an optimisation,
    not a correctness requirement.
    """
    cache_path = _metadata_cache_path(xlsx, descriptor)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    try:
        with tmp_path.open("wb") as fh:
            pickle.dump(metadata, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp_path.replace(cache_path)
    except OSError as e:
        _log.debug("Could not write metadata cache to %s: %s", cache_path, e)
        # Best-effort cleanup of the tmp file.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _select_sheet(
    all_sheets: dict[str, pd.DataFrame],
    candidates: tuple[str, ...],
    *,
    label: str,
    xlsx: Path,
) -> pd.DataFrame:
    candidates_normalised = {_norm(c) for c in candidates}
    for sheet_name, df in all_sheets.items():
        if _norm(sheet_name) in candidates_normalised:
            return df
    raise RuntimeError(
        f"No {label} sheet found in {xlsx}; expected one of "
        f"{list(candidates)}; got: {list(all_sheets.keys())}"
    )


def _slice_at_header(
    df: pd.DataFrame, required_markers: frozenset[str], *, xlsx: Path
) -> pd.DataFrame:
    """Find the header row by scanning for ``required_markers`` and slice from there.

    The returned DataFrame uses the discovered header row as its column
    names and contains only the rows below it.
    """
    for i in range(len(df)):
        values = {_norm(str(v)) for v in df.iloc[i].tolist() if pd.notna(v)}
        if required_markers.issubset(values):
            headers = [str(v).strip() if pd.notna(v) else "" for v in df.iloc[i].tolist()]
            body = df.iloc[i + 1 :].copy()
            body.columns = pd.Index(headers)
            return body
    raise RuntimeError(
        f"Could not find descriptor header row with markers "
        f"{sorted(required_markers)} in {xlsx}; scanned {len(df)} rows."
    )


def _try_read_table_names(all_sheets: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Best-effort extraction of (table_id → name) from the table sheet.

    Absent or malformed table sheet → empty dict (TableMetadata.name == "").
    """
    candidates_normalised = {_norm(c) for c in _TABLE_SHEET_CANDIDATES}
    table_raw = None
    for sheet_name, df in all_sheets.items():
        if _norm(sheet_name) in candidates_normalised:
            table_raw = df
            break
    if table_raw is None:
        return {}

    try:
        body = _slice_at_header(
            table_raw, frozenset({"tablenumber", "tablename"}), xlsx=Path("<table>")
        )
    except RuntimeError:
        return {}

    col_lookup = {_norm(str(c)): c for c in body.columns if pd.notna(c)}
    num_col = col_lookup.get("tablenumber")
    name_col = col_lookup.get("tablename")
    if num_col is None or name_col is None:
        return {}

    result: dict[str, str] = {}
    for _, row in body.iterrows():
        tid = _str_or_none(row[num_col])
        name = _str_or_none(row[name_col])
        if tid is None or name is None:
            continue
        result[tid] = name
    return result


def _norm(s: str) -> str:
    """Lowercase, whitespace-stripped form for case-insensitive header matching."""
    return re.sub(r"\s+", "", s).lower()


def _str_or_none(v: object) -> str | None:
    """Coerce ``v`` to a stripped string, returning None for NaN / empty values."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s
