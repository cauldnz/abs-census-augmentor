"""Tests for the AIHW Community Mental Health care fetcher (spec §20).

Synthetic ZIP+CSV fixtures mirror the live AIHW Community MH download
probed firsthand on 2026-06-09 (Real Data First). The signature quirks
this dataset adds:

- Three CSVs in the ZIP; only the **GeospatialFocus** one is read
  (matched by "geospatial" in the member name).
- ``GeospatialType`` mixes {``GCSSA``, ``PHN``, ``SA4``} in one file and
  ``GeospatialDivisionCode`` is **polymorphic** — a bare 3-digit code
  for ``SA4`` rows, a place *name* for ``GCSSA``/``PHN`` rows. The
  ``GeospatialType == "SA4"`` filter is therefore load-bearing.
- ``DemographicCategory`` / ``DemographicVariable`` carry the
  breakdowns; the headline is ``Total`` / ``Total``.
- cp1252 encoding, multi-FY with en-dash labels, 7 measures.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_cmh import (
    _AIHW_CMH_URLS_BY_RELEASE,
    AihwMhCommunityDataSource,
)

_2023_24_URL = _AIHW_CMH_URLS_BY_RELEASE["2023-24"]

_MEASURES = [
    "Number of patients",
    "Number of patients per 10,000 population",
    "Number of contacts",
    "Number of contacts per 10,000 population",
    "Number of treatment days per three-month period",
    "Average length of treatment (days)",
    "Population",
]

# Row tuple: (FinancialYear, GeospatialType, GeospatialDivisionCode,
#             DemographicCategory, DemographicVariable, MeasureName, Value)
_Row = tuple[str, str, str, str, str, str, float | int]


def _make_cmh_zip(*, rows: list[_Row]) -> bytes:
    """Build a synthetic AIHW Community MH ZIP carrying one
    GeospatialFocus CSV (cp1252) plus the sibling CSVs the parser
    ignores.
    """
    df = pd.DataFrame(
        [
            {
                "DataSource": "National Community Mental Health database",
                "FinancialYear": fy,
                "GeospatialType": gtype,
                "GeospatialDivisionCode": code,
                "GeospatialDivision": f"Name of {code}",
                "DemographicCategory": dcat,
                "DemographicVariable": dvar,
                "MeasureName": measure,
                "MeasureValue": value,
            }
            for (fy, gtype, code, dcat, dvar, measure, value) in rows
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("cp1252")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CMHC_MRF_GeospatialFocus_2324.csv", csv_bytes)
        zf.writestr("CMHC_MRF_DemogFocus_2324.csv", b"DataSource,FinancialYear,MeasureValue\n")
        zf.writestr("CMHC_MRF_SessionFocus_2324.csv", b"DataSource,FinancialYear,MeasureValue\n")
        zf.writestr("Community mental health care Tables 2023-24.xlsx", b"fake-xlsx")
    return buf.getvalue()


def _full_sa4_rows(
    sa4_code: str,
    *,
    fy: str = "2023–24",  # en-dash to mirror source
    dcat: str = "Total",
    dvar: str = "Total",
    patients: int = 5011,
    patients_per_10000: float = 206.5,
    contacts: int = 84899,
    contacts_per_10000: float = 3492.0,
    treatment_days: float = 7.0,
    avg_length: float = 90.0,
    population: int = 243095,
) -> list[_Row]:
    """Make the 7 Measure rows for one SA4 + FY + demographic cell."""
    vals = {
        "Number of patients": patients,
        "Number of patients per 10,000 population": patients_per_10000,
        "Number of contacts": contacts,
        "Number of contacts per 10,000 population": contacts_per_10000,
        "Number of treatment days per three-month period": treatment_days,
        "Average length of treatment (days)": avg_length,
        "Population": population,
    }
    return [(fy, "SA4", sa4_code, dcat, dvar, m, vals[m]) for m in _MEASURES]


@pytest.fixture
def cmh_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-cmh-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(cmh_data_dir: Path) -> None:
    ds = AihwMhCommunityDataSource(release="latest", root=cmh_data_dir)
    assert ds.resolved_release == "2023-24"


def test_resolve_unknown_release_raises(cmh_data_dir: Path) -> None:
    ds = AihwMhCommunityDataSource(release="2099-00", root=cmh_data_dir)
    with pytest.raises(RuntimeError, match="not in the hardcoded URL registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises(cmh_data_dir: Path) -> None:
    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_filters_to_sa4_total_and_downscales(cmh_data_dir: Path) -> None:
    """The signature behaviour: filter to SA4 + Total/Total + FY, exclude
    the name-coded GCSSA rows and the age-group demographic splits, strip
    nothing (bare codes), and downscale each SA4 to its SA2s.
    """
    rows: list[_Row] = [
        *_full_sa4_rows("101", patients=5011, contacts=84899, population=243095),
        *_full_sa4_rows("201", patients=8000, contacts=120000, population=400000),
        # GCSSA row whose *code* column holds a NAME — must be excluded by
        # the GeospatialType filter (and must not break parsing).
        ("2023–24", "GCSSA", "Greater Sydney", "Total", "Total", "Number of patients", 99999),
        # Age-group split for SA4-101 — excluded (DemographicVariable != Total).
        ("2023–24", "SA4", "101", "Age group", "0–11 years", "Number of patients", 7),
        # Earlier FY for SA4-101 — excluded.
        *_full_sa4_rows("101", fy="2022–23", patients=1),
    ]
    responses.add(responses.GET, _2023_24_URL, body=_make_cmh_zip(rows=rows), status=200)

    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "101011002": "101", "206011117": "201"})
    df = ds.load()

    assert set(df.index) == {"101011001", "101011002", "206011117"}
    # SA4-101 -> both its SA2s get the 2023-24 Total/Total values, not the
    # age-split 7, the GCSSA 99999, nor the 2022-23 value.
    assert df.loc["101011001", "mh_community_patients_count"] == 5011
    assert df.loc["101011002", "mh_community_patients_count"] == 5011
    assert df.loc["101011001", "mh_community_contacts_count"] == 84899
    assert df.loc["101011001", "mh_community_population"] == 243095
    assert df.loc["101011001", "mh_community_patients_per_10000"] == 206.5
    assert df.loc["206011117", "mh_community_patients_count"] == 8000
    assert (df["reference_financial_year"] == "2023-24").all()


@responses.activate
def test_load_missing_sa4_emits_null(cmh_data_dir: Path) -> None:
    rows = _full_sa4_rows("101")
    responses.add(responses.GET, _2023_24_URL, body=_make_cmh_zip(rows=rows), status=200)
    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "997999999": "997"})
    df = ds.load()
    assert df.loc["101011001", "mh_community_patients_count"] == 5011
    assert pd.isna(df.loc["997999999", "mh_community_patients_count"])


@responses.activate
def test_load_raises_when_no_sa4_total_rows(cmh_data_dir: Path) -> None:
    # Only an age-group split, no Total/Total -> loud failure.
    rows = _full_sa4_rows("101", dcat="Age group", dvar="0–11 years")
    responses.add(responses.GET, _2023_24_URL, body=_make_cmh_zip(rows=rows), status=200)
    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="no SA4 / Total / Total"):
        _ = ds.load()


@responses.activate
def test_load_raises_when_csv_missing(cmh_data_dir: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("CMHC_MRF_DemogFocus_2324.csv", b"FinancialYear\n2023-24\n")
    responses.add(responses.GET, _2023_24_URL, body=buf.getvalue(), status=200)
    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="missing the Geospatial-focus CSV"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(cmh_data_dir: Path) -> None:
    rows = _full_sa4_rows("101", patients=4242)
    responses.add(responses.GET, _2023_24_URL, body=_make_cmh_zip(rows=rows), status=200)
    mapping = {"101011001": "101", "101011002": "101"}
    ds = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()
    ds2 = AihwMhCommunityDataSource(root=cmh_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
