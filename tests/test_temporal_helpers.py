"""Tests for census_augment._temporal — release windows + resolution rules.

Verifies the spec-temporal.md §9 behaviour: per-`cover_basis` window
math, per-rule release resolution, out-of-range handling. Hermetic;
no real ABS data touched.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from census_augment._temporal import (
    OutOfRangeDateError,
    release_window,
    resolve_gnaf_release,
    resolve_release,
    to_date,
)
from census_augment.datasets._spec import TemporalDatasetMetadata


# ---- release_window ------------------------------------------------------


def test_window_census_reference_date() -> None:
    w = release_window("2021", cover_basis="census_reference_date")
    assert w.start == date(2021, 8, 1)
    assert w.end == date(2021, 8, 1)
    assert w.midpoint == date(2021, 8, 1)


def test_window_financial_year_ending_yyyy_yy() -> None:
    w = release_window("2022-23", cover_basis="financial_year_ending")
    assert w.start == date(2022, 7, 1)
    assert w.end == date(2023, 6, 30)


def test_window_financial_year_ending_yyyy() -> None:
    """The single-year form: ``"2024"`` → FY ending 2024."""
    w = release_window("2024", cover_basis="financial_year_ending")
    assert w.start == date(2023, 7, 1)
    assert w.end == date(2024, 6, 30)


def test_window_calendar_year_ending() -> None:
    w = release_window("2023", cover_basis="calendar_year_ending")
    assert w.start == date(2023, 1, 1)
    assert w.end == date(2023, 12, 31)


@pytest.mark.parametrize(
    "release_id,expected_start,expected_end",
    [
        ("2024-Q1", date(2024, 1, 1), date(2024, 3, 31)),
        ("2024-Q2", date(2024, 4, 1), date(2024, 6, 30)),
        ("2024-Q3", date(2024, 7, 1), date(2024, 9, 30)),
        ("2024-Q4", date(2024, 10, 1), date(2024, 12, 31)),
    ],
)
def test_window_quarter_ending(release_id: str, expected_start: date, expected_end: date) -> None:
    w = release_window(release_id, cover_basis="quarter_ending")
    assert w.start == expected_start
    assert w.end == expected_end


def test_window_quarter_ending_rejects_invalid_quarter() -> None:
    with pytest.raises(ValueError, match="quarter must be 1-4"):
        release_window("2024-Q5", cover_basis="quarter_ending")


def test_window_rejects_malformed_id() -> None:
    with pytest.raises(ValueError, match="must be YYYY-Qn"):
        release_window("foobar", cover_basis="quarter_ending")


# ---- resolve_release: closest_at_or_before ------------------------------


def _erp_metadata() -> TemporalDatasetMetadata:
    return TemporalDatasetMetadata(
        cadence="annual",
        cover_basis="financial_year_ending",
        release_id_format="YYYY (year ending 30 Jun)",
        available_releases=["2018", "2019", "2020", "2021", "2022", "2023", "2024"],
        asgs_edition_by_release={
            "2018": 2,
            "2019": 2,
            "2020": 2,
            "2021": 2,
            "2022": 3,
            "2023": 3,
            "2024": 3,
        },
    )


def test_resolve_closest_at_or_before_typical() -> None:
    """Row dated mid-2022: closest-at-or-before resolves to the 2022 release."""
    md = _erp_metadata()
    out = resolve_release(
        date(2022, 9, 1),
        metadata=md,
        rule="closest_at_or_before",
    )
    # 2022 release covers 2021-07-01 through 2022-06-30; window start <= 2022-09-01? No.
    # 2023 release covers 2022-07-01 through 2023-06-30; window start (2022-07-01) <= 2022-09-01: yes.
    # So 2023 is the most recent at-or-before.
    assert out == "2023"


def test_resolve_closest_at_or_before_boundary() -> None:
    """Row exactly on a window boundary picks that window."""
    md = _erp_metadata()
    out = resolve_release(
        date(2022, 7, 1),  # exactly the start of the 2023 release's window
        metadata=md,
        rule="closest_at_or_before",
    )
    assert out == "2023"


def test_resolve_closest_at_or_before_out_of_range_fail() -> None:
    """Row dated before any release fails by default."""
    md = _erp_metadata()
    with pytest.raises(OutOfRangeDateError):
        resolve_release(
            date(2010, 1, 1),
            metadata=md,
            rule="closest_at_or_before",
            out_of_range="fail",
        )


def test_resolve_closest_at_or_before_out_of_range_nearest() -> None:
    """`out_of_range="nearest"` clamps to the earliest release."""
    md = _erp_metadata()
    out = resolve_release(
        date(2010, 1, 1),
        metadata=md,
        rule="closest_at_or_before",
        out_of_range="nearest",
    )
    assert out == "2018"  # earliest available


# ---- resolve_release: closest -------------------------------------------


def test_resolve_closest_picks_nearest_midpoint() -> None:
    """`closest` rule picks the release whose midpoint is nearest the date."""
    md = TemporalDatasetMetadata(
        cadence="quarterly",
        cover_basis="quarter_ending",
        release_id_format="YYYY-Qn",
        available_releases=["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"],
        asgs_edition_by_release={
            "2024-Q1": 3,
            "2024-Q2": 3,
            "2024-Q3": 3,
            "2024-Q4": 3,
        },
    )
    # Date 1 Apr is the start of Q2 → closest midpoint is Q2's (mid-May)
    out = resolve_release(date(2024, 4, 1), metadata=md, rule="closest")
    assert out == "2024-Q2"


def test_resolve_closest_can_pick_future_release() -> None:
    """`closest` doesn't restrict to at-or-before, so dates near
    a release's *start* may resolve to that release even if it
    technically hasn't been published at row_date."""
    md = TemporalDatasetMetadata(
        cadence="quarterly",
        cover_basis="quarter_ending",
        release_id_format="YYYY-Qn",
        available_releases=["2024-Q1", "2024-Q2"],
        asgs_edition_by_release={"2024-Q1": 3, "2024-Q2": 3},
    )
    # Row date 2024-03-15 is in Q1 window. closest_at_or_before:
    # would pick Q1. closest: Q1 midpoint (~Feb 14) vs Q2 midpoint
    # (~mid-May). Distance from 2024-03-15: ~29 days vs ~60 days.
    # So closest picks Q1 here.
    out = resolve_release(date(2024, 3, 15), metadata=md, rule="closest")
    assert out == "2024-Q1"


