#!/usr/bin/env python3
"""Probe a real external artifact and dump its actual schema.

This is the "Real Data First" workhorse: point it at the real thing -- a URL or
a local path -- and it prints the shape details that silently break parsers
(column names, JSON key paths and leaf types, CSV encoding + header, Excel sheet
names verbatim, ZIP layout, Parquet/shapefile columns). Read the output, then
build your production code AND your test fixtures off what it actually shows --
never off documentation, naming conventions, or intuition.

This file is a TEMPLATE. The dispatch + per-type dumpers are project-agnostic
and useful as-is. When you have a recurring set of upstream sources, copy this
into your project's ``tools/`` and add a small table of the URLs you fetch so a
teammate can re-run the whole probe with no arguments.

Usage:
    python probe_artifact.py <url-or-path> [options]

    python probe_artifact.py https://example.org/data/export.csv
    python probe_artifact.py ./sample.xlsx
    python probe_artifact.py ./bundle.zip --rows 25
    python probe_artifact.py https://api.example.org/v1/items --type json
    python probe_artifact.py ./data.parquet

Options:
    --type {auto,json,csv,tsv,excel,zip,parquet,shapefile,text,bytes}
                    Force a parser. Default 'auto' = sniff from suffix + content.
    --rows N        Sample/preamble rows to print per table or sheet (default 5;
                    Excel preamble uses max(N, 20) so the real header is visible).
    --out PATH      Where to cache a downloaded URL (default: a temp file). The
                    cached file is kept so you can re-inspect it without re-fetching.
    --refresh       Re-download even if the cache file already exists.
    --max-bytes N   Cap how many bytes are read for text sniffing (default 2 MiB).

Dependency policy (deliberately light, so the probe runs almost anywhere):
    - Hard deps: Python 3.9+ stdlib, plus ``requests`` for http(s) URLs.
    - ``pandas`` (+ an Excel engine: ``python-calamine`` preferred, or
      ``openpyxl``/``xlrd``) lights up the Excel dumper.
    - ``pyarrow`` lights up the Parquet dumper.
    - ``pyogrio`` or ``fiona`` (or ``geopandas``) lights up the shapefile dumper.
    Missing an optional dep degrades to a clear "install X to probe this type"
    message rather than a traceback.

Output is intentionally pure ASCII. Probing tools get run in all sorts of
terminals (including Windows consoles defaulting to cp1252), and a probe that
mojibakes its own output while hunting an encoding bug would be a poor joke.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Fetch / cache
# ---------------------------------------------------------------------------

#: Encodings tried, in order, when decoding text. utf-8-sig first so a BOM is
#: stripped cleanly; cp1252 / latin-1 last to catch the legacy-Windows files
#: that are the classic mojibake source (see failure-modes.md sec 3).
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

_DEFAULT_MAX_BYTES = 2 * 1024 * 1024


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _cache_path_for(url: str, explicit_out: str | None) -> Path:
    if explicit_out:
        return Path(explicit_out)
    # Deterministic temp name from the URL so repeat runs reuse the download.
    import hashlib
    import tempfile

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(url.split("?", 1)[0]).suffix
    return Path(tempfile.gettempdir()) / f"probe_{digest}{suffix}"


def _download(url: str, dest: Path, refresh: bool) -> Path:
    if dest.exists() and not refresh:
        print(f"[cache] reusing {dest} ({dest.stat().st_size:,} bytes)")
        print("        (pass --refresh to force a re-download)")
        return dest
    try:
        import requests
    except ImportError:
        sys.exit(
            "[fail] 'requests' is required to fetch http(s) URLs.\n"
            "       Install it (`pip install requests`) or download the file "
            "manually and pass its local path instead."
        )
    print(f"[fetch] {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=300, allow_redirects=True) as r:
        ctype = r.headers.get("Content-Type", "")
        print(f"        HTTP {r.status_code}; Content-Type: {ctype!r}")
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        tmp.replace(dest)
    print(f"[save]  {dest} ({dest.stat().st_size:,} bytes)")
    return dest


# ---------------------------------------------------------------------------
# Type sniffing
# ---------------------------------------------------------------------------

#: Magic-byte signatures for content sniffing when the suffix is unhelpful.
_MAGIC = {
    b"PK\x03\x04": "zip",  # also xlsx/docx/ods, handled before zip fallthrough
    b"PAR1": "parquet",
    b"\x89HDF": "hdf5",
}

_SUFFIX_TYPE = {
    ".json": "json",
    ".geojson": "shapefile",
    ".csv": "csv",
    ".tsv": "tsv",
    ".txt": "text",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xls": "excel",
    ".zip": "zip",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".shp": "shapefile",
    ".gpkg": "shapefile",
}


def _sniff_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _SUFFIX_TYPE:
        # xlsx is a zip under the hood; trust the suffix over the PK magic.
        return _SUFFIX_TYPE[suffix]
    head = path.read_bytes()[:16]
    for magic, kind in _MAGIC.items():
        if head.startswith(magic):
            return kind
    # Text-ish? Try a decode and look for JSON / delimited structure.
    for enc in _ENCODINGS:
        try:
            sample = head.decode(enc)
        except UnicodeDecodeError:
            continue
        stripped = sample.lstrip()
        if stripped[:1] in ("{", "["):
            return "json"
        return "text"
    return "bytes"


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _cell(v: Any) -> str:
    """One-cell repr that keeps a pasted-back row tidy. '.' marks empty/NaN."""
    if v is None:
        return "."
    try:
        # Detect NaN without importing pandas/math at call sites.
        if v != v:  # noqa: PLR0124 - NaN is the only value != itself
            return "."
    except Exception:  # noqa: BLE001
        pass
    s = str(v)
    return repr(_truncate(s)) if isinstance(v, str) else _truncate(s)


def _row(values: list[Any]) -> str:
    return " | ".join(_cell(v) for v in values)


def _decode_best(raw: bytes) -> tuple[str | None, str]:
    """Return (encoding_that_worked_or_None, decoded_text). Reports the chain."""
    for enc in _ENCODINGS:
        try:
            return enc, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None, raw.decode("latin-1", errors="replace")


def _has_non_ascii(raw: bytes) -> bool:
    return any(b > 0x7F for b in raw)


# ---------------------------------------------------------------------------
# Per-type dumpers
# ---------------------------------------------------------------------------


def _walk_json(node: Any, prefix: str, lines: list[str], depth: int, max_depth: int) -> None:
    """Describe a JSON node's structure + leaf types (not every value)."""
    if depth > max_depth:
        lines.append(f"  {prefix}: ... (max depth)")
        return
    if isinstance(node, dict):
        lines.append(f"  {prefix or '<root>'}: object ({len(node)} keys)")
        for k, v in list(node.items())[:50]:
            _walk_json(v, f"{prefix}.{k}" if prefix else k, lines, depth + 1, max_depth)
        if len(node) > 50:
            lines.append(f"  {prefix}: ... (+{len(node) - 50} more keys)")
    elif isinstance(node, list):
        lines.append(f"  {prefix or '<root>'}: array (len {len(node)})")
        if node:
            # Describe the first element as the element schema; flag if mixed.
            types = {type(x).__name__ for x in node[:50]}
            if len(types) > 1:
                lines.append(f"  {prefix}[*]: MIXED element types {sorted(types)}")
            _walk_json(node[0], f"{prefix}[0]", lines, depth + 1, max_depth)
    else:
        tname = type(node).__name__
        sample = _truncate(repr(node), 40)
        lines.append(f"  {prefix}: {tname} = {sample}")


