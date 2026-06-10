"""AIHW Community Mental Health care fetcher (spec §20, dataset id
``aihw_mh_community``).

Fifth AIHW NMHSPF dataset. Captures state/territory **community
mental-health care** activity (the National Community Mental Health
database, "CMHC") — patients, contacts, treatment days — at SA4 level,
downscaled to SA2 via the boundary's ``SA4_CODE21`` attribute.
Catalogue identifier ``AIHW_CMH``.

Real-data findings (live-probed 2026-06-09) — confirmed firsthand off
the live ZIP, *correcting* the original scout note that implied a single
flat code column:

- The ZIP carries three long-format CSVs (Demog / Geospatial / Session
  focus). Only ``CMHC_MRF_GeospatialFocus_2324.csv`` is geographic — it
  is the one matched ("geospatial" in the member name).
- The CSV is **cp1252**.
- ``GeospatialType`` ∈ {``GCSSA``, ``PHN``, ``SA4``}. The
  ``GeospatialDivisionCode`` column is **polymorphic**: for ``SA4`` rows
  it holds the bare 3-digit SA4 code (``"101"``) — joining directly to
  the boundary's ``SA4_CODE21`` with no prefix strip — but for
  ``GCSSA``/``PHN`` rows it holds a *name* ("Greater Sydney"). So the
  ``GeospatialType == "SA4"`` filter is load-bearing, not cosmetic.
- A ``DemographicCategory`` / ``DemographicVariable`` pair carries the
  breakdowns; the headline total is ``Total`` / ``Total``.
- Multi-FY file (2013-14 … 2023-24) with **en-dash FY labels**
  (``2023–24``) — normalised + filtered to the requested release.
- Seven measures (see ``_MEASURE_TO_COLUMN``); the SA4 + Total/Total +
  FY slice pivots cleanly (one row per division × measure). The CSV is
  read all-string, so every value column is coerced to numeric.

The common machinery lives in :class:`AihwSa4Dataset`; this module
declares only the per-dataset schema specifics + the URL registry.
"""

from __future__ import annotations

from pathlib import Path

from ._aihw_sa4_base import AihwSa4Dataset

# AIHW getmedia URLs use opaque UUIDs per release. Discovered live from
# the NMHSPF "Regional activity data" landing page (2026-06-09).
_AIHW_CMH_URLS_BY_RELEASE: dict[str, str] = {
    "2023-24": (
        "https://www.aihw.gov.au/getmedia/"
        "f04af158-e8b1-4660-93cc-8fd85eea5a08/"
        "Community-mental-health-care-state-and-territory-tables-2023-24.zip"
    ),
}

_MEASURE_TO_COLUMN: dict[str, str] = {
    "Number of patients": "mh_community_patients_count",
    "Number of patients per 10,000 population": "mh_community_patients_per_10000",
    "Number of contacts": "mh_community_contacts_count",
    "Number of contacts per 10,000 population": "mh_community_contacts_per_10000",
    "Number of treatment days per three-month period": "mh_community_treatment_days_per_3mo",
    "Average length of treatment (days)": "mh_community_avg_treatment_length_days",
    "Population": "mh_community_population",
}

# Integer-valued measures (counts / population); the rest are rates or
# averages and stay float.
_COUNT_COLUMNS: tuple[str, ...] = (
    "mh_community_patients_count",
    "mh_community_contacts_count",
    "mh_community_population",
)


class AihwMhCommunityDataSource(AihwSa4Dataset):
    """Fetch + load AIHW NMHSPF Community Mental Health care data."""

    _label = "MH Community"
    _cache_slug = "aihw-mh-community"
    _registry_const_name = "_AIHW_CMH_URLS_BY_RELEASE"
    _url_registry = _AIHW_CMH_URLS_BY_RELEASE
    _encoding = "cp1252"
    # Read all-string: the SA4 code column is numeric-looking but must
    # stay text (and the GCSSA/PHN rows put names in the same column).
    _csv_dtype = str
    _member_substrings = ("geospatial",)
    _csv_missing_hint = "Geospatial-focus CSV (looked for *geospatial*.csv)"
    _required_columns = frozenset(
        {
            "FinancialYear",
            "GeospatialType",
            "GeospatialDivisionCode",
            "DemographicCategory",
            "DemographicVariable",
            "MeasureName",
            "MeasureValue",
        }
    )
    _financial_year_column = "FinancialYear"
    _filters = (
        ("GeospatialType", "SA4"),
        ("DemographicCategory", "Total"),
        ("DemographicVariable", "Total"),
    )
    _filter_empty_hint = "SA4 / Total / Total"
    _sa4_code_column = "GeospatialDivisionCode"
    # SA4 rows carry the bare 3-digit code directly — no prefix to strip.
    _sa4_code_strip_pattern = None
    _measure_column = "MeasureName"
    _value_column = "MeasureValue"
    _measure_to_column = _MEASURE_TO_COLUMN
    _count_columns = _COUNT_COLUMNS
    # CSV read all-string -> every value column needs numeric coercion.
    _coerce_all_value_columns = True


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhCommunityDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhCommunityDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_community", _build_fetcher)


_register()
