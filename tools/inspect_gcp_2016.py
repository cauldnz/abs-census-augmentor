"""One-off GCP 2016 DataPack schema probe (Phase F.4 unblock).

The 2016 GCP DataPack ZIP isn't reachable via a static URL the way the
2021 one is — ABS's modern ``find-census-data/datapacks`` page only
exposes 2021, and the legacy 2016 hosting requires either an interactive
form submission with session state or a manual download via ABS Information
Consultancy. Until / unless we find a direct URL, this script takes a
path to a locally-downloaded ZIP and dumps everything the
``census_augment.data_sources.datapacks`` parser will need to know to
support a second release alongside GCP 2021:

- The ZIP's internal directory layout.
- The descriptor (``Metadata_*.xlsx``) candidate file(s).
- The descriptor xlsx's sheet names + the rows that probably hold the
  column-code metadata.
- A representative table CSV's header + 3 sample rows (so we can spot
  the SA2 code column name; expect ``SA2_MAINCODE_2016`` or similar).
- The full list of ``G##*.csv`` files (2016's table inventory).

Usage:

    uv run python tools/inspect_gcp_2016.py path/to/2016_GCP_SA2_*.zip

How to obtain the ZIP:

1. Try the modern landing page first
   (https://www.abs.gov.au/census/find-census-data/datapacks). As of
   2026-05 it only offers 2021; 2016 has been migrated away.
2. If the modern page doesn't expose 2016, check the historical archive
   (https://www.abs.gov.au/census/find-census-data/historical) — that
   page links to where the 2016 DataPacks live now.
3. Worst case: ABS Information Consultancy can provide the ZIP on
   request (per the ABS contact page).

Once we have the URL, the script will get a ``_download()`` helper like
``tools/inspect_seifa_2016.py``.

Run via ``uv run python tools/inspect_gcp_2016.py <path>``. Paste the
stdout back so the Phase F.4 fetcher can be built off the captured
schema, per CLAUDE.md's "Real Data First" rule.

Not part of the pytest suite; this is a one-off discovery probe (same
class as ``tools/fetch_real_data.py`` and ``verify_real_parsers.py``).
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

#: How many rows to print per descriptor sheet's preamble dump.
_PREAMBLE_ROWS = 15

#: How many sample data rows to print per representative table CSV.
_SAMPLE_DATA_ROWS = 3

#: Patterns matching the descriptor / metadata xlsx — 2021 uses
#: ``Metadata_2021_GCP_DataPack.xlsx``. 2016 may rename or restructure;
#: we look for anything with both "Metadata" and "GCP".
_DESCRIPTOR_RE = re.compile(r"metadata.*gcp.*datapack.*\.xlsx$", re.IGNORECASE)

#: Pattern for a single-table CSV (G01, G02A, G09B, ...). The 2021 file
#: names look like ``2021Census_G01_AUS_SA2.csv``.
_TABLE_CSV_RE = re.compile(r".*G\d+[A-Z]?.*\.csv$", re.IGNORECASE)


def _row_repr(values: list[Any]) -> str:
    """One-line repr of a row, truncated per-cell to keep paste-back tidy."""
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


def _dump_zip_layout(zf: zipfile.ZipFile) -> None:
    """Print the ZIP's internal directory tree."""
    print("\n=== ZIP internal layout ===")
    print(f"  total entries: {len(zf.namelist())}")

    # Group entries by top-level directory for readability — the 2021
    # DataPack uses ``2021 Census GCP All Geographies for AUST/SA2/``
    # plus a sibling ``Metadata/`` directory; 2016 may differ.
    by_top: dict[str, int] = {}
    for name in zf.namelist():
        top = name.split("/", 1)[0] if "/" in name else "<root>"
        by_top[top] = by_top.get(top, 0) + 1
    for top, count in sorted(by_top.items()):
        print(f"    {top}/  ({count} entries)")

    # Surface every CSV that looks like a table file, plus every xlsx.
    print("\n  --- xlsx files ---")
    xlsx_names = sorted(n for n in zf.namelist() if n.lower().endswith(".xlsx"))
    if not xlsx_names:
        print("    (none)")
    for name in xlsx_names:
        info = zf.getinfo(name)
        print(f"    {name}  ({info.file_size:,} bytes)")

    print("\n  --- table CSVs (first 20) ---")
    table_names = sorted(n for n in zf.namelist() if _TABLE_CSV_RE.match(Path(n).name))
    if not table_names:
        print("    (none — adjust _TABLE_CSV_RE if 2016 uses a different convention)")
    for name in table_names[:20]:
        info = zf.getinfo(name)
        print(f"    {name}  ({info.file_size:,} bytes)")
    if len(table_names) > 20:
        print(f"    ... and {len(table_names) - 20} more")
    print(f"\n  table-CSV total count: {len(table_names)}")


