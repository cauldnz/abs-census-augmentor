"""AIHW Medicare-subsidised Mental Health services fetcher (spec §20,
dataset id ``aihw_mh_medicare``).

Fourth AIHW NMHSPF dataset. Captures Medicare-subsidised
mental-health-specific services — patients and services under the MBS —
at SA4 level, downscaled to SA2 via the boundary's ``SA4_CODE21``
attribute. Catalogue identifier ``AIHW_MBS``.

Real-data findings (live-probed 2026-06-05) — this dataset has the
fiddliest code/value formatting of the AIHW family, all confirmed
firsthand:

- The CSV is **cp1252**.
- SA4 codes are **hyphenated**: ``SA4-101`` (NOT ``SA4101`` like the
  other AIHW datasets). Strip ``^SA4-`` to match the boundary's bare
  3-digit ``SA4_CODE21``.
- ``ProviderType`` values contain **non-breaking spaces** (U+00A0),
  e.g. ``"All\xa0providers"``. Normalise NBSP → regular space before
  filtering to ``"All providers"`` (the headline; the file also splits
  by Psychiatrists / GPs / Clinical psychologists / etc.).
- Multi-FY file with **en-dash FY labels** (``2024–25``) — normalised +
  filtered to the requested release.
- Columns: ``FinancialYear, GeographicAreaType, GeographicAreaCode,
  phnname, ProviderType, Measure, Value``. Four measures:
  Patients / Services, each + a "rate per 1,000 population" twin.

Cross-level downscale requires the SA2 → SA4 mapping attached before
``load()`` — ``Pipeline.from_config`` wires this, and the enricher
attaches it to any fetcher exposing ``attach_sa2_to_sa4_mapping``.
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

# AIHW getmedia URLs use opaque UUIDs per release.
_AIHW_MEDICARE_URLS_BY_RELEASE: dict[str, str] = {
    "2024-25": (
        "https://www.aihw.gov.au/getmedia/"
        "e733afb1-0cba-4998-be88-86fa9291e621/"
        "Medicare-mental-health-service-2024-25.zip"
    ),
}

_MEASURE_TO_COLUMN: dict[str, str] = {
    "Patients": "mh_medicare_patients_count",
    "Patient rate per 1,000 population": "mh_medicare_patient_rate_per_1000",
    "Services": "mh_medicare_services_count",
    "Service rate per 1,000 population": "mh_medicare_service_rate_per_1000",
}

_COUNT_COLUMNS: tuple[str, ...] = (
    "mh_medicare_patients_count",
    "mh_medicare_services_count",
)

# Headline provider-type. Real values carry non-breaking spaces
# (U+00A0); we normalise NBSP -> space before comparing, so this
# plain-space literal matches.
_ALL_PROVIDERS = "All providers"


class AihwMhMedicareDataSource:
    """Fetch + load AIHW NMHSPF Medicare-subsidised MH-services data.

    Implements the :class:`DatasetFetcher` Protocol. SA4-native data
    downscaled to SA2 via a boundary-derived ``SA2 -> SA4`` mapping that
    callers attach before ``load()``.

    Args:
        release: Financial year (e.g. ``"2024-25"``) or ``"latest"``.
        root: Cache directory for the downloaded ZIP + parquet sidecar.
        session: Optional ``requests.Session`` (tests pass a hermetic one).
        chunk_size / timeout: As per the other ABS/AIHW fetchers.
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
        self._sa2_to_sa4: dict[str, str] | None = None

    # ---- mapping attachment ------------------------------------------

    def attach_sa2_to_sa4_mapping(self, mapping: dict[str, str]) -> None:
        """Attach the boundary-derived ``{sa2_code: sa4_code}`` lookup.

        SA4 codes must be the bare 3-digit form (``"101"``); the parser
        strips the AIHW ``SA4-`` prefix before joining.
        """
        if not isinstance(mapping, dict):
            raise TypeError(
                f"attach_sa2_to_sa4_mapping expects a dict[str, str]; got {type(mapping).__name__}"
            )
        self._sa2_to_sa4 = dict(mapping)
        _log.debug(
            "AihwMhMedicareDataSource: attached %d SA2 -> SA4 mappings",
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
        return self._root.exists() and any(self._root.glob("aihw-mh-medicare-*.zip"))

    @property
    def _zip_path(self) -> Path:
        return self._root / f"aihw-mh-medicare-{self.resolved_release}.zip"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"aihw-mh-medicare-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the AIHW Medicare ZIP for the resolved release."""
        self._resolve_release()
        if self._zip_path.exists() and not refresh:
            _log.debug("AIHW MH Medicare cached at %s", self._zip_path)
            return self._zip_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._zip_path.with_suffix(self._zip_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info("Downloading AIHW MH Medicare (%s) from %s", self.resolved_release, url)
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label="AIHW MH Medicare",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._zip_path)
        _log.info("Saved AIHW MH Medicare to %s", self._zip_path)
        return self._zip_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021`` with one row
        per SA2 (downscaled from the SA4-level source).
        """
        if self._sa2_to_sa4 is None:
            raise RuntimeError(
                "AihwMhMedicareDataSource.load() requires a SA2 -> SA4 "
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
        sa4_df = self._parse_zip(zip_path, release=self.resolved_release)

        records: list[dict[str, object]] = []
        for sa2_code, sa4_code in self._sa2_to_sa4.items():
            if sa4_code not in sa4_df.index:
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
            picked = max(_AIHW_MEDICARE_URLS_BY_RELEASE)
        elif self._release_request in _AIHW_MEDICARE_URLS_BY_RELEASE:
            picked = self._release_request
        else:
            raise RuntimeError(
                f"AIHW MH Medicare release {self._release_request!r} not in the "
                f"hardcoded URL registry. Available: "
                f"{sorted(_AIHW_MEDICARE_URLS_BY_RELEASE)}. AIHW uses opaque "
                f"getmedia UUIDs; new releases need to be added to "
                f"_AIHW_MEDICARE_URLS_BY_RELEASE in "
                f"src/census_augment/datasets/_aihw_medicare.py."
            )
        self._resolved_release = picked
        self._resolved_url = _AIHW_MEDICARE_URLS_BY_RELEASE[picked]
        _log.info(
            "Resolved AIHW MH Medicare release=%s, url=%s",
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_zip(zip_path: Path, *, release: str) -> pd.DataFrame:
        """Parse the AIHW Medicare ZIP -> DataFrame indexed by bare SA4
        code, one column per measure, for the requested release.

        Real-data layout (live-probed 2026-06-05):
        - Member CSV ``Medicare mental health services PHN SA4
          2024-25.csv``, **cp1252**.
        - Filter ``GeographicAreaType == "SA4"``, ``ProviderType`` (NBSP-
          normalised) == ``"All providers"``, and ``FinancialYear ==
          release`` (en-dash normalised).
        - SA4 codes are **hyphenated** (``SA4-101``) — strip ``^SA4-``.
        """
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = None
            for name in zf.namelist():
                low = name.lower()
                if "phn" in low and "sa4" in low and low.endswith(".csv"):
                    csv_name = name
                    break
            if csv_name is None:
                raise RuntimeError(
                    f"AIHW MH Medicare ZIP at {zip_path} is missing the PHN+SA4 "
                    f"CSV (looked for *PHN*SA4*.csv). Contents: {zf.namelist()}"
                )
            with zf.open(csv_name) as f:
                raw = pd.read_csv(io.TextIOWrapper(f, encoding="cp1252"))

        required = {
            "FinancialYear",
            "GeographicAreaType",
            "GeographicAreaCode",
            "ProviderType",
            "Measure",
            "Value",
        }
        missing = required - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"AIHW MH Medicare CSV in {zip_path} is missing expected columns "
                f"{sorted(missing)}; got {list(raw.columns)}. Upstream schema "
                f"may have changed — re-probe with tools/probe_new_datasets.py."
            )

        # Normalise non-breaking spaces in ProviderType + en-dash in FY.
        raw["ProviderType"] = (
            raw["ProviderType"].astype(str).str.replace("\xa0", " ", regex=False).str.strip()
        )
        raw["FinancialYear"] = raw["FinancialYear"].astype(str).str.replace("–", "-", regex=False)

        filt = (
            (raw["GeographicAreaType"] == "SA4")
            & (raw["ProviderType"] == _ALL_PROVIDERS)
            & (raw["FinancialYear"] == release)
        )
        slice_df = raw.loc[filt].copy()
        if slice_df.empty:
            available_fys = sorted(
                raw.loc[raw["GeographicAreaType"] == "SA4", "FinancialYear"].unique()
            )
            raise RuntimeError(
                f"AIHW MH Medicare CSV in {zip_path} has no SA4 / "
                f"{_ALL_PROVIDERS!r} rows for release {release!r}. Available FY "
                f"values for SA4: {available_fys}; ProviderType values: "
                f"{sorted(raw['ProviderType'].unique())}."
            )

        # SA4 codes are HYPHENATED (SA4-101) — strip the "SA4-" prefix.
        slice_df["sa4_code"] = (
            slice_df["GeographicAreaCode"].astype(str).str.replace(r"^SA4-", "", regex=True)
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
                "AIHW MH Medicare CSV contains unrecognised Measure labels %r; "
                "these will be dropped. Update _MEASURE_TO_COLUMN in "
                "_aihw_medicare.py if AIHW added a metric we want to surface.",
                sorted(unknown_measures),
            )
        renamed = pivoted.rename(columns=_MEASURE_TO_COLUMN)
        renamed = renamed.reindex(columns=list(_MEASURE_TO_COLUMN.values()))

        for count_col in _COUNT_COLUMNS:
            if count_col in renamed.columns:
                renamed[count_col] = pd.to_numeric(renamed[count_col], errors="coerce").astype(
                    "Int64"
                )

        return renamed


# ---- fetcher registration ------------------------------------------------


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhMedicareDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhMedicareDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_medicare", _build_fetcher)


_register()
