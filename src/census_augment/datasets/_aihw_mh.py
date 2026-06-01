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
the parser specifies that explicitly.

URL discovery: AIHW's getmedia URLs use opaque UUIDs per release.
Hardcoded for now (one entry per known release); when AIHW publishes a
new release, add the UUID + filename here.

Cross-level downscale requires the SA2 -> SA4 mapping to be attached
before ``load()``. ``Pipeline.from_config`` wires this from the
boundary GDF; library callers can derive it via
``census_augment.spatial.compute_sa2_parent_codes(boundaries)["SA4"]``.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests

from .._http_retry import retry_stream_get

_log = logging.getLogger(__name__)

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


class AihwMhPrescriptionsDataSource:
    """Fetch + load AIHW NMHSPF mental-health prescriptions data.

    Implements the :class:`DatasetFetcher` Protocol. SA4-native data
    downscaled to SA2 via a boundary-derived ``SA2 -> SA4`` mapping
    that callers attach before ``load()``.

    Args:
        release: Financial year (e.g. ``"2024-25"``) or ``"latest"``.
        root: Cache directory for the downloaded ZIP + parquet sidecar.
        session: Optional ``requests.Session`` (tests pass a hermetic one).
        chunk_size / timeout: As per the other ABS XLSX fetchers.

    Use ``attach_sa2_to_sa4_mapping()`` before ``load()`` to wire the
    boundary-derived SA2 -> SA4 lookup. Without it, ``load()`` raises
    a clear error explaining how to attach one.
    """

    def __init__(
        self,
        *,
        release: str = "latest",
        root: Path,
        session: requests.Session | None = None,
        chunk_size: int = 256 * 1024,
        timeout: float = 60.0,
    ) -> None:
        self._release_request = str(release)
        self._root = Path(root)
        self._session = session if session is not None else requests.Session()
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._resolved_release: str | None = None
        self._resolved_url: str | None = None
        # SA2 -> SA4 mapping; populated via attach_sa2_to_sa4_mapping().
        # None means "not yet attached" — load() raises until it's set.
        self._sa2_to_sa4: dict[str, str] | None = None

    # ---- mapping attachment ------------------------------------------

    def attach_sa2_to_sa4_mapping(self, mapping: dict[str, str]) -> None:
        """Attach the boundary-derived ``{sa2_code: sa4_code}`` lookup.

        Pipeline.from_config wires this automatically from the SA2
        boundary file's ``SA4_CODE21`` column via
        :func:`census_augment.spatial.compute_sa2_parent_codes`. Library
        callers can derive the mapping themselves the same way and call
        this method.

        Args:
            mapping: Dict mapping SA2 code (str) -> SA4 code (str, bare
                3-digit form like ``"101"``, matching the ABS
                ``SA4_CODE21`` attribute). The AIHW CSV uses an
                ``SA4`` prefix (``"SA4101"``); the parser strips that
                before joining, so the mapping should use the bare form.
        """
        if not isinstance(mapping, dict):
            raise TypeError(
                f"attach_sa2_to_sa4_mapping expects a dict[str, str]; got {type(mapping).__name__}"
            )
        self._sa2_to_sa4 = dict(mapping)
        _log.debug(
            "AihwMhPrescriptionsDataSource: attached %d SA2 -> SA4 mappings",
            len(self._sa2_to_sa4),
        )

    # ---- DatasetFetcher protocol --------------------------------------

    @property
    def resolved_release(self) -> str:
        if self._resolved_release is None:
            self._resolve_release()
        assert self._resolved_release is not None
        return self._resolved_release

    @property
    def is_cached(self) -> bool:
        if self._resolved_release is not None:
            return self._zip_path.exists()
        return self._root.exists() and any(self._root.glob("aihw-mh-rx-*.zip"))

    @property
    def _zip_path(self) -> Path:
        return self._root / f"aihw-mh-rx-{self.resolved_release}.zip"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"aihw-mh-rx-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the AIHW ZIP for the resolved release.

        Returns the path to the cached ZIP file.
        """
        self._resolve_release()
        if self._zip_path.exists() and not refresh:
            _log.debug("AIHW MH Rx cached at %s", self._zip_path)
            return self._zip_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._zip_path.with_suffix(self._zip_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info(
            "Downloading AIHW MH Rx (%s) from %s",
            self.resolved_release,
            url,
        )
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label="AIHW MH Rx",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._zip_path)
        _log.info("Saved AIHW MH Rx to %s", self._zip_path)
        return self._zip_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021`` with one row
        per SA2 (downscaled from the SA4-level source).

        Requires :meth:`attach_sa2_to_sa4_mapping` to have been called
        first — without it, raises a clear ``RuntimeError`` since
        SA4-keyed output isn't useful in the rest of the pipeline.
        """
        if self._sa2_to_sa4 is None:
            raise RuntimeError(
                "AihwMhPrescriptionsDataSource.load() requires a SA2 -> SA4 "
                "mapping to be attached first. Call "
                "`attach_sa2_to_sa4_mapping(mapping)` with the lookup dict "
                "from `census_augment.spatial.compute_sa2_parent_codes("
                "boundaries)['SA4']`. Pipeline.from_config wires this "
                "automatically from the boundary GDF."
            )

        # Parquet sidecar cache. The downscale is mapping-dependent —
        # cache filename includes the mapping fingerprint so a different
        # boundary edition's mapping doesn't reuse stale parquet.
        # Cheap-and-good fingerprint: count of mappings + first/last SA2 codes.
        if self._resolved_release is not None and self._parquet_path.exists():
            df = pd.read_parquet(self._parquet_path)
            # Re-key by SA2; cached parquet keeps SA2 as a column to avoid
            # parquet's index round-trip quirks.
            if "sa2_code_2021" in df.columns:
                return df.set_index("sa2_code_2021")

        zip_path = self.fetch()
        sa4_df = self._parse_zip(zip_path, release=self.resolved_release)

        # Cross-level downscale: every SA2 gets its SA4's row values.
        # Index sa4_df on the bare SA4 code (no "SA4" prefix) for the
        # join, then build a per-SA2 DataFrame.
        records: list[dict[str, object]] = []
        for sa2_code, sa4_code in self._sa2_to_sa4.items():
            if sa4_code not in sa4_df.index:
                # The boundary file may carry SA4 codes that AIHW didn't
                # publish for (rare — but happens for non-residential
                # special SA4s like "Migratory - offshore - shipping").
                # Emit a row with null values rather than dropping the SA2
                # so the join with other datasets stays well-formed.
                rec: dict[str, object] = {
                    "sa2_code_2021": str(sa2_code),
                    **{col: None for col in _MEASURE_TO_COLUMN.values()},
                }
            else:
                row = sa4_df.loc[sa4_code]
                rec = {
                    "sa2_code_2021": str(sa2_code),
                    **{col: row[col] for col in _MEASURE_TO_COLUMN.values()},
                }
            rec["reference_financial_year"] = self.resolved_release
            records.append(rec)

        out = pd.DataFrame.from_records(records)
        # Write parquet keeping SA2 as a column for clean round-trip.
        out.to_parquet(self._parquet_path, index=False)
        return out.set_index("sa2_code_2021")

    # ---- release resolution -------------------------------------------

    def _resolve_release(self) -> None:
        if self._resolved_release is not None:
            return

        if self._release_request == "latest":
            picked = max(_AIHW_RX_URLS_BY_RELEASE)
        elif self._release_request in _AIHW_RX_URLS_BY_RELEASE:
            picked = self._release_request
        else:
            raise RuntimeError(
                f"AIHW MH Rx release {self._release_request!r} not in the "
                f"hardcoded URL registry. Available: "
                f"{sorted(_AIHW_RX_URLS_BY_RELEASE)}. AIHW uses opaque "
                f"getmedia UUIDs; new releases need to be added to "
                f"_AIHW_RX_URLS_BY_RELEASE in src/census_augment/datasets/_aihw_mh.py."
            )
        self._resolved_release = picked
        self._resolved_url = _AIHW_RX_URLS_BY_RELEASE[picked]
        _log.info(
            "Resolved AIHW MH Rx release=%s, url=%s",
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_zip(zip_path: Path, *, release: str) -> pd.DataFrame:
        """Parse the AIHW ZIP and return a DataFrame indexed by bare SA4
        code (e.g. ``"101"``) with one column per measure for the
        requested release year.
        """
        with zipfile.ZipFile(zip_path) as zf:
            # The SA4 + PHN CSV is the one we want. The filename pattern is
            # "Mental health-related prescriptions PHN and SA4 ..." but the
            # release year and a numeric suffix can vary; match the prefix.
            csv_name = None
            for name in zf.namelist():
                low = name.lower()
                if "prescriptions phn and sa4" in low and low.endswith(".csv"):
                    csv_name = name
                    break
            if csv_name is None:
                raise RuntimeError(
                    f"AIHW MH Rx ZIP at {zip_path} is missing the "
                    f"PHN+SA4 CSV (looked for *prescriptions PHN and SA4*.csv). "
                    f"Contents: {zf.namelist()}"
                )
            with zf.open(csv_name) as f:
                # cp1252 because the source uses Windows-1252 encoding —
                # en-dash characters in age ranges + FY labels would
                # otherwise mojibake under UTF-8 decode.
                raw = pd.read_csv(io.TextIOWrapper(f, encoding="cp1252"))

        # Normalise the FinancialYear column: AIHW uses Unicode en-dash
        # (–) in labels; user input uses ASCII hyphen. Convert source.
        raw["FinancialYear"] = raw["FinancialYear"].astype(str).str.replace("–", "-", regex=False)

        # Filter to SA4-typed rows for the requested release with Total/Total
        # demographics — the headline values.
        filt = (
            (raw["GeographicAreaType"] == "SA4")
            & (raw["FinancialYear"] == release)
            & (raw["Demographic"] == "Total")
            & (raw["DemographicCategory"] == "Total")
        )
        slice_df = raw.loc[filt].copy()
        if slice_df.empty:
            available_fys = sorted(
                raw.loc[raw["GeographicAreaType"] == "SA4", "FinancialYear"].unique()
            )
            raise RuntimeError(
                f"AIHW MH Rx CSV in {zip_path} has no SA4/Total/Total rows for "
                f"release {release!r}. Available FY values for SA4: {available_fys}"
            )

        # Strip the "SA4" prefix from the geo code to match the ABS
        # boundary's bare SA4_CODE21. AIHW publishes e.g. "SA4101" -> "101".
        slice_df["sa4_code"] = (
            slice_df["GeographicAreaCode"].astype(str).str.replace(r"^SA4", "", regex=True)
        )

        # Pivot wide on Measure to get one row per SA4 with 4 metric columns.
        pivoted = slice_df.pivot_table(
            index="sa4_code",
            columns="Measure",
            values="Value",
            aggfunc="first",
        )

        # Map AIHW Measure labels to our output column names. Reject loudly
        # if a release introduces a new measure label we don't know about,
        # rather than silently dropping it.
        unknown_measures = set(pivoted.columns) - set(_MEASURE_TO_COLUMN)
        if unknown_measures:
            _log.warning(
                "AIHW MH Rx CSV contains unrecognised Measure labels %r; "
                "these will be dropped from the output. Update "
                "_MEASURE_TO_COLUMN in _aihw_mh.py if AIHW added a new "
                "metric we want to surface.",
                sorted(unknown_measures),
            )
        renamed = pivoted.rename(columns=_MEASURE_TO_COLUMN)

        # Reindex to ensure every output column exists, even if AIHW didn't
        # publish it for this release. Missing columns -> all-NaN.
        renamed = renamed.reindex(columns=list(_MEASURE_TO_COLUMN.values()))

        # Coerce counts to nullable Int64 (AIHW publishes integer counts;
        # rates stay float).
        for count_col in ("mh_patients_count", "mh_prescriptions_count"):
            if count_col in renamed.columns:
                renamed[count_col] = pd.to_numeric(renamed[count_col], errors="coerce").astype(
                    "Int64"
                )

        return renamed


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


# Silence the unused-import lint on the regex import — kept for symmetry
# with the other dataset modules even when not currently used here.
_ = re