def dump_json(raw: bytes, rows: int) -> None:
    enc, text = _decode_best(raw)
    print(f"  encoding: {enc or '(none cleanly; latin-1 with replacement)'}")
    print(f"  non-ASCII bytes present: {_has_non_ascii(raw)}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [fail] not valid JSON: {e}")
        print("  --- first 500 chars ---")
        print("  " + _truncate(text.replace("\n", " "), 500))
        return
    print("  --- structure (keys + leaf types, NOT full values) ---")
    print("  WATCH: are numbers really numbers, or strings? is the payload")
    print("         wrapped in an envelope (data/results/items)? optional keys?")
    lines: list[str] = []
    _walk_json(data, "", lines, 0, max_depth=6)
    for ln in lines[:200]:
        print(ln)
    if len(lines) > 200:
        print(f"  ... ({len(lines) - 200} more lines; raise max_depth/inspect manually)")


def dump_delimited(raw: bytes, rows: int, forced_delim: str | None) -> None:
    enc, text = _decode_best(raw)
    print(f"  encoding: {enc or '(none cleanly; latin-1 with replacement)'}")
    print(f"  non-ASCII bytes present: {_has_non_ascii(raw)}")
    if enc not in ("utf-8", "utf-8-sig"):
        print(f"  WATCH: this is NOT utf-8. Hard-code encoding={enc!r} in the reader,")
        print("         and put a non-ASCII value in your fixture to test the decode.")
    lines = text.splitlines()
    print(f"  line count (in sampled bytes): {len(lines)}")
    if not lines:
        print("  (no lines)")
        return
    # Delimiter: forced > sniffed > comma.
    delim = forced_delim
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|").delimiter
        except csv.Error:
            delim = ","
    print(f"  delimiter: {delim!r}")
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    parsed = []
    for i, rec in enumerate(reader):
        parsed.append(rec)
        if i >= rows:
            break
    if not parsed:
        return
    header = parsed[0]
    print(f"  column count: {len(header)}")
    print("  --- header ---")
    for idx, col in enumerate(header):
        print(f"    [{idx:>3}] {col!r}")
    print(f"  --- first {min(rows, len(parsed) - 1)} data rows ---")
    for r in parsed[1 : 1 + rows]:
        print(f"    {_row(r)}")
    print("  WATCH: leading-zero codes, thousands separators, sentinel rows at the")
    print("         tail, trailing footnote rows. Inspect the real tail too.")