# ---- to_date -------------------------------------------------------------


def test_to_date_from_date() -> None:
    assert to_date(date(2024, 1, 1)) == date(2024, 1, 1)


def test_to_date_from_datetime() -> None:
    assert to_date(datetime(2024, 1, 1, 12, 30)) == date(2024, 1, 1)


def test_to_date_from_iso_string() -> None:
    assert to_date("2024-01-01") == date(2024, 1, 1)


def test_to_date_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="Cannot coerce"):
        to_date(42)


def test_to_date_handles_pandas_timestamp() -> None:
    """pandas.Timestamp duck-types as datetime — to_date handles it."""
    import pandas as pd

    out = to_date(pd.Timestamp("2024-03-15"))
    assert out == date(2024, 3, 15)


# ---- OutOfRangeDateError surface ----------------------------------------


def test_out_of_range_error_carries_context() -> None:
    """The error captures dataset id, row date, earliest release,
    and row index for the caller to surface to the user."""
    md = _erp_metadata()
    with pytest.raises(OutOfRangeDateError) as exc_info:
        resolve_release(
            date(2010, 1, 1),
            metadata=md,
            rule="closest_at_or_before",
            out_of_range="fail",
            dataset_id="erp_by_sa2",
            row_index=42,
        )
    e = exc_info.value
    assert e.dataset_id == "erp_by_sa2"
    assert e.row_date == date(2010, 1, 1)
    assert e.earliest_release == "2018"
    assert e.row_index == 42
    assert "2018" in str(e)
    assert "42" in str(e)


