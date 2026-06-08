"""Tests for the AIHW Mental Health ED Presentations fetcher (spec §20).

Synthetic ZIP+CSV fixtures mirror the live AIHW ED download probed
firsthand on 2026-06-05 (Real Data First):

- Member CSV inside a subdir whose name carries a Unicode en-dash
  (``Data tables_ED states and territories 2023–24/ED_PHN_SA4_2324.csv``)
  — the parser matches by the ``PHN_SA4`` substring, not exact path.
- **cp1252** encoding (the prescriptions sibling is cp1252 too; APC is
  UTF-8 — encoding is per-dataset).
- Columns: ``FinancialYear, PresentationType, StateOrTerritory,
  GeographicAreaType, GeographicAreaCode, GeographicAreaName, Measure,
  Value``.
- ``GeographicAreaType`` ∈ {PHN, SA4}; SA4 codes like ``SA4101``.
- ``PresentationType`` ∈ {Mental health-related presentations, All
  presentations} — headline filter is the MH-related one.
- ``FinancialYear`` uses a Unicode en-dash; multi-year file.
- 2 ``Measure`` values: Number, Rate (per 10,000 population).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import responses

from census_augment.datasets._aihw_ed import (
    _AIHW_ED_URLS_BY_RELEASE,
    AihwMhEdPresentationsDataSource,
)

_2023_24_URL = _AIHW_ED_URLS_BY_RELEASE["2023-24"]

# The real member path carries a Unicode en-dash in the subdir name.
_MEMBER_PATH = "Data tables_ED states and territories 2023–24/ED_PHN_SA4_2324.csv"


def _make_ed_zip(
    *,
    rows: list[tuple[str, str, str, str, str, float | int | None]],
    member_path: str = _MEMBER_PATH,
) -> bytes:
    """Build a synthetic AIHW ED ZIP carrying one PHN_SA4 CSV inside an
    en-dash subdir. Each row tuple is (FinancialYear, PresentationType,
    GeographicAreaType, GeographicAreaCode, Measure, Value) — 6 fields.
    """
    df = pd.DataFrame(
        [
            {
                "FinancialYear": fy,
                "PresentationType": ptype,
                "StateOrTerritory": "NSW",
                "GeographicAreaType": gat,
                "GeographicAreaCode": code,
                "GeographicAreaName": f"Area {code}",
                "Measure": measure,
                "Value": value,
            }
            for (fy, ptype, gat, code, measure, value) in rows
        ]
    )
    # cp1252 (real-data finding) — en-dash in FY labels round-trips.
    csv_bytes = df.to_csv(index=False).encode("cp1252")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_path, csv_bytes)
        # Bonus members the real ZIP carries — parser must ignore them.
        subdir = member_path.rsplit("/", 1)[0]
        zf.writestr(f"{subdir}/ED_Time_of_day_2324.csv", b"FinancialYear,Hour,Value\n")
        zf.writestr(f"{subdir}/ED_csv_README_and_metadata_user_guide_2324.xlsx", b"fake-xlsx")
    return buf.getvalue()


def _full_sa4_rows(
    sa4_code: str,
    *,
    fy: str = "2023–24",  # en-dash to mirror source
    ptype: str = "Mental health-related presentations",
    number: int = 4200,
    rate: float = 180.0,
) -> list[tuple[str, str, str, str, str, float | int]]:
    """Make the 2 Measure rows for one SA4 + FY + PresentationType."""
    return [
        (fy, ptype, "SA4", sa4_code, "Number", number),
        (fy, ptype, "SA4", sa4_code, "Rate (per 10,000 population)", rate),
    ]


@pytest.fixture
def ed_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "aihw-ed-cache"


# ---- release resolution --------------------------------------------------


def test_resolve_latest(ed_data_dir: Path) -> None:
    ds = AihwMhEdPresentationsDataSource(release="latest", root=ed_data_dir)
    assert ds.resolved_release == "2023-24"


def test_resolve_unknown_release_raises(ed_data_dir: Path) -> None:
    ds = AihwMhEdPresentationsDataSource(release="2099-00", root=ed_data_dir)
    with pytest.raises(RuntimeError, match="not in the hardcoded URL registry"):
        _ = ds.resolved_release


# ---- mapping attachment guard --------------------------------------------


def test_load_without_mapping_raises(ed_data_dir: Path) -> None:
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    with pytest.raises(RuntimeError, match="attach_sa2_to_sa4_mapping"):
        _ = ds.load()


def test_attach_mapping_rejects_non_dict(ed_data_dir: Path) -> None:
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    with pytest.raises(TypeError, match="dict"):
        ds.attach_sa2_to_sa4_mapping(("not", "a", "dict"))


# ---- end-to-end load + downscale ----------------------------------------


@responses.activate
def test_load_downscales_and_filters(ed_data_dir: Path) -> None:
    rows = [
        *_full_sa4_rows("SA4101", number=4200, rate=180.0),
        *_full_sa4_rows("SA4201", number=6100, rate=150.0),
        # PHN row — ignored.
        ("2023–24", "Mental health-related presentations", "PHN", "PHN101", "Number", 99999),
        # "All presentations" rows for SA4101 — must be filtered out
        # (only MH-related is the headline).
        ("2023–24", "All presentations", "SA4", "SA4101", "Number", 50000),
        # Earlier-FY rows — filtered out (release is 2023-24).
        *_full_sa4_rows("SA4101", fy="2021–22", number=1),
    ]
    responses.add(responses.GET, _2023_24_URL, body=_make_ed_zip(rows=rows), status=200)

    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "101011002": "101", "206011117": "201"})
    df = ds.load()

    assert set(df.index) == {"101011001", "101011002", "206011117"}
    # Both SA4-101 SA2s get the MH-related 2023-24 Number (4200), not the
    # All-presentations 50000 nor the 2021-22 value.
    assert df.loc["101011001", "mh_ed_presentations_count"] == 4200
    assert df.loc["101011002", "mh_ed_presentations_count"] == 4200
    assert df.loc["101011001", "mh_ed_presentations_per_10000"] == 180.0
    assert df.loc["206011117", "mh_ed_presentations_count"] == 6100
    assert (df["reference_financial_year"] == "2023-24").all()


@responses.activate
def test_load_handles_endash_subdir_member(ed_data_dir: Path) -> None:
    """The member lives under an en-dash subdir; matching is by the
    PHN_SA4 substring, so the en-dash path must still resolve.
    """
    rows = _full_sa4_rows("SA4101", number=77)
    responses.add(responses.GET, _2023_24_URL, body=_make_ed_zip(rows=rows), status=200)
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    df = ds.load()
    assert df.loc["101011001", "mh_ed_presentations_count"] == 77


@responses.activate
def test_load_missing_sa4_emits_null(ed_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4101")
    responses.add(responses.GET, _2023_24_URL, body=_make_ed_zip(rows=rows), status=200)
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101", "997999999": "997"})
    df = ds.load()
    assert df.loc["101011001", "mh_ed_presentations_count"] == 4200
    assert pd.isna(df.loc["997999999", "mh_ed_presentations_count"])


@responses.activate
def test_load_raises_when_no_mh_rows(ed_data_dir: Path) -> None:
    # Only "All presentations" — no MH-related rows → loud failure.
    rows = _full_sa4_rows("SA4101", ptype="All presentations")
    responses.add(responses.GET, _2023_24_URL, body=_make_ed_zip(rows=rows), status=200)
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="no SA4"):
        _ = ds.load()


@responses.activate
def test_load_raises_when_csv_missing(ed_data_dir: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("subdir/wrong_name.csv", b"FinancialYear\n2023-24\n")
    responses.add(responses.GET, _2023_24_URL, body=buf.getvalue(), status=200)
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping({"101011001": "101"})
    with pytest.raises(RuntimeError, match="missing the PHN_SA4 CSV"):
        _ = ds.load()


@responses.activate
def test_parquet_cache_round_trip(ed_data_dir: Path) -> None:
    rows = _full_sa4_rows("SA4101", number=4242)
    responses.add(responses.GET, _2023_24_URL, body=_make_ed_zip(rows=rows), status=200)
    mapping = {"101011001": "101", "101011002": "101"}
    ds = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds.attach_sa2_to_sa4_mapping(mapping)
    df1 = ds.load()
    ds2 = AihwMhEdPresentationsDataSource(root=ed_data_dir)
    ds2.attach_sa2_to_sa4_mapping(mapping)
    df2 = ds2.load()
    pd.testing.assert_frame_equal(df1.sort_index(), df2.sort_index())
