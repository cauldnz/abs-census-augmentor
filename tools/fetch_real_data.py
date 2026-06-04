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
    from census_augment.data_sources.lga_boundaries import LgaBoundariesDataSource
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
        "--skip-lga",
        action="store_true",
        help=(
            "Skip the LGA boundary download (~40 MB). The LGA boundary "
            "is only needed by datasets that downscale from LGA-keyed "
            "sources via census_augment.correspondence (added v2.2.0). "
            "Default: include."
        ),
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
    p.add_argument(
        "--edition",
        type=int,
        choices=[1, 2, 3],
        default=3,
        help=(
            "ASGS boundary edition to fetch. 3 (default) = current "
            "(year 2021, GDA2020). 2 = historical (year 2016, GDA94); "
            "fetches the SA2 boundary file and the GCP 2016 DataPack. "
            "1 = historical (year 2011, GDA94); fetches the SA2 boundary "
            "file only — the 2011 GCP/BCP DataPack lives behind ABS's "
            "censusdata.abs.gov.au login and isn't auto-fetchable. "
            "MB Edition 1/2 correspondence is still deferred — both "
            "ship as 8 per-state shapefiles requiring concat logic."
        ),
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    data_dir = default_data_dir()
    # Edition 1 → year=2011/GDA94 (handled inline via edition_1_spec since
    # CensusConfig.year is Literal[2016, 2021] — cross-sectional 2011 isn't
    # a supported run mode; the boundary fetch is needed by temporal-mode
    # SEIFA 2011 lookups via per-edition spatial-index hand-off).
    # Edition 2 → year=2016/GDA94; Edition 3 → year=2021/GDA2020 (default).
    if args.edition == 1:
        # Construct a stand-in CensusConfig for the BoundariesDataSource's
        # required argument, then inject the real Edition 1 spec.
        from census_augment.data_sources._edition import edition_1_spec

        census = CensusConfig(year=2016, asgs_edition=2, datum="GDA94")
        boundary_year = "2011"
        edition_spec_override = edition_1_spec()
    elif args.edition == 2:
        census = CensusConfig(year=2016, asgs_edition=2, datum="GDA94")
        boundary_year = "2016"
        edition_spec_override = None
    else:
        census = CensusConfig()  # spec defaults: SA2, 2021, GCP, AUS, short-header, GDA2020
        boundary_year = "2021"
        edition_spec_override = None
    print(f"Cache root: {data_dir}")
    if args.edition == 1:
        print("ASGS edition: 1 (year=2011, datum=GDA94 — boundary-only fetch)")
    else:
        print(f"ASGS edition: {census.asgs_edition} (year={census.year}, datum={census.datum})")
    print("(Override via CENSUS_AUGMENT_DATA_DIR env var.)\n")

    print("=== Boundary ===")
    boundaries_kwargs: dict[str, object] = {
        "census": census,
        "base_url": DEFAULT_BOUNDARIES_URL,
        "root": data_dir / "boundaries" / boundary_year,
    }
    if edition_spec_override is not None:
        boundaries_kwargs["edition_spec"] = edition_spec_override
    boundaries = BoundariesDataSource(**boundaries_kwargs)  # type: ignore[arg-type]
    print(f"  URL:  {boundaries.url}")
    shp = boundaries.fetch(refresh=args.refresh)
    print(f"  shp:  {shp}")

    if args.edition == 1:
        # Edition 1 DataPack lives behind ABS login. Edition 1 MB
        # correspondence is per-state (same deferral as Edition 2). The
        # boundary above is enough for SEIFA 2011 temporal-mode lookups.
        print(
            "\nEdition 1 fetch complete (SA2 boundary only). "
            "GCP 2011 / BCP 2011 are not auto-fetchable — they require "
            "manual download from https://www.censusdata.abs.gov.au/datapacks "
            "and live outside the augmentor's auto-fetch contract.\n"
            "Run `uv run python tools/verify_real_parsers.py` to confirm "
            "the parser handles the live Edition 1 boundary file."
        )
        return 0

    print("=== DataPack ===")
    datapacks = DataPacksDataSource(
        census=census,
        base_url=DEFAULT_DATAPACKS_URL,
        root=data_dir / "census" / str(census.year),
    )
    print(f"  URL:    {datapacks.url}")
    extract = datapacks.fetch(refresh=args.refresh)
    print(f"  files:  {extract}")

    if args.edition == 2:
        # Edition 2 MB correspondence is still deferred (see CHANGELOG /
        # spec-temporal.md §6) — ABS publishes Mesh Block shapefiles
        # per state/territory rather than as a single national ZIP, and
        # the §7.3 fast-path concat across the 8 state files isn't
        # wired up yet. The Edition 2 boundary + DataPack above are
        # enough to exercise the parser + verify_real_parsers.py probe
        # introduced by F.4.
        print(
            "\nEdition 2 fetch complete (SA2 boundary + GCP 2016 DataPack). "
            "MB correspondence for 2016 is still deferred to a follow-up.\n"
            "Run `uv run python tools/verify_real_parsers.py` to confirm "
            "the parser handles the live Edition 2 files."
        )
        return 0

    print("=== Mesh Block correspondence ===")
    mb_ds = MbCorrespondenceDataSource(
        year=census.year,
        datum=census.datum,
        base_url=DEFAULT_BOUNDARIES_URL,
        root=data_dir / "mb" / str(census.year),
    )
    print(f"  URL:    {mb_ds.url}")
    mb_shp = mb_ds.fetch(refresh=args.refresh)
    print(f"  shp:    {mb_shp}")

    # LGA boundary (v2.2.0+) — needed by datasets that downscale from
    # LGA-keyed sources via census_augment.correspondence. Annual
    # release cadence; we fetch the latest (currently 2025). Opt out
    # with --skip-lga if you're not using any LGA-keyed dataset.
    if args.skip_lga:
        print("=== LGA boundary ===")
        print("  (skipped via --skip-lga)")
    else:
        print("=== LGA boundary (2025) ===")
        lga_ds = LgaBoundariesDataSource(
            year="latest",
            root=data_dir / "boundaries" / "lga" / "2025",
        )
        print(f"  URL:    {lga_ds.url}")
        lga_shp = lga_ds.fetch(refresh=args.refresh)
        print(f"  shp:    {lga_shp}")

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
