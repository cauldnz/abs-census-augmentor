"""Temporal-mode helpers: release-window math and per-row release resolution.

Implements `spec-temporal.md` §9 — given a row's date and a dataset's
`TemporalDatasetMetadata`, picks the right release per the configured
resolution rule.

Two responsibilities:

1. Coverage windows. Each `cover_basis` value maps a release id to a
   (start_date, end_date) window. ``2022-23`` (financial_year_ending)
   → 2022-07-01 through 2023-06-30. ``2024-Q3`` (quarter_ending) →
   2024-07-01 through 2024-09-30. ``2021`` (census_reference_date) →
   2021-08-10 (the Census reference date, treated as an instant).

2. Resolution. Two rules:
   - ``closest_at_or_before`` — most recent release whose window start
     is ≤ row date. Causally correct for "as-of" analysis.
   - ``closest`` — release whose window midpoint is nearest row date.

   Out-of-range behaviour: when no release satisfies the rule,
   ``fail`` raises `OutOfRangeDateError` per the first affected row;
   ``nearest`` clamps to the earliest available release with a WARNING.

Cross-references:
- `spec-temporal.md` §2 — the boundary-correctness invariant.
- `spec-temporal.md` §9.1 — resolution rule semantics.
- `datasets._spec.TemporalDatasetMetadata` — the per-dataset metadata
  this module operates on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from .datasets._spec import TemporalDatasetMetadata

_log = logging.getLogger(__name__)


class OutOfRangeDateError(ValueError):
    """Raised when a row's date predates every release of a touched dataset.

    Carries enough context for the caller to surface a useful message:
    the dataset id, the row date, and the earliest release available.
    """

    def __init__(
        self,
        *,
        dataset_id: str,
        row_date: date,
        earliest_release: str | None,
        row_index: int | str | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.row_date = row_date
        self.earliest_release = earliest_release
        self.row_index = row_index
        if earliest_release is None:
            msg = (
                f"Dataset {dataset_id!r} has no available_releases declared in its "
                f"temporal: block; cannot resolve release for date {row_date}"
            )
        else:
            row_loc = f" (row {row_index})" if row_index is not None else ""
            msg = (
                f"Row date {row_date}{row_loc} predates the earliest available "
                f"release of dataset {dataset_id!r} ({earliest_release!r}). "
                f"Either pre-filter rows or set `temporal.out_of_range: nearest`."
            )
        super().__init__(msg)


@dataclass(frozen=True)
class ReleaseWindow:
    """Coverage window for one release.

    ``start`` is inclusive; ``end`` is inclusive. For instant-coverage
    releases (Census reference date) ``start == end``.
    """

    release_id: str
    start: date
    end: date

    @property
    def midpoint(self) -> date:
        # Midpoint by ordinal-day arithmetic — avoids timedelta-with-date
        # type-narrowing fiddliness.
        return date.fromordinal((self.start.toordinal() + self.end.toordinal()) // 2)


def release_window(
    release_id: str,
    cover_basis: Literal[
        "census_reference_date",
        "financial_year_ending",
        "calendar_year_ending",
        "quarter_ending",
    ],
) -> ReleaseWindow:
    """Compute the coverage window for ``release_id`` given its basis.

    ``release_id`` formats by basis:

    - ``census_reference_date``: ``"YYYY"`` (Census year). Window is the
      single Census reference date (1 Aug for 2011, 9 Aug for 2016,
      10 Aug for 2021 — but we use 1 Aug across all to keep this
      math simple; the difference is at most a few days and doesn't
      affect resolution rules in practice).
    - ``financial_year_ending``: ``"YYYY-YY"`` (e.g. ``"2022-23"``) or
      ``"YYYY"`` (year ending 30 Jun). Window: 1 Jul prior calendar
      year through 30 Jun release year.
    - ``calendar_year_ending``: ``"YYYY"``. Window: 1 Jan through
      31 Dec of that year.
    - ``quarter_ending``: ``"YYYY-Qn"`` (e.g. ``"2024-Q3"``). Window:
      the three calendar months ending at the quarter date.

    Raises ``ValueError`` on a malformed release id.
    """
    rid = release_id.strip()
    if cover_basis == "census_reference_date":
        # Census year, e.g. "2021"
        try:
            year = int(rid)
        except ValueError as e:
            raise ValueError(
                f"release_id {rid!r} for cover_basis=census_reference_date must be YYYY"
            ) from e
        # 1 Aug per the simplification noted above.
        day = date(year, 8, 1)
        return ReleaseWindow(release_id=rid, start=day, end=day)

    if cover_basis == "financial_year_ending":
        # Two flavours: "2022-23" or "2022".
        if "-" in rid:
            prefix, suffix = rid.split("-", 1)
            try:
                start_year = int(prefix)
                # suffix is two digits (the ending FY's last two)
                end_year = int(suffix) + (start_year // 100) * 100
                if end_year < start_year:
                    # century rollover handling
                    end_year += 100
            except ValueError as e:
                raise ValueError(
                    f"release_id {rid!r} for cover_basis=financial_year_ending "
                    f"must be 'YYYY-YY' or 'YYYY'"
                ) from e
        else:
            try:
                end_year = int(rid)
                start_year = end_year - 1
            except ValueError as e:
                raise ValueError(
                    f"release_id {rid!r} for cover_basis=financial_year_ending "
                    f"must be 'YYYY-YY' or 'YYYY'"
                ) from e
        return ReleaseWindow(
            release_id=rid,
            start=date(start_year, 7, 1),
            end=date(end_year, 6, 30),
        )

    if cover_basis == "calendar_year_ending":
        try:
            year = int(rid)
        except ValueError as e:
            raise ValueError(
                f"release_id {rid!r} for cover_basis=calendar_year_ending must be YYYY"
            ) from e
        return ReleaseWindow(
            release_id=rid,
            start=date(year, 1, 1),
            end=date(year, 12, 31),
        )

    if cover_basis == "quarter_ending":
        # e.g. "2024-Q3"
        if "-Q" not in rid:
            raise ValueError(f"release_id {rid!r} for cover_basis=quarter_ending must be YYYY-Qn")
        year_part, q_part = rid.split("-Q", 1)
        try:
            year = int(year_part)
            quarter = int(q_part)
        except ValueError as e:
            raise ValueError(
                f"release_id {rid!r} for cover_basis=quarter_ending must be YYYY-Qn"
            ) from e
        if quarter < 1 or quarter > 4:
            raise ValueError(f"release_id {rid!r}: quarter must be 1-4 (got Q{quarter})")
        end_month = quarter * 3
        # Quarter end dates: Q1=Mar 31, Q2=Jun 30, Q3=Sep 30, Q4=Dec 31.
        end_day = (31, 30, 30, 31)[quarter - 1]
        start_month = end_month - 2
        return ReleaseWindow(
            release_id=rid,
            start=date(year, start_month, 1),
            end=date(year, end_month, end_day),
        )

    raise ValueError(f"Unknown cover_basis: {cover_basis!r}")  # pragma: no cover


def resolve_release(
    row_date: date,
    *,
    metadata: TemporalDatasetMetadata,
    rule: Literal["closest_at_or_before", "closest"],
    out_of_range: Literal["fail", "nearest"] = "fail",
    dataset_id: str = "<unknown>",
    row_index: int | str | None = None,
) -> str:
    """Pick the release for ``row_date`` per ``rule``.

    Raises :class:`OutOfRangeDateError` when ``out_of_range="fail"`` and
    no release covers / precedes the row's date. Returns the earliest
    available release with a WARNING when ``out_of_range="nearest"``.
    """
    available = metadata.available_releases
    if not available:
        raise OutOfRangeDateError(
            dataset_id=dataset_id,
            row_date=row_date,
            earliest_release=None,
            row_index=row_index,
        )

    windows: list[ReleaseWindow] = [release_window(rid, metadata.cover_basis) for rid in available]
    # Sort by window start for stable behaviour.
    windows.sort(key=lambda w: w.start)

    if rule == "closest_at_or_before":
        eligible = [w for w in windows if w.start <= row_date]
        if not eligible:
            if out_of_range == "nearest":
                _log.warning(
                    "Row %s date %s predates earliest %s release %r; clamping to nearest",
                    row_index,
                    row_date,
                    dataset_id,
                    windows[0].release_id,
                )
                return windows[0].release_id
            raise OutOfRangeDateError(
                dataset_id=dataset_id,
                row_date=row_date,
                earliest_release=windows[0].release_id,
                row_index=row_index,
            )
        # Most recent eligible release — i.e. max window start.
        return max(eligible, key=lambda w: w.start).release_id

    if rule == "closest":
        # Distance to midpoint (instant releases have start == end so
        # midpoint is the instant itself).
        def distance(w: ReleaseWindow) -> int:
            mp = w.midpoint
            return abs((row_date - mp).days)

        return min(windows, key=distance).release_id

    raise ValueError(f"Unknown resolution rule: {rule!r}")  # pragma: no cover


def resolve_gnaf_release(
    row_date: date,
    *,
    available_releases: list[str],
    rule: Literal["closest_at_or_before", "closest"],
    out_of_range: Literal["fail", "nearest"] = "fail",
    row_index: int | str | None = None,
) -> str:
    """Pick a G-NAF release for ``row_date`` per ``rule`` (Phase G).

    G-NAF releases are ``YYYYMM`` strings (e.g. ``"202602"``). The
    implied coverage rule: a release published in ``YYYYMM`` reflects
    the address corpus *as of* the first of that month. Treat each
    release as an instant on that publication date — older addresses
    appear and retired addresses persist in releases published before
    a row's date.

    Mirrors :func:`resolve_release` for the dataset case but doesn't
    need a :class:`TemporalDatasetMetadata`: G-NAF's available
    releases come from
    :meth:`census_augment.data_sources.gnaf.GnafDataSource.list_available_releases`,
    not from a spec markdown block.

    Raises :class:`OutOfRangeDateError` when ``out_of_range="fail"`` and
    no release satisfies the rule. Returns the earliest available
    release with a WARNING when ``out_of_range="nearest"``.
    """
    if not available_releases:
        raise OutOfRangeDateError(
            dataset_id="<gnaf>",
            row_date=row_date,
            earliest_release=None,
            row_index=row_index,
        )

    # Normalise to (release_id, publication_date) and sort.
    indexed: list[tuple[str, date]] = []
    for rid in available_releases:
        if len(rid) != 6 or not rid.isdigit():
            raise ValueError(
                f"G-NAF release id {rid!r} is not 6-digit YYYYMM; "
                f"available_releases must be sorted YYYYMM strings."
            )
        year = int(rid[:4])
        month = int(rid[4:])
        if month < 1 or month > 12:
            raise ValueError(f"G-NAF release id {rid!r}: month must be 01-12")
        indexed.append((rid, date(year, month, 1)))
    indexed.sort(key=lambda pair: pair[1])

    if rule == "closest_at_or_before":
        eligible = [pair for pair in indexed if pair[1] <= row_date]
        if not eligible:
            if out_of_range == "nearest":
                _log.warning(
                    "Row %s date %s predates earliest G-NAF release %r; clamping",
                    row_index,
                    row_date,
                    indexed[0][0],
                )
                return indexed[0][0]
            raise OutOfRangeDateError(
                dataset_id="<gnaf>",
                row_date=row_date,
                earliest_release=indexed[0][0],
                row_index=row_index,
            )
        # Most recent eligible release.
        return max(eligible, key=lambda pair: pair[1])[0]

    if rule == "closest":

        def distance(pair: tuple[str, date]) -> int:
            return abs((row_date - pair[1]).days)

        return min(indexed, key=distance)[0]

    raise ValueError(f"Unknown resolution rule: {rule!r}")  # pragma: no cover


def to_date(value: object) -> date:
    """Coerce one of (date, datetime, pandas-Timestamp, str) to a `date`.

    Used by the pipeline when iterating an input DataFrame's date column.
    Raises ``ValueError`` for unparseable values; the caller surfaces a
    row-indexed error.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    # pandas.Timestamp duck-types as datetime; the above branch catches it.
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    raise ValueError(f"Cannot coerce {value!r} (type {type(value).__name__}) to date")
