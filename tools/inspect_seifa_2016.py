"""One-off SEIFA 2016 SA2 XLSX schema probe (Phase F.3 unblock).

Downloads the live ABS SEIFA 2016 SA2 spreadsheet and dumps everything the
``census_augment.datasets._seifa`` parser will need to know to support a
second release alongside SEIFA 2021:

- All sheet names.
- For each sheet: the first ~20 rows (so we can spot the preamble + the
  actual data header row).
- For sheets that look like SA2 data: the header row's column labels
  plus 3 sample data rows.
- The workbook-wide structural notes (number of sheets, byte size, etc.)
  so we can sanity-check the download against ABS publication notes.

Run via ``uv run python tools/inspect_seifa_2016.py``. Paste the stdout
back so the maintainer-facing Phase F.3 PR can build a hermetic
``seifa_2016`` fetcher off a known schema, per CLAUDE.md's
"Real Data First" rule. The script is idempotent — re-running fetches
fresh and re-prints. Pass ``--refresh`` to force re-download.

The ABS 2016 file is in legacy ``.xls`` (Excel 97-2003 binary) format,
not ``.xlsx``. openpyxl can't read ``.xls``; this script uses
``pandas.read_excel`` which auto-routes to the right backend (xlrd 1.2.0
for .xls, openpyxl for .xlsx). If your venv doesn't have xlrd, install
it: ``uv pip install 'xlrd==1.2.0'``.

Not part of the pytest suite; this is a one-off discovery probe (same
class as ``tools/fetch_real_data.py`` and ``verify_real_parsers.py``).
Once ``seifa_2016`` registers, the equivalent post-fetch shape check
moves into ``verify_real_parsers.py`` as a permanent drift detector.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import requests

#: SEIFA 2016 SA2 XLS download URL. Captured 2026-05-19 from
#: https://www.abs.gov.au/AUSSTATS/abs@.nsf/DetailsPage/2033.0.55.0012016
#: via WebFetch. The Lotus Notes openagent endpoint logs the download
#: and 302s to the actual file on ``www.ausstats.abs.gov.au``. The query
#: string after the filename is ABS's audit log payload (catalogue id +
#: section + UNID + flags + dates + label); changing any of it doesn't
#: break the download but might affect ABS's hit telemetry.
_SEIFA_2016_URL = (
    "https://www.abs.gov.au/AUSSTATS/subscriber.nsf/log"
    "?openagent"
    "&2033055001%20-%20sa2%20indexes.xls"
    "&2033.0.55.001"
    "&Data%20Cubes"
    "&C9F7AD36397CB43DCA25825D000F917C"
    "&0"
    "&2016"
    "&27.03.2018"
    "&Latest"
)

#: Where to cache the download. Sibling to ``tools/`` so the gitignored
#: ``data/`` tree (per repo policy) holds the artefact.
_LOCAL_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "seifa_2016_raw"
    / "2033055001_seifa_2016_sa2.xls"
)

#: How many rows to print per sheet's preamble dump. ~15-20 covers
#: every preamble layout ABS has shipped in the 2011-2021 SEIFA era;
#: more than that and the output becomes unreadable when pasted back.
_PREAMBLE_ROWS = 20

#: How many sample data rows to print after the detected header.
_SAMPLE_DATA_ROWS = 3


def _download(url: str, dest: Path) -> Path:
    """Stream the SEIFA 2016 XLS to ``dest``. Idempotent.

    Skips the network when ``dest`` already exists. Pass ``--refresh``
    to force re-fetch (e.g. ABS revised the file in place).
    """
    if dest.exists() and "--refresh" not in sys.argv:
        print(f"[cache] using existing {dest} ({dest.stat().st_size:,} bytes)")
        return dest
    print(f"[fetch] {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        tmp.replace(dest)
    print(f"[save]  {dest} ({dest.stat().st_size:,} bytes)")
    return dest


def _pick_engine(path: Path) -> Literal["xlrd", "openpyxl"] | None:
    """Map the file extension to a pandas Excel engine.

    .xls → xlrd; .xlsx → openpyxl. ``None`` (passed to
    ``pd.ExcelFile(engine=None)``) lets pandas auto-detect for other
    extensions (.xlsm, .xlsb).
    """
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return "xlrd"
    if suffix == ".xlsx":
        return "openpyxl"
    return None


def _row_repr(values: list[Any]) -> str:
    """One-line repr of a row, truncated per-cell to keep the
    paste-back manageable."""
    cells: list[str] = []
    for v in values:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            cells.append("·")
            continue
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        cells.append(repr(s) if isinstance(v, str) else s)
    return " | ".join(cells)


def _dump_sheet(name: str, df: pd.DataFrame) -> None:
    """Print a structured probe of one sheet's raw cell values.

    ``df`` is the result of ``pd.read_excel(..., header=None)`` so the
    indexing reflects the source workbook's row positions (1-indexed for
    human readability — ``df.iloc[0]`` is the spreadsheet's row 1).
    """
    print(f"\n  sheet name: {name!r}")
    print(f"  dimensions: rows={len(df)}, cols={len(df.columns)}")

    preamble_count = min(_PREAMBLE_ROWS, len(df))
    print(f"  --- first {preamble_count} rows (look for the data header row) ---")
    for i in range(preamble_count):
        row = df.iloc[i].tolist()
        if all(v is None or (isinstance(v, float) and pd.isna(v)) for v in row):
            print(f"    [{i + 1:>3}] <empty>")
            continue
        print(f"    [{i + 1:>3}] {_row_repr(row)}")

    # Heuristic: the data header row is the first row whose first cell
    # contains a string that looks like an SA2 code label (e.g. "2016 SA2",
    # "SA2 code", "Statistical Area Level 2 (SA2) Code"). This matches the
    # 2021 file's layout where the preamble runs ~5 rows and the header
    # is the 6th. Worth surfacing so the maintainer can eyeball the
    # parser's row-detection heuristic.
    candidate_header: int | None = None
    for i in range(preamble_count):
        first = df.iloc[i, 0] if len(df.columns) > 0 else None
        if isinstance(first, str) and any(
            tok in first.lower() for tok in ("sa2", "statistical area")
        ):
            candidate_header = i
            break
    if candidate_header is None:
        print("  (no SA2-shaped header row spotted in the first 20 rows)")
        return
    print(f"\n  candidate data header row: {candidate_header + 1}")
    print(f"  --- header + {_SAMPLE_DATA_ROWS} sample data rows ---")
    end = min(candidate_header + 1 + _SAMPLE_DATA_ROWS, len(df))
    for i in range(candidate_header, end):
        print(f"    [{i + 1:>3}] {_row_repr(df.iloc[i].tolist())}")


def _dump_workbook(path: Path) -> None:
    print(f"\n[open]  {path}")
    engine = _pick_engine(path)
    print(f"  engine: {engine or '(auto)'}")
    try:
        excel = pd.ExcelFile(path, engine=engine)
    except ImportError as e:
        sys.stderr.write(
            f"[fail]  Couldn't load pandas Excel engine for {path.suffix}: {e}\n"
            f"        For .xls files: ``uv pip install 'xlrd==1.2.0'`` (the "
            f"        newer xlrd 2.x dropped .xls support).\n"
        )
        raise
    print(f"  sheet count: {len(excel.sheet_names)}")
    print(f"  sheet names: {excel.sheet_names}")
    for name in excel.sheet_names:
        # header=None so the returned DataFrame preserves the spreadsheet's
        # literal cell values without trying to infer a header row — we
        # want to see the preamble too.
        df = pd.read_excel(excel, sheet_name=name, header=None)
        _dump_sheet(str(name), df)


def main() -> int:
    try:
        path = _download(_SEIFA_2016_URL, _LOCAL_PATH)
    except requests.HTTPError as e:
        sys.stderr.write(
            f"[fail]  Download failed: {e}\n"
            "        ABS may have moved the file. Open "
            "https://www.abs.gov.au/AUSSTATS/abs@.nsf/DetailsPage/"
            "2033.0.55.0012016?OpenDocument in a browser and copy the "
            "SA2 'Indexes' download link, then update _SEIFA_2016_URL.\n"
        )
        return 1
    except requests.RequestException as e:
        sys.stderr.write(f"[fail]  Network error: {e}\n")
        return 1
    try:
        _dump_workbook(path)
    except Exception as e:  # noqa: BLE001 — pandas/xlrd raise many shapes
        sys.stderr.write(f"[fail]  Could not open {path}: {type(e).__name__}: {e}\n")
        return 1
    print("\n[done]  Paste this output back to the maintainer / agent driving Phase F.3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
