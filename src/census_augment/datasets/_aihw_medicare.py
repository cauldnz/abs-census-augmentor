"""AIHW Medicare-subsidised Mental Health services fetcher (spec §20,
dataset id ``aihw_mh_medicare``).

Fourth AIHW NMHSPF dataset. Captures Medicare-subsidised
mental-health-specific services — patients and services under the MBS —
at SA4 level, downscaled to SA2 via the boundary's ``SA4_CODE21``
attribute. Catalogue identifier ``AIHW_MBS``.

Real-data findings (live-probed 2026-06-05) — this dataset has the
fiddliest code/value formatting of the AIHW family, all confirmed
firsthand:

- The CSV is **cp1252**.
- SA4 codes are **hyphenated**: ``SA4-101`` (NOT ``SA4101`` like the
  other AIHW datasets). Strip ``^SA4-`` to match the boundary's bare
  3-digit ``SA4_CODE21``.
- ``ProviderType`` values contain **non-breaking spaces** (U+00A0),
  e.g. ``"All\xa0providers"``. Normalise NBSP → regular space before
  filtering to ``"All providers"`` (the headline; the file also splits
  by Psychiatrists / GPs / Clinical psychologists / etc.).
- Multi-FY file with **en-dash FY labels** (``2024–25``) — normalised +
  filtered to the requested release.
- Columns: ``FinancialYear, GeographicAreaType, GeographicAreaCode,
  phnname, ProviderType, Measure, Value``. Four measures:
  Patients / Services, each + a "rate per 1,000 population" twin.

The common machinery lives in :class:`AihwSa4Dataset`; this module
declares only the per-dataset schema specifics + the URL registry.
"""

from __future__ import annotations

from pathlib import Path

from ._aihw_sa4_base import AihwSa4Dataset

# AIHW getmedia URLs use opaque UUIDs per release.
_AIHW_MEDICARE_URLS_BY_RELEASE: dict[str, str] = {
    "2024-25": (
        "https://www.aihw.gov.au/getmedia/"
        "e733afb1-0cba-4998-be88-86fa9291e621/"
        "Medicare-mental-health-service-2024-25.zip"
    ),
}

_MEASURE_TO_COLUMN: dict[str, str] = {
    "Patients": "mh_medicare_patients_count",
    "Patient rate per 1,000 population": "mh_medicare_patient_rate_per_1000",
    "Services": "mh_medicare_services_count",
    "Service rate per 1,000 population": "mh_medicare_service_rate_per_1000",
}

_COUNT_COLUMNS: tuple[str, ...] = (
    "mh_medicare_patients_count",
    "mh_medicare_services_count",
)

# Headline provider-type. Real values carry non-breaking spaces
# (U+00A0); the base normalises NBSP -> space before comparing, so this
# plain-space literal matches.
_ALL_PROVIDERS = "All providers"


class AihwMhMedicareDataSource(AihwSa4Dataset):
    """Fetch + load AIHW NMHSPF Medicare-subsidised MH-services data."""

    _label = "MH Medicare"
    _cache_slug = "aihw-mh-medicare"
    _registry_const_name = "_AIHW_MEDICARE_URLS_BY_RELEASE"
    _url_registry = _AIHW_MEDICARE_URLS_BY_RELEASE
    _encoding = "cp1252"
    _member_substrings = ("phn", "sa4")
    _csv_missing_hint = "PHN+SA4 CSV (looked for *PHN*SA4*.csv)"
    _required_columns = frozenset(
        {
            "FinancialYear",
            "GeographicAreaType",
            "GeographicAreaCode",
            "ProviderType",
            "Measure",
            "Value",
        }
    )
    # ProviderType carries non-breaking spaces — normalise before filtering.
    _nbsp_strip_columns = ("ProviderType",)
    _financial_year_column = "FinancialYear"
    _filters = (("GeographicAreaType", "SA4"), ("ProviderType", _ALL_PROVIDERS))
    _filter_empty_hint = f"SA4 / {_ALL_PROVIDERS!r}"
    _sa4_code_column = "GeographicAreaCode"
    # SA4 codes are HYPHENATED (SA4-101) — strip the "SA4-" prefix.
    _sa4_code_strip_pattern = r"^SA4-"
    _measure_to_column = _MEASURE_TO_COLUMN
    _count_columns = _COUNT_COLUMNS


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhMedicareDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhMedicareDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_medicare", _build_fetcher)


_register()