def dump_excel(path: Path, rows: int) -> None:
    try:
        import pandas as pd
    except ImportError:
        print("  [skip] pandas not installed. `pip install pandas python-calamine`")
        print("         (calamine reads .xls AND .xlsx and ships no console scripts).")
        return
    preamble = max(rows, 20)
    engines = ["calamine", "openpyxl"] if path.suffix.lower() != ".xls" else ["calamine", "xlrd"]
    excel = None
    last_err: Exception | None = None
    for engine in engines:
        try:
            excel = pd.ExcelFile(path, engine=engine)
            print(f"  engine: {engine}")
            break
        except Exception as e:  # noqa: BLE001 - engine missing or rejects file
            last_err = e
    if excel is None:
        print(f"  [fail] no Excel engine could open this. Tried {engines}.")
        print(f"         `pip install python-calamine`. Last error: {last_err}")
        return
    print(f"  sheet count: {len(excel.sheet_names)}")
    print(f"  sheet names (VERBATIM -- mind spaces vs underscores): {excel.sheet_names}")
    for name in excel.sheet_names:
        df = pd.read_excel(excel, sheet_name=name, header=None)
        print(f"\n  --- sheet {name!r}: rows={len(df)} cols={len(df.columns)} ---")
        print(f"  (first {min(preamble, len(df))} rows; find where the real header sits)")
        for i in range(min(preamble, len(df))):
            vals = df.iloc[i].tolist()
            if all((v is None or (isinstance(v, float) and v != v)) for v in vals):
                print(f"    [{i + 1:>3}] <empty>")
            else:
                print(f"    [{i + 1:>3}] {_row(vals)}")


def dump_zip(path: Path, rows: int) -> None:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        print(f"  total entries: {len(names)}")
        by_top: dict[str, int] = {}
        for n in names:
            top = n.split("/", 1)[0] if "/" in n else "<root>"
            by_top[top] = by_top.get(top, 0) + 1
        print("  --- top-level layout (mind any wrapper directory) ---")
        for top, count in sorted(by_top.items()):
            print(f"    {top}/  ({count} entries)")
        print("  --- entries (first 40, with sizes) ---")
        for info in zf.infolist()[:40]:
            kind = "dir " if info.is_dir() else "file"
            print(f"    {kind} {info.file_size:>12,}  {info.filename}")
        if len(names) > 40:
            print(f"    ... (+{len(names) - 40} more entries)")
        # Peek inside a representative data member so the nested schema shows.
        member = next(
            (n for n in names if n.lower().endswith((".csv", ".tsv", ".json"))),
            None,
        )
        if member:
            print(f"\n  --- peek inside {member!r} ---")
            raw = zf.read(member)[:_DEFAULT_MAX_BYTES]
            if member.lower().endswith(".json"):
                dump_json(raw, rows)
            else:
                delim = "\t" if member.lower().endswith(".tsv") else None
                dump_delimited(raw, rows, delim)
        else:
            print("\n  (no .csv/.tsv/.json member to peek into; inspect manually)")


