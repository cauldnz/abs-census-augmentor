"""Tests for the AIHW Medicare-subsidised MH services fetcher (spec §20).

Synthetic ZIP+CSV fixtures mirror the live AIHW Medicare download
probed firsthand on 2026-06-05 (Real Data First). The fiddly bits this
dataset adds over its AIHW siblings:

- SA4 codes are **hyphenated** (``SA4-101``), not ``SA4101``.
- ``ProviderType`` values carry **non-breaking spaces** (U+00A0), e.g.
  ``"All\xa0providers"`` — the parser normalises NBSP before filtering.
- cp1252 encoding, multi-FY with en-dash labels.
- 4 measures: Patients / Services, each + a rate-per-1,000 twin.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_medicare import (
    _AIHW_MEDICARE_URLS_BY_RELEASE,
    AihwMhMedicareDataSource,
)

_2024_25_URL = _AIHW_MEDICARE_URLS_BY_RELEASE["2024-25"]

# Real ProviderType values use a non-breaking space (U+00A0). The
# fixtures encode that exactly so the parser's NBSP normalisation is
# genuinely exercised.
_NBSP = "\xa0"
_ALL_PROVIDERS_RAW = f"All{_NBSP}providers"

_MEASURES = [
    "Patients",
    "Patient rate per 1,000 population",
    "Services",
    "Service rate per 1,000 population",
]


def _make_medicare_zip(
    *,
    rows: list[tuple[str, str, str, str, str, float | int | None]],
) -> bytes:
    """Build a synthetic AIHW Medicare ZIP carrying one PHN SA4 CSV.

    Each row tuple is (FinancialYear, GeographicAreaType,
    GeographicAreaCode, ProviderType, Measure, Value). CSV is cp1252.
    """
    df = pd.DataFrame(
        [
            {
                "FinancialYear": fy,
                "GeographicAreaType": gat,
                "GeographicAreaCode": code,
                "phnname": "stub",
                "ProviderType": ptype,
                "Measure": measure,
                "Value": value,
            }
            for (fy, gat, code, ptype, measure, value) in rows
        ]
    )
    csv_bytes = df.to_csv(index=False).encode("cp1252")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Medicare mental health services PHN SA4 2024-25.csv", csv_bytes)
        zf.writestr(
            "Medicare mental health services quarters and demographics 2024-25.csv",
            b"FinancialYear,Demographic,Value\n",
        )
        zf.writestr("Medicare mental health service 2024-25.xlsx", b"fake-xlsx")
    return buf.getvalue()


def _full_sa4_rows(
    sa4_code: str,
    *,
    fy: str = "2024–25",  # en-dash to mirror source
    ptype: str = _ALL_PROVIDERS_RAW,
    patients: int = 12000,
    patient_rate: float = 480.0,
    services: int = 90000,
    service_rate: float = 3600.0,
) -> list[tuple[str, str, str, str, str, float | int]]:
    """Make the 4 Measure rows for one SA4 + FY + ProviderType.

    ``sa4_code`` should be the hyphenated form (``SA4-101``).
    """
    vals = {
        "Patients": patients,
        "Patient rate per 1,000 population": patient_rate,
        "Services": services,
        "Service rate per 1,000 population": service_rate,
    }
    return [(fy, "SA4", sa4_code, ptype, m, vals[m]) for m in _MEASURES]


@pytest.fixture
def medicare_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-medicare-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(medicare_data_dir: Path) -> None:
    ds = AihwMhMedicareDataSource(release="latest", root=medicare_data_dir)
    assert ds.resolved_release == "2024-25"


def test_resolve_unknown_release_raises(medicare_data_dir: Path) -> None:
    ds = AihwMhMedicareDataSource(release="2099-00", root=medicare_data_dir)
    with pytest.raises(RuntimeError, match="not in the hardcoded URL registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises(medicare_data_dir: Path) -> None:
    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_handles_hyphen_codes_and_nbsp(medicare_data_dir: Path) -> None:
    """The two signature quirks: hyphenated SA4-101 codes (stripped to
    bare 101) and NBSP in the All-providers ProviderType (normalised
    before filtering). Plus FY + PHN filtering.
    """
    rows = [
        *_full_sa4_rows("SA4-101", patients=12000, services=90000),
        *_full_sa4_rows("SA4-201", patients=20000, services=150000),
        # A per-provider split row (Psychiatrists) for SA4-101 that must
        # be filtered out — only "All providers" is the headline.
        ("2024–25", "SA4", "SA4-101", "Psychiatrists", "Patients", 999),
        # PHN row — ignored.
        ("2024–25", "PHN", "PHN101", _ALL_PROVIDERS_RAW, "Patients", 99999),
        # Earlier FY — filtered out.
        *_full_sa4_rows("SA4-101", fy="2022–23", patients=1),
    ]
    responses.add(responses.GET, _2024_25_URL, body=_make_medicare_zip(rows=rows), status=200)

    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "101011002": "101", "206011117": "201"})
    df = ds.load()

    # SA4-101 -> bare "101"; both its SA2s get the All-providers 2024-25
    # patients (12000), not the Psychiatrists 999 nor the 2022-23 value.
    assert set(df.index) == {"101011001", "101011002", "206011117"}
    assert df.loc["101011001", "mh_medicare_patients_count"] == 12000
    assert df.loc["101011002", "mh_medicare_patients_count"] == 12000
    assert df.loc["101011001", "mh_medicare_services_count"] == 90000
    assert df.loc["101011001", "mh_medicare_patient_rate_per_1000"] == 480.0
    assert df.loc["206011117", "mh_medicare_patients_count"] == 20000
    assert (df["reference_financial_year"] == "2024-25").all()


@responses.activate
def test_load_missing_sa4_emits_null(medicare_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4-101")
    responses.add(responses.GET, _2024_25_URL, body=_make_medicare_zip(rows=rows), status=200)
    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "997999999": "997"})
    df = ds.load()
    assert df.loc["101011001", "mh_medicare_patients_count"] == 12000
    assert pd.isna(df.loc["997999999", "mh_medicare_patients_count"])


@responses.activate
def test_load_raises_when_no_all_providers_rows(medicare_data_dir: Path) -> None:
    # Only a per-provider split, no "All providers" -> loud failure.
    rows = _full_sa4_rows("SA4-101", ptype="Psychiatrists")
    responses.add(responses.GET, _2024_25_URL, body=_make_medicare_zip(rows=rows), status=200)
    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="All providers"):
        _ = ds.load()


@responses.activate
def test_load_raises_when_csv_missing(medicare_data_dir: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("wrong.csv", b"FinancialYear\n2024-25\n")
    responses.add(responses.GET, _2024_25_URL, body=buf.getvalue(), status=200)
    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="missing the PHN\\+SA4 CSV"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(medicare_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4-101", patients=4242)
    responses.add(responses.GET, _2024_25_URL, body=_make_medicare_zip(rows=rows), status=200)
    mapping = {"101011001": "101", "101011002": "101"}
    ds = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()
    ds2 = AihwMhMedicareDataSource(root=medicare_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
