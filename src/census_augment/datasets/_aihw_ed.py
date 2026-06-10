"""AIHW Mental Health Emergency Department services fetcher (spec §20,
dataset id ``aihw_mh_ed_presentations``).

Third AIHW NMHSPF dataset (after ``aihw_mh_prescriptions`` and
``aihw_mh_admitted_patients``). Captures mental-health-related
**emergency department presentations** at SA4 level, downscaled to SA2
via the boundary file's ``SA4_CODE21`` attribute (see ``spec.md`` §20.7
Strategy 1). Catalogue identifier ``AIHW_ED``.

Real-data findings (live-probed 2026-06-05):

- The member CSV lives inside a subdirectory whose name contains a
  **literal Unicode en-dash** (``Data tables_ED states and territories
  2023–24/ED_PHN_SA4_2324.csv``). Match the member by the ``PHN_SA4``
  substring, NOT an exact path.
- The CSV is **cp1252** (like prescriptions; unlike APC which is UTF-8).
- The file carries **multiple financial years** (2014-15 … 2023-24)
  with the FY label using a Unicode en-dash — normalise to ASCII and
  filter to the requested release.
- A **``PresentationType``** dimension (``Mental health-related
  presentations`` vs ``All presentations``) — filter to the MH-related
  rows for the headline values.
- SA4 codes use the ``SA4101`` prefix form (strip ``^SA4``).
- Columns: ``FinancialYear, PresentationType, StateOrTerritory,
  GeographicAreaType, GeographicAreaCode, GeographicAreaName, Measure,
  Value``. Two measures: ``Number`` and ``Rate (per 10,000 population)``.

The common machinery lives in :class:`AihwSa4Dataset`; this module
declares only the per-dataset schema specifics + the URL registry.
"""

from __future__ import annotations

from pathlib import Path

from ._aihw_sa4_base import AihwSa4Dataset

# AIHW getmedia URLs use opaque UUIDs per release. The single ZIP
# carries all financial years; the release id selects which FY's rows
# the parser surfaces. New ZIP releases need a new UUID added here.
_AIHW_ED_URLS_BY_RELEASE: dict[str, str] = {
    "2023-24": (
        "https://www.aihw.gov.au/getmedia/"
        "f9ac2b47-69b7-47f5-a1a2-7e5d1099195b/"
        "Mental-health-services-provided-in-emergency-departments-"
        "states-and-territories-2023-24.zip"
    ),
}

# Map the AIHW Measure label (verbatim) to the augmentor's snake_case
# column. Two measures: a count and a per-10,000-population rate.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Number": "mh_ed_presentations_count",
    "Rate (per 10,000 population)": "mh_ed_presentations_per_10000",
}

_COUNT_COLUMNS: tuple[str, ...] = ("mh_ed_presentations_count",)

# Only the MH-related rows are the headline; the file also carries an
# "All presentations" denominator series.
_MH_PRESENTATION_TYPE = "Mental health-related presentations"


class AihwMhEdPresentationsDataSource(AihwSa4Dataset):
    """Fetch + load AIHW NMHSPF mental-health ED-presentations data."""

    _label = "MH ED"
    _cache_slug = "aihw-mh-ed"
    _registry_const_name = "_AIHW_ED_URLS_BY_RELEASE"
    _url_registry = _AIHW_ED_URLS_BY_RELEASE
    _encoding = "cp1252"
    _member_substrings = ("phn_sa4",)
    _csv_missing_hint = "PHN_SA4 CSV (looked for *PHN_SA4*.csv)"
    _required_columns = frozenset(
        {
            "FinancialYear",
            "PresentationType",
            "GeographicAreaType",
            "GeographicAreaCode",
            "Measure",
            "Value",
        }
    )
    _financial_year_column = "FinancialYear"
    _filters = (
        ("GeographicAreaType", "SA4"),
        ("PresentationType", _MH_PRESENTATION_TYPE),
    )
    _filter_empty_hint = f"SA4 / {_MH_PRESENTATION_TYPE!r}"
    _sa4_code_column = "GeographicAreaCode"
    _sa4_code_strip_pattern = r"^SA4"
    _measure_to_column = _MEASURE_TO_COLUMN
    _count_columns = _COUNT_COLUMNS


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhEdPresentationsDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhEdPresentationsDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_ed_presentations", _build_fetcher)


_register()
