"""AIHW Mental Health Emergency Department services fetcher (spec §20,
dataset id ``aihw_mh_ed_presentations``).

Third AIHW NMHSPF dataset (after ``aihw_mh_prescriptions`` and
``aihw_mh_admitted_patients``). Captures mental-health-related
**emergency department presentations** at SA4 level, downscaled to SA2
via the boundary file's ``SA4_CODE21`` attribute (see ``spec.md`` §20.7
Strategy 1). Catalogue identifier ``AIHW_ED``.

Real-data findings (live-probed 2026-06-05) — every AIHW dataset in
this family has its own quirks, so the parse is per-dataset:

- The member CSV lives inside a subdirectory whose name contains a
  **literal Unicode en-dash** (``Data tables_ED states and territories
  2023–24/ED_PHN_SA4_2324.csv``). Match the member by the ``PHN_SA4``
  substring, NOT an exact path.
- The CSV is **cp1252** (like prescriptions; unlike APC which is UTF-8).
- The file carries **multiple financial years** (2014-15 … 2023-24)
  with the FY label using a Unicode en-dash — normalise to ASCII and
  filter to the requested release, same as prescriptions.
- A **``PresentationType``** dimension (``Mental health-related
  presentations`` vs ``All presentations``) — filter to the MH-related
  rows for the headline values.
- SA4 codes use the ``SA4101`` prefix form (strip ``^SA4``).
- Columns: ``FinancialYear, PresentationType, StateOrTerritory,
  GeographicAreaType, GeographicAreaCode, GeographicAreaName, Measure,
  Value``. Two measures: ``Number`` and ``Rate (per 10,000 population)``.

Cross-level downscale requires the SA2 → SA4 mapping attached before
``load()`` via ``attach_sa2_to_sa4_mapping`` — ``Pipeline.from_config``
wires this from the boundary GDF (and the enricher attaches it to any
fetcher exposing the method, so no per-dataset pipeline change needed).
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

# AIHW getmedia URLs use opaque UUIDs per release. The single ZIP
# carries all financial years; the release id selects which FY's rows
# the parser surfaces. New ZIP releases need a new UUID added here.
_AIHW_ED_URLS_BY_RELEASE: dict[str, str] = {
    "2023-24": (
        "https://www.aihw.gov.au/getmedia/"
        "f9ac2b47-69b7-47f5-a1a2-7e5d1099195b/"
        "Mental-health-services-provided-in-emergency-departments-"
        "states-and-territories-2023-24.zip"
    ),
}

# Map the AIHW Measure label (verbatim) to the augmentor's snake_case
# column. Two measures: a count and a per-10,000-population rate.
_MEASURE_TO_COLUMN: dict[str, str] = {
    "Number": "mh_ed_presentations_count",
    "Rate (per 10,000 population)": "mh_ed_presentations_per_10000",
}

_COUNT_COLUMNS: tuple[str, ...] = ("mh_ed_presentations_count",)

# Only the MH-related rows are the headline; the file also carries an
# "All presentations" denominator series.
_MH_PRESENTATION_TYPE = "Mental health-related presentations"


class AihwMhEdPresentationsDataSource:
    """Fetch + load AIHW NMHSPF mental-health ED-presentations data.

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
        self._sa2_to_sa4: dict[str, str] | None = None

    # ---- mapping attachment ------------------------------------------

    def attach_sa2_to_sa4_mapping(self, mapping: dict[str, str]) -> None:
        """Attach the boundary-derived ``{sa2_code: sa4_code}`` lookup.

        SA4 codes must be the bare 3-digit form (``"101"``); the parser
        strips the AIHW ``SA4`` prefix before joining. Pipeline.from_config
        wires this from ``compute_sa2_parent_codes(boundaries)["SA4"]``.
        """
        if not isinstance(mapping, dict):
            raise TypeError(
                f"attach_sa2_to_sa4_mapping expects a dict[str, str]; got {type(mapping).__name__}"
            )
        self._sa2_to_sa4 = dict(mapping)
        _log.debug(
            "AihwMhEdPresentationsDataSource: attached %d SA2 -> SA4 mappings",
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
        return self._root.exists() and any(self._root.glob("aihw-mh-ed-*.zip"))

    @property
    def _zip_path(self) -> Path:
        return self._root / f"aihw-mh-ed-{self.resolved_release}.zip"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"aihw-mh-ed-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the AIHW ED ZIP for the resolved release."""
        self._resolve_release()
        if self._zip_path.exists() and not refresh:
            _log.debug("AIHW MH ED cached at %s", self._zip_path)
            return self._zip_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._zip_path.with_suffix(self._zip_path.suffix + ".tmp")
        url = self._resolved_url or ""
        _log.info("Downloading AIHW MH ED (%s) from %s", self.resolved_release, url)
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label="AIHW MH ED",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._zip_path)
        _log.info("Saved AIHW MH ED to %s", self._zip_path)
        return self._zip_path

    def load(self) -> pd.DataFrame:
        """Return a DataFrame indexed by ``sa2_code_2021`` with one row
        per SA2 (downscaled from the SA4-level source).

        Requires :meth:`attach_sa2_to_sa4_mapping` first — without it,
        raises a clear ``RuntimeError``.
        """
        if self._sa2_to_sa4 is None:
            raise RuntimeError(
                "AihwMhEdPresentationsDataSource.load() requires a SA2 -> SA4 "
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
            picked = max(_AIHW_ED_URLS_BY_RELEASE)
        elif self._release_request in _AIHW_ED_URLS_BY_RELEASE:
            picked = self._release_request
        else:
            raise RuntimeError(
                f"AIHW MH ED release {self._release_request!r} not in the "
                f"hardcoded URL registry. Available: "
                f"{sorted(_AIHW_ED_URLS_BY_RELEASE)}. AIHW uses opaque "
                f"getmedia UUIDs; new releases need to be added to "
                f"_AIHW_ED_URLS_BY_RELEASE in "
                f"src/census_augment/datasets/_aihw_ed.py."
            )
        self._resolved_release = picked
        self._resolved_url = _AIHW_ED_URLS_BY_RELEASE[picked]
        _log.info(
            "Resolved AIHW MH ED release=%s, url=%s",
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    @staticmethod
    def _parse_zip(zip_path: Path, *, release: str) -> pd.DataFrame:
        """Parse the AIHW ED ZIP and return a DataFrame indexed by bare
        SA4 code with one column per measure for the requested release.

        Real-data layout (live-probed 2026-06-05):
        - Member CSV under a subdir whose name carries a Unicode en-dash
          (``Data tables_ED states and territories 2023–24/
          ED_PHN_SA4_2324.csv``) — match by the ``PHN_SA4`` substring.
        - **cp1252** encoding.
        - Filter ``GeographicAreaType == "SA4"``,
          ``PresentationType == "Mental health-related presentations"``,
          and ``FinancialYear == release`` (after en-dash normalisation).
        - SA4 codes carry an ``SA4`` prefix — stripped to match the
          boundary's bare ``SA4_CODE21``.
        """
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = None
            for name in zf.namelist():
                low = name.lower()
                if "phn_sa4" in low and low.endswith(".csv"):
                    csv_name = name
                    break
            if csv_name is None:
                raise RuntimeError(
                    f"AIHW MH ED ZIP at {zip_path} is missing the PHN_SA4 CSV "
                    f"(looked for *PHN_SA4*.csv). Contents: {zf.namelist()}"
                )
            with zf.open(csv_name) as f:
                # cp1252 (real-data finding) — the en-dash in the FY
                # labels mojibakes under UTF-8.
                raw = pd.read_csv(io.TextIOWrapper(f, encoding="cp1252"))

        required = {
            "FinancialYear",
            "PresentationType",
            "GeographicAreaType",
            "GeographicAreaCode",
            "Measure",
            "Value",
        }
        missing = required - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"AIHW MH ED CSV in {zip_path} is missing expected columns "
                f"{sorted(missing)}; got {list(raw.columns)}. Upstream schema "
                f"may have changed — re-probe with tools/probe_new_datasets.py."
            )

        # Normalise the en-dash FY labels to ASCII so they match the
        # release id.
        raw["FinancialYear"] = raw["FinancialYear"].astype(str).str.replace("–", "-", regex=False)

        filt = (
            (raw["GeographicAreaType"] == "SA4")
            & (raw["PresentationType"] == _MH_PRESENTATION_TYPE)
            & (raw["FinancialYear"] == release)
        )
        slice_df = raw.loc[filt].copy()
        if slice_df.empty:
            available_fys = sorted(
                raw.loc[raw["GeographicAreaType"] == "SA4", "FinancialYear"].unique()
            )
            raise RuntimeError(
                f"AIHW MH ED CSV in {zip_path} has no SA4 / "
                f"{_MH_PRESENTATION_TYPE!r} rows for release {release!r}. "
                f"Available FY values for SA4: {available_fys}; "
                f"PresentationType values: "
                f"{sorted(raw['PresentationType'].unique())}."
            )

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
                "AIHW MH ED CSV contains unrecognised Measure labels %r; "
                "these will be dropped. Update _MEASURE_TO_COLUMN in "
                "_aihw_ed.py if AIHW added a metric we want to surface.",
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


def _build_fetcher(root: Path, release: str | None = None) -> AihwMhEdPresentationsDataSource:
    kwargs: dict[str, object] = {"root": root}
    if release is not None:
        kwargs["release"] = release
    return AihwMhEdPresentationsDataSource(**kwargs)  # type: ignore[arg-type]


def _register() -> None:
    from . import registry  # noqa: PLC0415

    registry.register_fetcher("aihw_mh_ed_presentations", _build_fetcher)


_register()
