"""AIHW Mental Health Admitted Patient Care fetcher (spec §20, dataset
id ``aihw_mh_admitted_patients``).

Sibling to :class:`AihwMhPrescriptionsDataSource` in ``_aihw_mh`` — same
AIHW NMHSPF "Regional activity data" source family, same SA4 → SA2
downscale pattern. AIHW publishes mental-health admitted-patient-care
activity at **SA4** level (89 SA4s nationally); the augmentor downscales
to SA2 via the boundary file's ``SA4_CODE21`` attribute (see
``spec.md`` §20.7 Strategy 1). Every SA2 inside SA4 X inherits SA4 X's
value unchanged — the honest "no within-parent variation" contract.

Real-data findings (live-probed 2026-06-05) vs the MH-Prescriptions ZIP:

- The member CSV is **UTF-8**, NOT cp1252 like the prescriptions file.
  Different files in the same AIHW source family use different
  encodings, so the encoding is per-dataset (don't share the constant).
- There is **no ``FinancialYear`` column** — the ZIP is a single-year
  (2023-24) publication, so the release id is fixed and no FY filter
  applies. (The prescriptions file carried 10 FYs in one CSV.)
- The headline filter dimension is **``SeparationType == "Total"``**
  (other values: ``Same day``, ``Overnight``), not the
  ``Demographic``/``DemographicCategory`` of the prescriptions file.
- SA4 codes use the same ``SA4101`` prefix form as prescriptions
  (strip ``^SA4`` to match the boundary's bare 3-digit ``SA4_CODE21``).
- Columns: ``Jurisdiction, GeographicAreaType, GeographicAreaCode,
  GeographicAreaName, SeparationType, Measure, Value``.

URL discovery: AIHW's getmedia URLs use opaque UUIDs per release.
Hardcoded; when AIHW publishes a new release, add the UUID here.

Cross-level downscale requires the SA2 → SA4 mapping attached before
``load()`` via ``attach_sa2_to_sa4_mapping`` — ``Pipeline.from_config``
wires this from the boundary GDF.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pandas as pd
import requests

from .._http_retry import retry_stream_get

_log = logging.getLogger(__name__)

# AIHW getmedia URLs use opaque UUIDs that are stable per release.
# New annual releases need an entry added here. Discovery is via the
# NMHSPF "Regional activity data" page (link in the spec markdown).
_AIHW_APC_URLS_BY_RELEASE: dict[str, str] = {
    "2023-24": (
        "https://www.aihw.gov.au/getmedia/"
        "1ed521e7-7ee2-4dc0-98a4-d4f0bd0b027d/"
        "Admitted-patient-care-state-and-territory-2023-24-data-files.zip"
    ),
}

# Map from the AIHW Measure label (verbatim from the real CSV) to the
# augmentor's snake_case column. Order is the column order the parser
# produces. Four metrics, each with a count + a per-10,000-population
# rate twin = 8 columns.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Hospitalisations": "mh_hospitalisations_count",
    "Patient days": "mh_patient_days_count",
    "Psychiatric care days": "mh_psychiatric_care_days_count",
    "Procedures": "mh_procedures_count",
    "Hospitalisations per 10,000 population": "mh_hospitalisations_per_10000",
    "Patient days per 10,000 population": "mh_patient_days_per_10000",
    "Psychiatric care days per 10,000 population": "mh_psychiatric_care_days_per_10000",
    "Procedures per 10,000 population": "mh_procedures_per_10000",
}

# The four count columns coerce to nullable Int64; the rate twins stay float.
_COUNT_COLUMNS: tuple[str, ...] = (
    "mh_hospitalisations_count",
    "mh_patient_days_count",
    "mh_psychiatric_care_days_count",
    "mh_procedures_count",
)


class AihwMhAdmittedPatientsDataSource:
    """Fetch + load AIHW NMHSPF mental-health admitted-patient-care data.

    Implements the :class:`DatasetFetcher` Protocol. SA4-native data
    downscaled to SA2 via a boundary-derived ``SA2 -> SA4`` mapping that
    callers attach before ``load()``.

    Args:
        release: Financial year (e.g. ``"2023-24"``) or ``"latest"``.
        root: Cache directory for the downloaded ZIP + parquet sidecar.
        session: Optional ``requests.Session`` (tests pass a hermetic one).
        chunk_size / timeout: As per the other ABS/AIHW fetchers.

    Use ``attach_sa2_to_sa4_mapping()`` before ``load()``. Without it,
    ``load()`` raises a clear error explaining how to attach one.
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
        :func:`census_augment.spatial.compute_sa2_parent_codes`. The SA4
        codes must be the bare 3-digit form (``"101"``); the parser
        strips the AIHW ``SA4`` prefix before joining.
        """
        if not isinstance(mapping, dict):
            raise TypeError(
                f"attach_sa2_to_sa4_mapping expects a dict[str, str]; got {type(mapping).__name__}"
            )
        self._sa2_to_sa4 = dict(mapping)
        _log.debug(
            "AihwMhAdmittedPatientsDataSource: attached %d SA2 -> SA4 mappings",
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
        return self._root.exists() and any(self._root.glob("aihw-mh-apc-*.zip"))

    @property
    def _zip_path(self) -> Path:
        return self._root / f"aihw-mh-apc-{self.resolved_release}.zip"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"aihw-mh-apc-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the AIHW APC ZIP for the resolved release."""
        self._resolve_release()
        if self._zip_path.exists() and not refresh:
            _log.debug("AIHW MH APC cached at %s", self._zip_path)
            return self._zip_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._zip_path.with_suffix(self._zip_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info("Downloading AIHW MH APC (%s) from %s", self.resolved_release, url)
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label="AIHW MH APC",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._zip_path)
        _log.info("Saved AIHW MH APC to %s", self._zip_path)
        return self._zip_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021`` with one row
        per SA2 (downscaled from the SA4-level source).

        Requires :meth:`attach_sa2_to_sa4_mapping` first — without it,
        raises a clear ``RuntimeError`` since SA4-keyed output isn't
        useful in the rest of the pipeline.
        """
        if self._sa2_to_sa4 is None:
            raise RuntimeError(
                "AihwMhAdmittedPatientsDataSource.load() requires a SA2 -> SA4 "
                "mapping to be attached first. Call "
                "`attach_sa2_to_sa4_mapping(mapping)` with the lookup dict "
                "from `census_augment.spatial.compute_sa2_parent_codes("
                "boundaries)['SA4']`. Pipeline.from_config wires this "
                "automatically from the boundary GDF."
            )

        if self._resolved_release is not None and self._parquet_path.exists():
            df = pd.read_parquet(self._parquet_path)
            if "sa2_code_2021" in df.columns:
                return df.set_index("sa2_code_2021")

        zip_path = self.fetch()
        sa4_df = self._parse_zip(zip_path)

        # Cross-level downscale: every SA2 inherits its SA4's row values.
        records: list[dict[str, object]] = []
        for sa2_code, sa4_code in self._sa2_to_sa4.items():
            if sa4_code not in sa4_df.index:
                # The boundary may carry SA4 codes AIHW didn't publish for
                # (rare pseudo-SA4s). Emit nulls rather than dropping the
                # SA2 so the join with other datasets stays well-formed.
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
        out.to_parquet(self._parquet_path, index=False)
        return out.set_index("sa2_code_2021")

    # ---- release resolution -------------------------------------------

    def _resolve_release(self) -> None:
        if self._resolved_release is not None:
            return

        if self._release_request == "latest":
            picked = max(_AIHW_APC_URLS_BY_RELEASE)
        elif self._release_request in _AIHW_APC_URLS_BY_RELEASE:
            picked = self._release_request
        else:
            raise RuntimeError(
                f"AIHW MH APC release {self._release_request!r} not in the "
                f"hardcoded URL registry. Available: "
                f"{sorted(_AIHW_APC_URLS_BY_RELEASE)}. AIHW uses opaque "
                f"getmedia UUIDs; new releases need to be added to "
                f"_AIHW_APC_URLS_BY_RELEASE in "
                f"src/census_augment/datasets/_aihw_apc.py."
            )
        self._resolved_release = picked
        self._resolved_url = _AIHW_APC_URLS_BY_RELEASE[picked]
        _log.info(
            "Resolved AIHW MH APC release=%s, url=%s",
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_zip(zip_path: Path) -> pd.DataFrame:
        """Parse the AIHW APC ZIP and return a DataFrame indexed by bare
        SA4 code (e.g. ``"101"``) with one column per measure.

        Real-data layout (live-probed 2026-06-05):
        - Member CSV ``Admitted patient care state and territory PHN_SA4
          2023-24.csv`` — **UTF-8** (not cp1252).
        - Filter ``GeographicAreaType == "SA4"`` and
          ``SeparationType == "Total"`` for the headline values.
        - SA4 codes carry an ``SA4`` prefix (``"SA4101"``) — stripped to
          match the boundary's bare ``SA4_CODE21``.
        """
        with zipfile.ZipFile(zip_path) as zf:
            # Match the PHN_SA4 member specifically — the ZIP also has a
            # "Common Procedures" CSV and two metadata XLSX we don't want.
            csv_name = None
            for name in zf.namelist():
                low = name.lower()
                if "phn_sa4" in low and low.endswith(".csv"):
                    csv_name = name
                    break
            if csv_name is None:
                raise RuntimeError(
                    f"AIHW MH APC ZIP at {zip_path} is missing the PHN_SA4 CSV "
                    f"(looked for *PHN_SA4*.csv). Contents: {zf.namelist()}"
                )
            with zf.open(csv_name) as f:
                # UTF-8 (real-data finding) — NOT cp1252 like the
                # prescriptions sibling. Mixed encodings across the AIHW
                # source family, so the encoding is per-dataset.
                raw = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8"))

        required_cols = {
            "GeographicAreaType",
            "SeparationType",
            "GeographicAreaCode",
            "Measure",
            "Value",
        }
        missing = required_cols - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"AIHW MH APC CSV in {zip_path} is missing expected columns "
                f"{sorted(missing)}; got {list(raw.columns)}. Upstream schema "
                f"may have changed — re-probe with tools/probe_new_datasets.py."
            )

        # Filter to SA4 rows with the "Total" separation type (headline).
        filt = (raw["GeographicAreaType"] == "SA4") & (raw["SeparationType"] == "Total")
        slice_df = raw.loc[filt].copy()
        if slice_df.empty:
            sep_values = sorted(raw.get("SeparationType", pd.Series(dtype=str)).unique())
            raise RuntimeError(
                f"AIHW MH APC CSV in {zip_path} has no SA4/Total rows. "
                f"SeparationType values seen: {sep_values}; "
                f"GeographicAreaType values: "
                f"{sorted(raw['GeographicAreaType'].unique())}."
            )

        # Strip the "SA4" prefix to match the boundary's bare SA4_CODE21.
        slice_df["sa4_code"] = (
            slice_df["GeographicAreaCode"].astype(str).str.replace(r"^SA4", "", regex=True)
        )

        pivoted = slice_df.pivot_table(
            index="sa4_code",
            columns="Measure",
            values="Value",
            aggfunc="first",
        )

        unknown_measures = set(pivoted.columns) - set(_MEASURE_TO_COLUMN)
        if unknown_measures:
            _log.warning(
                "AIHW MH APC CSV contains unrecognised Measure labels %r; "
                "these will be dropped. Update _MEASURE_TO_COLUMN in "
                "_aihw_apc.py if AIHW added a new metric we want to surface.",
                sorted(unknown_measures),
            )
        renamed = pivoted.rename(columns=_MEASURE_TO_COLUMN)
        renamed = renamed.reindex(columns=list(_MEASURE_TO_COLUMN.values()))

        # Counts -> nullable Int64; rates stay float.
        for count_col in _COUNT_COLUMNS:
            if count_col in renamed.columns:
                renamed[count_col] = pd.to_numeric(renamed[count_col], errors="coerce").astype(
                    "Int64"
                )

        return renamed


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhAdmittedPatientsDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhAdmittedPatientsDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_admitted_patients", _build_fetcher)


_register()
