"""Tests for the AIHW Mental Health Prescriptions dataset fetcher (spec §20).

Synthetic ZIP+CSV fixtures mirror the live AIHW NMHSPF download probed
on 2026-06-01 (see ``tools/probe_new_datasets.py``):

- Long-format CSV with columns FinancialYear / GeographicAreaType /
  GeographicAreaCode / GeographicAreaName / Demographic /
  DemographicCategory / Measure / Value.
- Mixes PHN and SA4 rows in the same file.
- `GeographicAreaCode` for SA4 rows uses an "SA4" prefix (e.g.
  "SA4101"); the parser strips that before joining to the ABS
  boundary's bare 3-digit SA4_CODE21.
- FinancialYear values in source use a Unicode en-dash (–); fetcher
  normalises to ASCII for matching against `release` strings.
- Encoding is Windows-1252 (cp1252), not UTF-8.

This dataset is SA4-keyed and requires a SA2 -> SA4 mapping to be
attached before `.load()` (it downscales SA4 values to SA2 rows via
the boundary's `SA4_CODE21` attribute). Tests both directly attach
synthetic mappings and verify the right-shape errors when no mapping
is attached.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_mh import (
    _AIHW_RX_URLS_BY_RELEASE,
    AihwMhPrescriptionsDataSource,
)

# The hardcoded URL for the 2024-25 release — same as in
# _AIHW_RX_URLS_BY_RELEASE; pulled out so tests don't double-import.
_2024_25_URL = _AIHW_RX_URLS_BY_RELEASE["2024-25"]


def _make_aihw_zip(
    *,
    rows: list[tuple[str, str, str, str, str, str, str, float | int | None]],
    csv_filename: str = "Mental health-related prescriptions PHN and SA4 2024-25 (6).csv",
) -> bytes:
    """Build a synthetic AIHW NMHSPF ZIP carrying one PHN+SA4 CSV.

    Each row tuple is:
        (FinancialYear, GeographicAreaType, GeographicAreaCode,
         GeographicAreaName, Demographic, DemographicCategory,
         Measure, Value)

    Encoding the CSV as cp1252 mirrors the real AIHW publication
    (en-dash characters in FY labels and age bands).
    """
    df = pd.DataFrame(
        rows,
        columns=[
            "FinancialYear",
            "GeographicAreaType",
            "GeographicAreaCode",
            "GeographicAreaName",
            "Demographic",
            "DemographicCategory",
            "Measure",
            "Value",
        ],
    )
    csv_bytes = df.to_csv(index=False).encode("cp1252")

    # Pack into a ZIP that mirrors the real layout (additional files
    # alongside the SA4 CSV — the parser ignores them).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
        # Bonus files the real ZIP carries — verifying parser tolerates them.
        zf.writestr(
            "Mental health-related prescriptions quarters demographic 2024-25.csv",
            b"FinancialYear,FakeDemoGraphic,Value\n",
        )
        zf.writestr(
            "Mental health-related prescription tables 2024-25.xlsx",
            b"fake-binary-xlsx",
        )
    return buf.getvalue()


def _full_sa4_rows(
    sa4_code: str,
    sa4_name: str,
    *,
    fy: str = "2024–25",  # en-dash to mirror source
    patients: int = 50000,
    patient_rate: float = 200,
    prescriptions: int = 500000,
    prescription_rate: float = 2000,
) -> list[tuple[str, str, str, str, str, str, str, float | int]]:
    """Make the 4 Total/Total rows for one SA4 + FY combination — one row
    per Measure (Patients, Patient rate per 1,000, Prescriptions,
    Prescription rate per 1,000).
    """
    base = (fy, "SA4", sa4_code, sa4_name, "Total", "Total")
    return [
        (*base, "Patients", patients),
        (*base, "Patient rate per 1,000 population", patient_rate),
        (*base, "Prescriptions", prescriptions),
        (*base, "Prescription rate per 1,000 population", prescription_rate),
    ]


@pytest.fixture
def aihw_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest_picks_max_known_release(aihw_data_dir: Path) -> None:
    """`latest` resolves to the most recent entry in the URL registry."""
    ds = AihwMhPrescriptionsDataSource(release="latest", root=aihw_data_dir)
    # Only 2024-25 is registered right now; future releases add to the
    # constant. This test pins the current behaviour.
    assert ds.resolved_release == "2024-25"


def test_resolve_specific_known_release(aihw_data_dir: Path) -> None:
    ds = AihwMhPrescriptionsDataSource(release="2024-25", root=aihw_data_dir)
    assert ds.resolved_release == "2024-25"


def test_resolve_unknown_release_raises(aihw_data_dir: Path) -> None:
    """A release not in `_AIHW_RX_URLS_BY_RELEASE` raises with a clear
    message — AIHW uses opaque getmedia UUIDs so new releases need to
    be added to the constant.
    """
    ds = AihwMhPrescriptionsDataSource(release="2030-31", root=aihw_data_dir)
    with pytest.raises(RuntimeError, match=r"not in the hardcoded URL registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises_with_guidance(aihw_data_dir: Path) -> None:
    """Without an attached SA2 -> SA4 mapping, load() must refuse loudly
    rather than silently emit SA4-keyed output (which wouldn't join into
    the rest of the SA2-native pipeline).
    """
    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


def test_attach_mapping_rejects_non_dict(aihw_data_dir: Path) -> None:
    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    with pytest.raises(TypeError, match="dict"):
        ds.attach_sa2_to_sa4_mapping(["not", "a", "dict"])


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_downscales_sa4_to_sa2(aihw_data_dir: Path) -> None:
    """End-to-end: ZIP download, parse SA4 rows, downscale to SA2 via the
    attached mapping. Every SA2 inside SA4 X gets X's values.
    """
    rows = [
        # SA4 101: Central Coast, NSW
        *_full_sa4_rows(
            "SA4101",
            "Central Coast",
            patients=53989,
            patient_rate=219,
            prescriptions=521924,
            prescription_rate=2121,
        ),
        # SA4 201: Melbourne — Inner
        *_full_sa4_rows(
            "SA4201",
            "Melbourne - Inner",
            patients=78000,
            patient_rate=180,
            prescriptions=650000,
            prescription_rate=1500,
        ),
        # PHN row that the parser should ignore.
        ("2024–25", "PHN", "PHN101", "Eastern Sydney", "Total", "Total", "Patients", 99999),
        # Earlier-year SA4 row that should be filtered out (release="2024-25"
        # only).
        *_full_sa4_rows("SA4101", "Central Coast", fy="2015–16", patients=1),
    ]
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping(
        {
            # Two SA2s in SA4 101
            "102011028": "101",
            "102011029": "101",
            # One SA2 in SA4 201
            "206011117": "201",
        }
    )
    df = ds.load()

    # Three SA2 rows out
    assert set(df.index) == {"102011028", "102011029", "206011117"}
    assert df.index.name == "sa2_code_2021"

    # Both Central Coast SA2s get SA4 101's values
    assert df.loc["102011028", "mh_patients_count"] == 53989
    assert df.loc["102011029", "mh_patients_count"] == 53989
    assert df.loc["102011028", "mh_patient_rate_per_1000"] == 219
    assert df.loc["102011029", "mh_prescriptions_count"] == 521924

    # Melbourne SA4 201 SA2 gets that SA4's values
    assert df.loc["206011117", "mh_patients_count"] == 78000
    assert df.loc["206011117", "mh_prescription_rate_per_1000"] == 1500

    # Reference FY attached
    assert (df["reference_financial_year"] == "2024-25").all()


@responses.activate
def test_load_sa2_with_missing_sa4_emits_null(aihw_data_dir: Path) -> None:
    """SA2s mapped to a SA4 AIHW didn't publish (e.g. migratory / offshore
    pseudo-SA4s, or a boundary mismatch) get null values rather than being
    dropped — keeps the join with other datasets well-formed.
    """
    rows = _full_sa4_rows("SA4101", "Central Coast")
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping(
        {
            "102011028": "101",  # in publication
            "997999999": "997",  # not in publication
        }
    )
    df = ds.load()
    assert df.loc["102011028", "mh_patients_count"] == 50000
    # Null fields for the unmapped SA4
    assert pd.isna(df.loc["997999999", "mh_patients_count"])
    assert pd.isna(df.loc["997999999", "mh_prescription_rate_per_1000"])
    # Reference FY still attached for the orphan SA2
    assert df.loc["997999999", "reference_financial_year"] == "2024-25"


@responses.activate
def test_load_handles_en_dash_in_financial_year(aihw_data_dir: Path) -> None:
    """The source CSV uses en-dash in FY labels ('2024–25'); our release
    identifiers use ASCII hyphen ('2024-25'). The parser must normalise.
    """
    rows = _full_sa4_rows("SA4101", "Central Coast", fy="2024–25", patients=42)
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    df = ds.load()
    assert df.loc["102011028", "mh_patients_count"] == 42


@responses.activate
def test_load_raises_on_empty_csv(aihw_data_dir: Path) -> None:
    """If no SA4/Total/Total rows match the requested FY, raise with the
    available-years list so the caller can self-diagnose.
    """
    rows = _full_sa4_rows("SA4101", "Central Coast", fy="2015–16")
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    with pytest.raises(RuntimeError, match=r"no SA4/Total/Total rows"):
        _ = ds.load()


@responses.activate
def test_load_raises_when_csv_missing_from_zip(aihw_data_dir: Path) -> None:
    """If the ZIP doesn't contain the expected PHN+SA4 CSV (e.g. AIHW
    renamed the file), raise loudly with the ZIP's actual contents.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("wrong-filename.csv", b"FinancialYear\n2024-25\n")
    responses.add(responses.GET, _2024_25_URL, body=buf.getvalue(), status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    with pytest.raises(RuntimeError, match="missing the PHN\\+SA4 CSV"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(aihw_data_dir: Path) -> None:
    """Second load() should read from the parquet sidecar — same result,
    no additional network calls needed (the `responses` library asserts
    no extra calls).
    """
    rows = _full_sa4_rows("SA4101", "Central Coast", patients=12345)
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    mapping = {"102011028": "101", "102011029": "101"}
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()

    # Second instance + load — hits parquet cache, no extra GET needed.
    ds2 = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())


@responses.activate
def test_unknown_measure_label_is_warned_not_failed(
    aihw_data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If AIHW adds a new Measure label not in `_MEASURE_TO_COLUMN`,
    warn but continue (returning the known measures only). Keeps the
    fetcher resilient to additive upstream changes.
    """
    rows = _full_sa4_rows("SA4101", "Central Coast")
    # Add a hypothetical new measure
    rows.append(
        ("2024–25", "SA4", "SA4101", "Central Coast", "Total", "Total", "Some New Measure", 999)
    )
    zip_bytes = _make_aihw_zip(rows=rows)
    responses.add(responses.GET, _2024_25_URL, body=zip_bytes, status=200)

    ds = AihwMhPrescriptionsDataSource(root=aihw_data_dir)
    ds.attach_sa2_to_sa4_mapping({"102011028": "101"})
    with caplog.at_level("WARNING", logger="census_augment.datasets._aihw_mh"):
        df = ds.load()
    assert df.loc["102011028", "mh_patients_count"] == 50000
    assert any("Some New Measure" in rec.message for rec in caplog.records)