def _dump_descriptor(zf: zipfile.ZipFile) -> None:
    """Find and dump the metadata xlsx's sheet structure."""
    print("\n=== Descriptor xlsx (metadata) ===")
    matches = [n for n in zf.namelist() if _DESCRIPTOR_RE.search(n)]
    if not matches:
        print("  (no Metadata_*GCP*DataPack*.xlsx file found)")
        print("  Check the xlsx list above — 2016 may use a different naming convention.")
        return
    descriptor_name = matches[0]
    print(f"  descriptor: {descriptor_name}")
    if len(matches) > 1:
        print(f"  (also matched: {matches[1:]})")

    with zf.open(descriptor_name) as f:
        buf = io.BytesIO(f.read())
    try:
        excel = pd.ExcelFile(buf, engine="openpyxl")
    except Exception as e:  # noqa: BLE001
        print(f"  [fail] Could not open descriptor xlsx: {type(e).__name__}: {e}")
        return
    print(f"  sheet count: {len(excel.sheet_names)}")
    print(f"  sheet names: {excel.sheet_names}")

    for name in excel.sheet_names:
        df = pd.read_excel(excel, sheet_name=name, header=None)
        print(f"\n  --- sheet {name!r}: first {_PREAMBLE_ROWS} rows ---")
        rows_to_show = min(_PREAMBLE_ROWS, len(df))
        for i in range(rows_to_show):
            row = df.iloc[i].tolist()
            if all(v is None or (isinstance(v, float) and pd.isna(v)) for v in row):
                print(f"    [{i + 1:>3}] <empty>")
                continue
            print(f"    [{i + 1:>3}] {_row_repr(row)}")


def _dump_sample_csv(zf: zipfile.ZipFile) -> None:
    """Pick one G01-shaped CSV and dump its header + 3 sample rows.

    Surfaces the SA2 code column name — this is the join key the
    GCP 2016 fetcher will need. Expect ``SA2_MAINCODE_2016`` or
    ``SA2_MAIN16`` (matching the boundary file convention) but ABS
    has varied the column name across releases, so the parser needs
    to detect it.
    """
    print("\n=== Sample table CSV (G01-shaped) ===")
    # Prefer G01 — total population, the most stable table across
    # Census years. Fall back to first matching table if no G01.
    candidates = [n for n in zf.namelist() if _TABLE_CSV_RE.match(Path(n).name)]
    if not candidates:
        print("  (no table CSVs found)")
        return
    g01 = next(
        (n for n in candidates if re.search(r"G01[^0-9]", Path(n).name, re.IGNORECASE)),
        candidates[0],
    )
    print(f"  picked: {g01}")
    with zf.open(g01) as f:
        # ABS DataPack CSVs are UTF-8 (or sometimes CP1252 in older
        # releases). Try UTF-8 first; on decode failure, fall back.
        raw = f.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = raw.decode(encoding)
            print(f"  encoding: {encoding}")
            break
        except UnicodeDecodeError:
            continue
    else:
        print("  (could not decode CSV as any of utf-8-sig / utf-8 / cp1252)")
        return
    lines = text.splitlines()
    print(f"  line count: {len(lines)}")
    print(f"  --- header + {_SAMPLE_DATA_ROWS} sample rows ---")
    for i, line in enumerate(lines[: 1 + _SAMPLE_DATA_ROWS]):
        # CSV-aware splitting — but quick-and-dirty here; ABS DataPacks
        # don't quote fields, so a plain split on ',' is good enough.
        cells = line.split(",")
        print(f"    [{i:>3}] ({len(cells)} cols) first 5: {cells[:5]}")
        if i == 0:
            # Find the SA2 code column for the maintainer's convenience.
            sa2_cols = [c for c in cells if "SA2" in c.upper() or "MAIN" in c.upper()]
            print(f"          SA2-looking columns: {sa2_cols}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "zip_path",
        type=Path,
        help="Path to a locally-downloaded 2016 GCP DataPack ZIP "
        "(e.g. data/gcp_2016_raw/2016_GCP_SA2_*.zip).",
    )
    args = p.parse_args()

    if not args.zip_path.exists():
        sys.stderr.write(f"[fail]  No such file: {args.zip_path}\n")
        return 1
    if not args.zip_path.is_file():
        sys.stderr.write(f"[fail]  Not a file: {args.zip_path}\n")
        return 1

    print(f"[open]  {args.zip_path}  ({args.zip_path.stat().st_size:,} bytes)")
    try:
        with zipfile.ZipFile(args.zip_path) as zf:
            _dump_zip_layout(zf)
            _dump_descriptor(zf)
            _dump_sample_csv(zf)
    except zipfile.BadZipFile:
        sys.stderr.write(f"[fail]  {args.zip_path} is not a valid ZIP archive.\n")
        return 1
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[fail]  Unexpected error: {type(e).__name__}: {e}\n")
        return 1

    print("\n[done]  Paste this output back to the maintainer / agent driving Phase F.4.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
