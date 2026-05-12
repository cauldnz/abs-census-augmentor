"""Download real ABS data for offline parser verification.

Usage:
    python tools/fetch_real_data.py [--refresh] [--skip-nominatim]

Files are written to the user-cache root resolved by
:func:`census_augment.paths.default_data_dir` — by default a platform
user-cache directory (e.g. ``~/.cache/census-augment/data/`` on Linux),
overridable via the ``CENSUS_AUGMENT_DATA_DIR`` env var. See spec §9.

The same cache is shared with the library (``Pipeline.from_config``) and
the CLI, so this script also primes them — no duplicate downloads.

Idempotent: skips downloads when local files already exist; ``--refresh``
forces re-download. Uses the actual ``BoundariesDataSource`` /
``DataPacksDataSource`` classes, so running this exercises the production
download path.

Run ``tools/verify_real_parsers.py`` afterwards to validate.

Not part of the pytest suite (see ``tools/README.md`` for rationale).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from census_augment.config import (
        DEFAULT_BOUNDARIES_URL,
        DEFAULT_DATAPACKS_URL,
        CensusConfig,
    )
    from census_augment.data_sources.boundaries import BoundariesDataSource
    from census_augment.data_sources.datapacks import DataPacksDataSource
    from census_augment.data_sources.gnaf import (
        DEFAULT_GNAF_S3_BASE_URL,
        GnafDataSource,
    )
    from census_augment.data_sources.mb_correspondence import MbCorrespondenceDataSource
    from census_augment.paths import default_data_dir
except ModuleNotFoundError as e:
    sys.stderr.write(
        "ERROR: census_augment is not importable from the active Python "
        f"({sys.executable}).\n\n"
        f"Underlying error: {e}\n\n"
        "Most likely cause: you ran `python tools/...` instead of\n"
        "`uv run python tools/...`. Plain `python` uses your system\n"
        "PATH, not the project's .venv where the package lives.\n\n"
        "Fix:\n"
        "    uv run python tools/fetch_real_data.py\n\n"
        "Or activate the venv first:\n"
        "    Windows : .venv\\Scripts\\activate\n"
        "    macOS/Linux: source .venv/bin/activate\n"
        "    Then    : python tools/fetch_real_data.py\n"
    )
    sys.exit(2)

NOMINATIM_USER_AGENT = "census-augment-fetch/0.1 (real-data-verification; https://example.com)"
NOMINATIM_SAMPLE_ADDRESS = "1 Macquarie Street, Sydney NSW"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: G-NAF attribution string per Geoscape's Open G-NAF EULA (spec §19.5).
GNAF_ATTRIBUTION = (
    "Incorporates or developed using G-NAF © Geoscape Australia licensed "
    "by the Commonwealth of Australia under the Open Geo-coded National "
    "Address File (G-NAF) End User Licence Agreement."
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", action="store_true", help="Force re-download of cached files")
    p.add_argument(
        "--skip-nominatim",
        action="store_true",
        help="Skip the Nominatim sample query",
    )
    p.add_argument(
        "--skip-gnaf",
        action="store_true",
        help=(
            "Skip the G-NAF download (~10 GB). Useful when iterating on "
            "the boundaries/DataPack code paths."
        ),
    )
    p.add_argument(
        "--gnaf-release",
        default="latest",
        help=(
            "G-NAF release to fetch (YYYYMM, e.g. '202602'). Defaults to "
            "'latest' — picks whichever release is highest in the "
            f"configured S3 bucket ({DEFAULT_GNAF_S3_BASE_URL})."
        ),
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    data_dir = default_data_dir()
    census = CensusConfig()  # spec defaults: SA2, 2021, GCP, AUS, short-header, GDA2020
    print(f"Cache root: {data_dir}")
    print("(Override via CENSUS_AUGMENT_DATA_DIR env var.)\n")

    print("=== Boundary ===")
    boundaries = BoundariesDataSource(
        census=census,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "boundaries",
    )
    print(f"  URL:  {boundaries.url}")
    shp = boundaries.fetch(refresh=args.refresh)
    print(f"  shp:  {shp}")

    print("=== DataPack ===")
    datapacks = DataPacksDataSource(
        census=census,
        base_url=DEFAULT_DATAPACKS_URL,
        root=data_dir / "census",
    )
    print(f"  URL:    {datapacks.url}")
    extract = datapacks.fetch(refresh=args.refresh)
    print(f"  files:  {extract}")

    print("=== Mesh Block correspondence ===")
    mb_ds = MbCorrespondenceDataSource(
        year=census.year,
        datum=census.datum,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "mb",
    )
    print(f"  URL:    {mb_ds.url}")
    mb_shp = mb_ds.fetch(refresh=args.refresh)
    print(f"  shp:    {mb_shp}")

    if not args.skip_nominatim:
        print("=== Nominatim sample ===")
        sample_path = data_dir / "nominatim_sample.json"
        if sample_path.exists() and not args.refresh:
            print(f"  (cached: {sample_path})")
        else:
            r = requests.get(
                NOMINATIM_URL,
                params={
                    "q": NOMINATIM_SAMPLE_ADDRESS,
                    "format": "json",
                    "limit": "1",
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=30,
            )
            r.raise_for_status()
            data_dir.mkdir(parents=True, exist_ok=True)
            sample_path.write_text(
                json.dumps(
                    {
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "address": NOMINATIM_SAMPLE_ADDRESS,
                        "user_agent": NOMINATIM_USER_AGENT,
                        "results": r.json(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"  saved:  {sample_path}")

    print("=== G-NAF ===")
    print("  Attribution:")
    for line in GNAF_ATTRIBUTION.split(". "):
        print(f"    {line}")
    if args.skip_gnaf:
        print("  (skipped via --skip-gnaf)")
    else:
        gnaf_ds = GnafDataSource(
            release=args.gnaf_release,
            data_dir=data_dir,
        )
        print(f"  S3:    {gnaf_ds.s3_base_url}")
        gnaf_path = gnaf_ds.fetch(refresh=args.refresh)
        print(f"  cache: {gnaf_path}")
        print(f"  release: {gnaf_ds.resolved_release}")

    print()
    print("Done. Run `python tools/verify_real_parsers.py` to validate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
