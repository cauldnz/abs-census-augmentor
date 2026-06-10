"""Shared base class for the AIHW NMHSPF SA4-keyed datasets (spec §20).

Five AIHW datasets in the "Regional activity data" family
(``aihw_mh_prescriptions``, ``aihw_mh_admitted_patients``,
``aihw_mh_ed_presentations``, ``aihw_mh_medicare``,
``aihw_mh_community``) share an identical shape: a hardcoded
``getmedia`` URL-per-release registry, a single ZIP holding a
long-format CSV, the same SA4 → SA2 downscale via an attached boundary
mapping, the same parquet sidecar caching, and the same
``reference_financial_year`` stamping.

Their *only* genuine variation — confirmed firsthand per the Real Data
First discipline, one re-probe per dataset — is a handful of schema
details: the CSV encoding, which member to read, the filter
dimension(s) and value(s), the SA4 code format (bare / ``SA4``-prefixed
/ hyphenated ``SA4-``), the measure→column map, and whether the file
carries a ``FinancialYear`` column at all. This base captures the common
machinery; each subclass declares only those differences as class
attributes.

Behaviour is identical to the five hand-written fetchers this replaced —
the per-dataset tests, lock-doors, and spec-match tests all pass
untouched. Logging is routed to each subclass's *own* module logger (via
``type(self).__module__``) so per-dataset ``caplog`` filters keep
working and log provenance stays accurate.

Subclass contract — override the class attributes in the
``# ---- subclass config`` block. Everything else is inherited.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import ClassVar

import pandas as pd
import requests

from .._http_retry import retry_stream_get


class AihwSa4Dataset:
    """Base for AIHW NMHSPF SA4-keyed, SA2-downscaled fetchers.

    Implements the :class:`DatasetFetcher` Protocol. SA4-native data
    downscaled to SA2 via a boundary-derived ``SA2 -> SA4`` mapping that
    callers attach before ``load()``.

    Args:
        release: Financial year (e.g. ``"2024-25"``) or ``"latest"``.
        root: Cache directory for the downloaded ZIP + parquet sidecar.
        session: Optional ``requests.Session`` (tests pass a hermetic one).
        chunk_size / timeout: As per the other ABS/AIHW fetchers.

    Use ``attach_sa2_to_sa4_mapping()`` before ``load()``. Without it,
    ``load()`` raises a clear error explaining how to attach one.
    """

    # ---- subclass config (override these) ----------------------------
    # Human label used in log lines + error messages, e.g. "MH Rx".
    _label: ClassVar[str] = ""
    # Cache-file slug, e.g. "aihw-mh-rx" -> aihw-mh-rx-<release>.zip.
    _cache_slug: ClassVar[str] = ""
    # Name of the module-level URL registry constant (for error text).
    _registry_const_name: ClassVar[str] = ""
    # {release: url}; the module-level _AIHW_*_URLS_BY_RELEASE constant.
    _url_registry: ClassVar[dict[str, str]] = {}
    # CSV encoding ("cp1252" or "utf-8") — differs across the family.
    _encoding: ClassVar[str] = "utf-8"
    # dtype passed to read_csv; str forces all-string (one dataset needs
    # it because its code column is numeric-looking yet must stay text).
    _csv_dtype: ClassVar[type[str] | None] = None
    # Lowercased substrings that must ALL appear in the member filename.
    _member_substrings: ClassVar[tuple[str, ...]] = ()
    # Human hint after "is missing the " in the CSV-not-found error.
    _csv_missing_hint: ClassVar[str] = ""
    # Columns that must be present, else a loud "missing expected columns".
    _required_columns: ClassVar[frozenset[str]] = frozenset()
    # Columns to normalise non-breaking spaces in before filtering.
    _nbsp_strip_columns: ClassVar[tuple[str, ...]] = ()
    # FinancialYear column name, or None if the file is single-year.
    _financial_year_column: ClassVar[str | None] = None
    # (column, value) equality filters; the FIRST must be the geo-type.
    _filters: ClassVar[tuple[tuple[str, str], ...]] = ()
    # Human hint after "has no " in the empty-slice error, e.g. "SA4/Total".
    _filter_empty_hint: ClassVar[str] = ""
    # Column holding the SA4 code.
    _sa4_code_column: ClassVar[str] = ""
    # Regex prefix to strip from the SA4 code (e.g. r"^SA4", r"^SA4-"),
    # or None when the code is already bare.
    _sa4_code_strip_pattern: ClassVar[str | None] = None
    # Long-format measure label + value column names.
    _measure_column: ClassVar[str] = "Measure"
    _value_column: ClassVar[str] = "Value"
    # {AIHW measure label: snake_case output column}; the module constant.
    _measure_to_column: ClassVar[dict[str, str]] = {}
    # Output columns to coerce to nullable Int64 (counts, not rates).
    _count_columns: ClassVar[tuple[str, ...]] = ()
    # When the CSV is read all-string, every value column needs
    # to_numeric; otherwise pandas already inferred the rate floats.
    _coerce_all_value_columns: ClassVar[bool] = False

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

    @property
    def _log(self) -> logging.Logger:
        # Route to the SUBCLASS's module logger so per-dataset caplog
        # filters keep working and provenance stays accurate.
        return logging.getLogger(type(self).__module__)

    # ---- mapping attachment ------------------------------------------

    def attach_sa2_to_sa4_mapping(self, mapping: dict[str, str]) -> None:
        """Attach the boundary-derived ``{sa2_code: sa4_code}`` lookup.

        Pipeline.from_config wires this automatically from the SA2
        boundary file's ``SA4_CODE21`` column via
        :func:`census_augment.spatial.compute_sa2_parent_codes`. SA4
        codes must be the bare 3-digit form (``"101"``); the parser
        normalises the AIHW code format before joining.
        """
        if not isinstance(mapping, dict):
            raise TypeError(
                f"attach_sa2_to_sa4_mapping expects a dict[str, str]; got {type(mapping).__name__}"
            )
        self._sa2_to_sa4 = dict(mapping)
        self._log.debug(
            "%s: attached %d SA2 -> SA4 mappings",
            type(self).__name__,
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
        return self._root.exists() and any(self._root.glob(f"{self._cache_slug}-*.zip"))

    @property
    def _zip_path(self) -> Path:
        return self._root / f"{self._cache_slug}-{self.resolved_release}.zip"

    @property
    def _parquet_path(self) -> Path:
        return self._root / f"{self._cache_slug}-{self.resolved_release}.parquet"

    def fetch(self, refresh: bool = False) -> Path:
        """Download the AIHW ZIP for the resolved release."""
        self._resolve_release()
        if self._zip_path.exists() and not refresh:
            self._log.debug("AIHW %s cached at %s", self._label, self._zip_path)
            return self._zip_path

        self._root.mkdir(parents=True, exist_ok=True)
        tmp = self._zip_path.with_suffix(self._zip_path.suffix + ".tmp")
        url = self._resolved_url or ""
        self._log.info("Downloading AIHW %s (%s) from %s", self._label, self.resolved_release, url)
        with retry_stream_get(
            self._session,
            url,
            timeout=self._timeout,
            label=f"AIHW {self._label}",
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self._chunk_size):
                    if chunk:
                        f.write(chunk)
        tmp.replace(self._zip_path)
        self._log.info("Saved AIHW %s to %s", self._label, self._zip_path)
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
                f"{type(self).__name__}.load() requires a SA2 -> SA4 "
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

        # Cross-level downscale: every SA2 inherits its SA4's row values.
        out_columns = list(self._measure_to_column.values())
        records: list[dict[str, object]] = []
        for sa2_code, sa4_code in self._sa2_to_sa4.items():
            if sa4_code not in sa4_df.index:
                # The boundary may carry SA4 codes AIHW didn't publish for
                # (rare pseudo-SA4s). Emit nulls rather than dropping the
                # SA2 so the join with other datasets stays well-formed.
                rec: dict[str, object] = {
                    "sa2_code_2021": str(sa2_code),
                    **{col: None for col in out_columns},
                }
            else:
                row = sa4_df.loc[sa4_code]
                rec = {
                    "sa2_code_2021": str(sa2_code),
                    **{col: row[col] for col in out_columns},
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
            picked = max(self._url_registry)
        elif self._release_request in self._url_registry:
            picked = self._release_request
        else:
            module_basename = type(self).__module__.rsplit(".", 1)[-1]
            raise RuntimeError(
                f"AIHW {self._label} release {self._release_request!r} not in the "
                f"hardcoded URL registry. Available: "
                f"{sorted(self._url_registry)}. AIHW uses opaque "
                f"getmedia UUIDs; new releases need to be added to "
                f"{self._registry_const_name} in "
                f"src/census_augment/datasets/{module_basename}.py."
            )
        self._resolved_release = picked
        self._resolved_url = self._url_registry[picked]
        self._log.info(
            "Resolved AIHW %s release=%s, url=%s",
            self._label,
            self._resolved_release,
            self._resolved_url,
        )

    # ---- parsing -------------------------------------------------------

    def _parse_zip(self, zip_path: Path, *, release: str) -> pd.DataFrame:
        """Parse the AIHW ZIP into a DataFrame indexed by bare SA4 code
        with one column per measure for the requested release.

        The steps are common across the family; per-dataset specifics
        (member match, encoding, filters, code format, measures) come
        from the class attributes declared by each subclass.
        """
        raw = self._read_member_csv(zip_path)
        self._check_required_columns(raw, zip_path)

        # Normalise non-breaking spaces in the filter columns that carry
        # them (so the equality filters below match the plain-space form).
        for col in self._nbsp_strip_columns:
            raw[col] = raw[col].astype(str).str.replace("\xa0", " ", regex=False).str.strip()

        # Normalise en-dash FY labels to ASCII so they match the release id.
        if self._financial_year_column is not None:
            raw[self._financial_year_column] = (
                raw[self._financial_year_column].astype(str).str.replace("–", "-", regex=False)
            )

        slice_df = self._apply_filters(raw, zip_path, release)

        # Derive the bare SA4 code (strip the AIHW prefix if any).
        code = slice_df[self._sa4_code_column].astype(str)
        if self._sa4_code_strip_pattern is not None:
            code = code.str.replace(self._sa4_code_strip_pattern, "", regex=True)
        slice_df["sa4_code"] = code.str.strip()

        pivoted = slice_df.pivot_table(
            index="sa4_code",
            columns=self._measure_column,
            values=self._value_column,
            aggfunc="first",
        )

        unknown_measures = set(pivoted.columns) - set(self._measure_to_column)
        if unknown_measures:
            module_basename = type(self).__module__.rsplit(".", 1)[-1]
            self._log.warning(
                "AIHW %s CSV contains unrecognised %s labels %r; these will be "
                "dropped. Update the measure map in %s.py if AIHW added a metric "
                "we want to surface.",
                self._label,
                self._measure_column,
                sorted(unknown_measures),
                module_basename,
            )
        renamed = pivoted.rename(columns=self._measure_to_column)
        renamed = renamed.reindex(columns=list(self._measure_to_column.values()))

        # When the CSV was read all-string, every value column needs
        # numeric coercion; otherwise only the integer counts do.
        if self._coerce_all_value_columns:
            for col in renamed.columns:
                renamed[col] = pd.to_numeric(renamed[col], errors="coerce")
        for count_col in self._count_columns:
            if count_col in renamed.columns:
                renamed[count_col] = pd.to_numeric(renamed[count_col], errors="coerce").astype(
                    "Int64"
                )

        return renamed

    # ---- parse helpers -------------------------------------------------

    def _read_member_csv(self, zip_path: Path) -> pd.DataFrame:
        with zipfile.ZipFile(zip_path) as zf:
            csv_name = None
            for name in zf.namelist():
                low = name.lower()
                if low.endswith(".csv") and all(sub in low for sub in self._member_substrings):
                    csv_name = name
                    break
            if csv_name is None:
                raise RuntimeError(
                    f"AIHW {self._label} ZIP at {zip_path} is missing the "
                    f"{self._csv_missing_hint}. Contents: {zf.namelist()}"
                )
            with zf.open(csv_name) as f:
                return pd.read_csv(
                    io.TextIOWrapper(f, encoding=self._encoding), dtype=self._csv_dtype
                )

    def _check_required_columns(self, raw: pd.DataFrame, zip_path: Path) -> None:
        missing = self._required_columns - set(raw.columns)
        if missing:
            raise RuntimeError(
                f"AIHW {self._label} CSV in {zip_path} is missing expected columns "
                f"{sorted(missing)}; got {list(raw.columns)}. Upstream schema "
                f"may have changed — re-probe the live ZIP."
            )

    def _apply_filters(self, raw: pd.DataFrame, zip_path: Path, release: str) -> pd.DataFrame:
        mask = pd.Series(True, index=raw.index)
        for col, val in self._filters:
            mask &= raw[col] == val
        if self._financial_year_column is not None:
            mask &= raw[self._financial_year_column] == release

        slice_df = raw.loc[mask].copy()
        if slice_df.empty:
            diagnostics: list[str] = []
            if self._financial_year_column is not None and self._filters:
                geo_col, geo_val = self._filters[0]
                avail = sorted(
                    raw.loc[raw[geo_col] == geo_val, self._financial_year_column].dropna().unique()
                )
                diagnostics.append(f"Available FY for {geo_val}: {avail}")
            for col, _ in self._filters:
                diagnostics.append(f"{col} values: {sorted(raw[col].dropna().unique())}")
            raise RuntimeError(
                f"AIHW {self._label} CSV in {zip_path} has no {self._filter_empty_hint} "
                f"rows for release {release!r}. " + "; ".join(diagnostics)
            )
        return slice_df
