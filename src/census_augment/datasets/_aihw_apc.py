"""AIHW Mental Health Admitted Patient Care fetcher (spec §20, dataset
id ``aihw_mh_admitted_patients``).

Sibling to :class:`AihwMhPrescriptionsDataSource` — same AIHW NMHSPF
"Regional activity data" source family, same SA4 → SA2 downscale
pattern. AIHW publishes mental-health admitted-patient-care activity at
**SA4** level (89 SA4s nationally); the augmentor downscales to SA2 via
the boundary file's ``SA4_CODE21`` attribute (see ``spec.md`` §20.7
Strategy 1). Every SA2 inside SA4 X inherits SA4 X's value unchanged.

Real-data findings (live-probed 2026-06-05) vs the MH-Prescriptions ZIP:

- The member CSV is **UTF-8**, NOT cp1252 like the prescriptions file.
  Different files in the same AIHW source family use different
  encodings, so the encoding is per-dataset (don't share the constant).
- There is **no ``FinancialYear`` column** — the ZIP is a single-year
  (2023-24) publication, so the release id is fixed and no FY filter
  applies. (The prescriptions file carried 10 FYs in one CSV.)
- The headline filter dimension is **``SeparationType == "Total"``**
  (other values: ``Same day``, ``Overnight``).
- SA4 codes use the same ``SA4101`` prefix form as prescriptions
  (strip ``^SA4`` to match the boundary's bare 3-digit ``SA4_CODE21``).
- Columns: ``Jurisdiction, GeographicAreaType, GeographicAreaCode,
  GeographicAreaName, SeparationType, Measure, Value``.

The common machinery lives in :class:`AihwSa4Dataset`; this module
declares only the per-dataset schema specifics + the URL registry.
"""

from __future__ import annotations

from pathlib import Path

from ._aihw_sa4_base import AihwSa4Dataset

# AIHW getmedia URLs use opaque UUIDs that are stable per release.
# New annual releases need an entry added here. Discovery is via the
# NMHSPF "Regional activity data" page (link in the spec markdown).
_AIHW_APC_URLS_BY_RELEASE: dict[str, str] = {
    "2023-24": (
        "https://www.aihw.gov.au/getmedia/"
        "1ed521e7-7ee2-4dc0-98a4-d4f0bd0b027d/"
        "Admitted-patient-care-state-and-territory-2023-24-data-files.zip"
    ),
}

# Map from the AIHW Measure label (verbatim from the real CSV) to the
# augmentor's snake_case column. Four metrics, each with a count + a
# per-10,000-population rate twin = 8 columns.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Hospitalisations": "mh_hospitalisations_count",
    "Patient days": "mh_patient_days_count",
    "Psychiatric care days": "mh_psychiatric_care_days_count",
    "Procedures": "mh_procedures_count",
    "Hospitalisations per 10,000 population": "mh_hospitalisations_per_10000",
    "Patient days per 10,000 population": "mh_patient_days_per_10000",
    "Psychiatric care days per 10,000 population": "mh_psychiatric_care_days_per_10000",
    "Procedures per 10,000 population": "mh_procedures_per_10000",
}

# The four count columns coerce to nullable Int64; the rate twins stay float.
_COUNT_COLUMNS: tuple[str, ...] = (
    "mh_hospitalisations_count",
    "mh_patient_days_count",
    "mh_psychiatric_care_days_count",
    "mh_procedures_count",
)


class AihwMhAdmittedPatientsDataSource(AihwSa4Dataset):
    """Fetch + load AIHW NMHSPF mental-health admitted-patient-care data."""

    _label = "MH APC"
    _cache_slug = "aihw-mh-apc"
    _registry_const_name = "_AIHW_APC_URLS_BY_RELEASE"
    _url_registry = _AIHW_APC_URLS_BY_RELEASE
    # UTF-8 (real-data finding) — NOT cp1252 like the prescriptions sibling.
    _encoding = "utf-8"
    _member_substrings = ("phn_sa4",)
    _csv_missing_hint = "PHN_SA4 CSV (looked for *PHN_SA4*.csv)"
    _required_columns = frozenset(
        {"GeographicAreaType", "SeparationType", "GeographicAreaCode", "Measure", "Value"}
    )
    # No FinancialYear column — single-year publication.
    _financial_year_column = None
    _filters = (("GeographicAreaType", "SA4"), ("SeparationType", "Total"))
    _filter_empty_hint = "SA4/Total"
    _sa4_code_column = "GeographicAreaCode"
    _sa4_code_strip_pattern = r"^SA4"
    _measure_to_column = _MEASURE_TO_COLUMN
    _count_columns = _COUNT_COLUMNS


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhAdmittedPatientsDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhAdmittedPatientsDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_admitted_patients", _build_fetcher)


_register()