# ---- resolve_gnaf_release (Phase G) --------------------------------------


_GNAF_RELEASES_SAMPLE = [
    "202003",
    "202006",
    "202009",
    "202012",
    "202103",
    "202106",
    "202109",
    "202112",
    "202203",
    "202206",
    "202209",
    "202212",
    "202303",
    "202306",
    "202309",
    "202312",
    "202403",
    "202406",
]


def test_resolve_gnaf_release_closest_at_or_before_typical() -> None:
    """Mid-2022 date → latest at-or-before release is 202206."""
    out = resolve_gnaf_release(
        date(2022, 7, 15),
        available_releases=_GNAF_RELEASES_SAMPLE,
        rule="closest_at_or_before",
    )
    assert out == "202206"


def test_resolve_gnaf_release_closest_at_or_before_boundary() -> None:
    """Row date exactly on a release's nominal publication day picks
    that release (start-of-month convention)."""
    out = resolve_gnaf_release(
        date(2022, 6, 1),
        available_releases=_GNAF_RELEASES_SAMPLE,
        rule="closest_at_or_before",
    )
    assert out == "202206"


def test_resolve_gnaf_release_closest_picks_nearest() -> None:
    """`closest` can pick a release published AFTER the row date when
    it's closer in days than the latest at-or-before."""
    # Row date 2022-05-25: 202206 is +7 days; 202203 is -85 days.
    out = resolve_gnaf_release(
        date(2022, 5, 25),
        available_releases=_GNAF_RELEASES_SAMPLE,
        rule="closest",
    )
    assert out == "202206"


def test_resolve_gnaf_release_out_of_range_fail_default() -> None:
    """Pre-earliest dates raise without explicit ``out_of_range``."""
    with pytest.raises(OutOfRangeDateError) as exc_info:
        resolve_gnaf_release(
            date(2015, 1, 1),
            available_releases=_GNAF_RELEASES_SAMPLE,
            rule="closest_at_or_before",
            row_index=7,
        )
    e = exc_info.value
    assert e.dataset_id == "<gnaf>"
    assert e.earliest_release == "202003"
    assert e.row_index == 7


def test_resolve_gnaf_release_out_of_range_nearest_clamps() -> None:
    """`out_of_range='nearest'` clamps to the earliest release."""
    out = resolve_gnaf_release(
        date(2015, 1, 1),
        available_releases=_GNAF_RELEASES_SAMPLE,
        rule="closest_at_or_before",
        out_of_range="nearest",
    )
    assert out == "202003"


def test_resolve_gnaf_release_empty_releases_raises() -> None:
    """No releases at all → OutOfRangeDateError with earliest=None."""
    with pytest.raises(OutOfRangeDateError) as exc_info:
        resolve_gnaf_release(
            date(2024, 1, 1),
            available_releases=[],
            rule="closest_at_or_before",
        )
    assert exc_info.value.earliest_release is None


def test_resolve_gnaf_release_malformed_id_raises() -> None:
    """Non-YYYYMM entries in the release list raise loudly."""
    with pytest.raises(ValueError, match="6-digit YYYYMM"):
        resolve_gnaf_release(
            date(2024, 1, 1),
            available_releases=["2024-Q1"],
            rule="closest_at_or_before",
        )


def test_resolve_gnaf_release_month_out_of_range_raises() -> None:
    """Month=00 / Month=13 entries raise loudly."""
    with pytest.raises(ValueError, match="month must be 01-12"):
        resolve_gnaf_release(
            date(2024, 1, 1),
            available_releases=["202413"],
            rule="closest_at_or_before",
        )
