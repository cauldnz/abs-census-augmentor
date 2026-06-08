"""Tests for the AIHW Mental Health Admitted Patient Care fetcher (spec §20).

Synthetic ZIP+CSV fixtures mirror the live AIHW APC download probed
firsthand on 2026-06-05 (Real Data First):

- Member CSV ``Admitted patient care state and territory PHN_SA4
  2023-24.csv``, **UTF-8** (NOT cp1252 like the prescriptions sibling).
- Columns: FinancialYear is ABSENT; the real columns are
  ``Jurisdiction, GeographicAreaType, GeographicAreaCode,
  GeographicAreaName, SeparationType, Measure, Value``.
- ``GeographicAreaType`` ∈ {PHN, SA4}; SA4 codes like ``SA4101``.
- ``SeparationType`` ∈ {Same day, Overnight, Total} — headline filter
  is ``Total``.
- 8 ``Measure`` values: Hospitalisations / Patient days / Psychiatric
  care days / Procedures, each + " per 10,000 population".

This dataset is SA4-keyed and requires a SA2 → SA4 mapping attached
before ``.load()`` (downscale to SA2). Tests attach synthetic mappings.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_apc import (
    _AIHW_APC_URLS_BY_RELEASE,
    AihwMhAdmittedPatientsDataSource,
)

_2023_24_URL = _AIHW_APC_URLS_BY_RELEASE["2023-24"]

# The 8 real Measure labels, in (count, rate) pairs.
_MEASURES = [
    "Hospitalisations",
    "Patient days",
    "Psychiatric care days",
    "Procedures",
    "Hospitalisations per 10,000 population",
    "Patient days per 10,000 population",
    "Psychiatric care days per 10,000 population",
    "Procedures per 10,000 population",
]


def _make_apc_zip(
    *,
    rows: list[tuple[str, str, str, str, float | int | None]],
    csv_filename: str = "Admitted patient care state and territory PHN_SA4 2023-24.csv",
) -> bytes:
    """Build a synthetic AIHW APC ZIP carrying one PHN_SA4 CSV.

    Each row tuple is (GeographicAreaType, GeographicAreaCode,
    SeparationType, Measure, Value). The CSV is UTF-8 (real-data
    finding) and includes the Jurisdiction / GeographicAreaName columns
    the real file has, populated with stub values.
    """
    df = pd.DataFrame(
        [
            {
                "Jurisdiction": "NSW",
                "GeographicAreaType": gat,
                "GeographicAreaCode": code,
                "GeographicAreaName": f"Area {code}",
                "SeparationType": sep,
                "Measure": measure,
                "Value": value,
            }
            for (gat, code, sep, measure, value) in rows
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
        # Bonus members the real ZIP carries — the parser must ignore them.
        zf.writestr(
            "Admitted patient care state and territory Common Procedures 2023-24.csv",
            b"Jurisdiction,Procedure,Value\nNSW,Fake,1\n",
        )
        zf.writestr(
            "Admitted patient care state and territory tables 2023-24.xlsx",
            b"fake-binary-xlsx",
        )
    return buf.getvalue()


def _full_sa4_rows(
    sa4_code: str,
    *,
    sep: str = "Total",
    hosp: int = 5000,
    patient_days: int = 40000,
    psych_days: int = 12000,
    procedures: int = 800,
    hosp_rate: float = 250.0,
    patient_days_rate: float = 2000.0,
    psych_days_rate: float = 600.0,
    procedures_rate: float = 40.0,
) -> list[tuple[str, str, str, str, float | int]]:
    """Make the 8 Measure rows for one SA4 + SeparationType combination."""
    vals = {
        "Hospitalisations": hosp,
        "Patient days": patient_days,
        "Psychiatric care days": psych_days,
        "Procedures": procedures,
        "Hospitalisations per 10,000 population": hosp_rate,
        "Patient days per 10,000 population": patient_days_rate,
        "Psychiatric care days per 10,000 population": psych_days_rate,
        "Procedures per 10,000 population": procedures_rate,
    }
    return [("SA4", sa4_code, sep, m, vals[m]) for m in _MEASURES]


@pytest.fixture
def apc_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-apc-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(apc_data_dir: Path) -> None:
    ds = AihwMhAdmittedPatientsDataSource(release="latest", root=apc_data_dir)
    assert ds.resolved_release == "2023-24"


def test_resolve_specific(apc_data_dir: Path) -> None:
    ds = AihwMhAdmittedPatientsDataSource(release="2023-24", root=apc_data_dir)
    assert ds.resolved_release == "2023-24"


def test_resolve_unknown_release_raises(apc_data_dir: Path) -> None:
    ds = AihwMhAdmittedPatientsDataSource(release="2099-00", root=apc_data_dir)
    with pytest.raises(RuntimeError, match="not in the hardcoded URL registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises(apc_data_dir: Path) -> None:
    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


def test_attach_mapping_rejects_non_dict(apc_data_dir: Path) -> None:
    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    with pytest.raises(TypeError, match="dict"):
        ds.attach_sa2_to_sa4_mapping(["nope"])


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_downscales_sa4_to_sa2(apc_data_dir: Path) -> None:
    rows = [
        *_full_sa4_rows("SA4101", hosp=5000, patient_days=40000),
        *_full_sa4_rows("SA4201", hosp=8000, patient_days=70000),
        # PHN row the parser must ignore.
        ("PHN", "PHN101", "Total", "Hospitalisations", 99999),
        # Non-Total separation rows that must be filtered out.
        *_full_sa4_rows("SA4101", sep="Same day", hosp=1),
        *_full_sa4_rows("SA4101", sep="Overnight", hosp=2),
    ]
    responses.add(responses.GET, _2023_24_URL, body=_make_apc_zip(rows=rows), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping(
        {
            "101011001": "101",
            "101011002": "101",
            "206011117": "201",
        }
    )
    df = ds.load()

    assert set(df.index) == {"101011001", "101011002", "206011117"}
    assert df.index.name == "sa2_code_2021"

    # Both SA4-101 SA2s get SA4 101's "Total" values (not Same day / Overnight).
    assert df.loc["101011001", "mh_hospitalisations_count"] == 5000
    assert df.loc["101011002", "mh_hospitalisations_count"] == 5000
    assert df.loc["101011001", "mh_patient_days_count"] == 40000
    # Melbourne SA4 gets its own.
    assert df.loc["206011117", "mh_hospitalisations_count"] == 8000

    # Rate twins present + float.
    assert df.loc["101011001", "mh_hospitalisations_per_10000"] == 250.0

    # All 8 metric columns + ref FY.
    expected_cols = {
        "mh_hospitalisations_count",
        "mh_patient_days_count",
        "mh_psychiatric_care_days_count",
        "mh_procedures_count",
        "mh_hospitalisations_per_10000",
        "mh_patient_days_per_10000",
        "mh_psychiatric_care_days_per_10000",
        "mh_procedures_per_10000",
        "reference_financial_year",
    }
    assert expected_cols <= set(df.columns)
    assert (df["reference_financial_year"] == "2023-24").all()


@responses.activate
def test_load_sa2_with_missing_sa4_emits_null(apc_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4101")
    responses.add(responses.GET, _2023_24_URL, body=_make_apc_zip(rows=rows), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping(
        {
            "101011001": "101",  # published
            "997999999": "997",  # not published
        }
    )
    df = ds.load()
    assert df.loc["101011001", "mh_hospitalisations_count"] == 5000
    assert pd.isna(df.loc["997999999", "mh_hospitalisations_count"])
    assert df.loc["997999999", "reference_financial_year"] == "2023-24"


@responses.activate
def test_load_raises_when_no_sa4_total_rows(apc_data_dir: Path) -> None:
    # Only Same day rows — no Total → loud failure naming what was seen.
    rows = _full_sa4_rows("SA4101", sep="Same day")
    responses.add(responses.GET, _2023_24_URL, body=_make_apc_zip(rows=rows), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="no SA4/Total rows"):
        _ = ds.load()


@responses.activate
def test_load_raises_when_csv_missing_from_zip(apc_data_dir: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("wrong-name.csv", b"Jurisdiction\nNSW\n")
    responses.add(responses.GET, _2023_24_URL, body=buf.getvalue(), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="missing the PHN_SA4 CSV"):
        _ = ds.load()


@responses.activate
def test_load_raises_on_missing_columns(apc_data_dir: Path) -> None:
    # A CSV whose member name matches but whose columns are wrong should
    # fail loud (schema-drift guard), not silently mis-parse.
    bad = pd.DataFrame({"Jurisdiction": ["NSW"], "SomethingElse": [1]})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(
            "Admitted patient care state and territory PHN_SA4 2023-24.csv",
            bad.to_csv(index=False).encode("utf-8"),
        )
    responses.add(responses.GET, _2023_24_URL, body=buf.getvalue(), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="missing expected columns"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(apc_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4101", hosp=4242)
    responses.add(responses.GET, _2023_24_URL, body=_make_apc_zip(rows=rows), status=200)

    mapping = {"101011001": "101", "101011002": "101"}
    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()

    ds2 = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())


@responses.activate
def test_unknown_measure_label_is_warned_not_failed(
    apc_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rows = _full_sa4_rows("SA4101")
    rows.append(("SA4", "SA4101", "Total", "Some New Measure", 999))
    responses.add(responses.GET, _2023_24_URL, body=_make_apc_zip(rows=rows), status=200)

    ds = AihwMhAdmittedPatientsDataSource(root=apc_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with caplog.at_level("WARNING", logger="census_augment.datasets._aihw_apc"):
        df = ds.load()
    assert df.loc["101011001", "mh_hospitalisations_count"] == 5000
    assert any("Some New Measure" in rec.message for rec in caplog.records)