def dump_parquet(path: Path, rows: int) -> None:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("  [skip] pyarrow not installed. `pip install pyarrow`")
        return
    pf = pq.ParquetFile(path)
    schema = pf.schema_arrow
    print(f"  rows: {pf.metadata.num_rows:,}   row groups: {pf.metadata.num_row_groups}")
    print(f"  column count: {len(schema)}")
    print("  --- columns (name : arrow dtype) ---")
    for field in schema:
        print(f"    {field.name!r}: {field.type}")
    print("  WATCH: physical types vs what you expect (int vs string codes, ")
    print("         timestamp units/timezones, dictionary-encoded columns).")


def dump_shapefile(path: Path, rows: int) -> None:
    # pyogrio is the fast modern reader; fiona is the classic; geopandas wraps
    # either. Try them in that order.
    fields = crs = geom = None
    try:
        import pyogrio

        info = pyogrio.read_info(path)
        fields = list(info.get("fields", []))
        crs = info.get("crs")
        geom = info.get("geometry_type")
        print("  reader: pyogrio")
    except ImportError:
        try:
            import fiona

            with fiona.open(path) as src:
                fields = list(src.schema["properties"].keys())
                crs = str(src.crs)
                geom = src.schema.get("geometry")
            print("  reader: fiona")
        except ImportError:
            print("  [skip] no shapefile reader. `pip install pyogrio` (or fiona).")
            return
    print(f"  CRS: {crs}")
    print(f"  geometry type: {geom}")
    print(f"  attribute column count: {len(fields or [])}")
    print("  --- attribute (field) columns ---")
    for f in fields or []:
        print(f"    {f!r}")
    print("  WATCH: the join-key field name + its width (codes vary by vintage),")
    print("         the CRS/datum (reprojection bugs), and sentinel/empty geometry.")


def dump_text(raw: bytes, rows: int) -> None:
    enc, text = _decode_best(raw)
    print(f"  encoding: {enc or '(none cleanly; latin-1 with replacement)'}")
    print(f"  non-ASCII bytes present: {_has_non_ascii(raw)}")
    lines = text.splitlines()
    print(f"  line count (in sampled bytes): {len(lines)}")
    print(f"  --- first {min(rows + 1, len(lines))} lines ---")
    for ln in lines[: rows + 1]:
        print(f"    {_truncate(ln, 120)}")


def dump_bytes(path: Path) -> None:
    raw = path.read_bytes()[:64]
    print(f"  first {len(raw)} bytes (hex): {raw.hex(' ')}")
    print("  (unrecognised binary; force --type or inspect with a domain tool)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Probe a real external artifact and dump its actual schema.",
    )
    p.add_argument("source", help="URL (http/https) or local path to the artifact")
    p.add_argument(
        "--type",
        default="auto",
        choices=[
            "auto", "json", "csv", "tsv", "excel",
            "zip", "parquet", "shapefile", "text", "bytes",
        ],
    )
    p.add_argument("--rows", type=int, default=5)
    p.add_argument("--out", default=None)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--max-bytes", type=int, default=_DEFAULT_MAX_BYTES)
    args = p.parse_args()

    if _is_url(args.source):
        path = _download(args.source, _cache_path_for(args.source, args.out), args.refresh)
    else:
        path = Path(args.source)
        if not path.exists():
            sys.exit(f"[fail] no such file: {path}")

    kind = args.type if args.type != "auto" else _sniff_type(path)
    print(f"\n=== probing {path.name} as: {kind} ===")

    # For the byte-oriented text types, read a capped slice; the structured
    # readers (excel/zip/parquet/shapefile) open the file directly.
    if kind in ("json", "csv", "tsv", "text"):
        raw = path.read_bytes()[: args.max_bytes]

    if kind == "json":
        dump_json(raw, args.rows)
    elif kind in ("csv", "tsv"):
        dump_delimited(raw, args.rows, "\t" if kind == "tsv" else None)
    elif kind == "text":
        dump_text(raw, args.rows)
    elif kind == "excel":
        dump_excel(path, args.rows)
    elif kind == "zip":
        dump_zip(path, args.rows)
    elif kind == "parquet":
        dump_parquet(path, args.rows)
    elif kind == "shapefile":
        dump_shapefile(path, args.rows)
    else:
        dump_bytes(path)

    print("\n[done] Build production code AND fixtures off the schema above --")
    print("       copy the real names/types, do not retype from memory or docs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
