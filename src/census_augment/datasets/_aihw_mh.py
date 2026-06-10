"""AIHW Mental Health-related Prescriptions fetcher (spec §20, dataset
id ``aihw_mh_prescriptions``).

AIHW publishes the NMHSPF mental-health prescriptions ZIP at SA4 (not
SA2 or SA3) — 89 SA4 codes nationally. The augmentor joins SA4 values
onto SA2 rows via the boundary file's ``SA4_CODE21`` attribute (see
``spec.md`` §20.7 Strategy 1). Every SA2 inside SA4 X inherits SA4 X's
value unchanged — the honest "no within-parent variation" contract.

The ZIP contains:
- A long-format CSV mixing SA4 + PHN rows (the source of truth)
- A demographic-quarter CSV (not used)
- A metadata workbook (not used)

CSV is cp1252-encoded (en-dash characters in age ranges + FY labels);
SA4 codes carry an ``SA4`` prefix (``SA4101`` -> ``101``); the headline
filter is ``GeographicAreaType == SA4`` + ``Demographic``/
``DemographicCategory == Total``.

The common machinery (fetch, cache, downscale, parquet sidecar) lives in
:class:`AihwSa4Dataset`; this module declares only the per-dataset
schema specifics + the URL registry.
"""

from __future__ import annotations

from pathlib import Path

from ._aihw_sa4_base import AihwSa4Dataset

# AIHW getmedia URLs use opaque UUIDs that are stable per release.
# New annual releases need an entry added here. Discovery is via the
# NMHSPF "Regional activity data" page (link in spec markdown).
_AIHW_RX_URLS_BY_RELEASE: dict[str, str] = {
    "2024-25": (
        "https://www.aihw.gov.au/getmedia/"
        "464b35c8-9573-4a02-a508-0757c66feeb4/"
        "Mental-health-related-prescriptions-2024-25.zip"
    ),
}

# Map from the AIHW Measure label to the augmentor's snake_case column.
# Order is the column order the parser produces.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Patients": "mh_patients_count",
    "Patient rate per 1,000 population": "mh_patient_rate_per_1000",
    "Prescriptions": "mh_prescriptions_count",
    "Prescription rate per 1,000 population": "mh_prescription_rate_per_1000",
}


class AihwMhPrescriptionsDataSource(AihwSa4Dataset):
    """Fetch + load AIHW NMHSPF mental-health prescriptions data."""

    _label = "MH Rx"
    _cache_slug = "aihw-mh-rx"
    _registry_const_name = "_AIHW_RX_URLS_BY_RELEASE"
    _url_registry = _AIHW_RX_URLS_BY_RELEASE
    _encoding = "cp1252"
    _member_substrings = ("prescriptions phn and sa4",)
    _csv_missing_hint = "PHN+SA4 CSV (looked for *prescriptions PHN and SA4*.csv)"
    # No required-columns guard historically — preserve that (a missing
    # column raises KeyError at access, as before).
    _financial_year_column = "FinancialYear"
    _filters = (
        ("GeographicAreaType", "SA4"),
        ("Demographic", "Total"),
        ("DemographicCategory", "Total"),
    )
    _filter_empty_hint = "SA4/Total/Total"
    _sa4_code_column = "GeographicAreaCode"
    _sa4_code_strip_pattern = r"^SA4"
    _measure_to_column = _MEASURE_TO_COLUMN
    _count_columns = ("mh_patients_count", "mh_prescriptions_count")


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhPrescriptionsDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhPrescriptionsDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_prescriptions", _build_fetcher)


_register()
