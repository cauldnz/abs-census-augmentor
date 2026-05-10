"""Census enrichment: attach SA2-keyed dataset values to the pipeline (spec §7.4).

In v1.3 this module dispatches across registered datasets (spec §20).
The variable string's namespace tells us which dataset provides it:

- ``G\\d+.<col>`` → ``gcp_2021`` dataset (existing GCP path via
  :class:`VariableCatalog` + :class:`DataPacksDataSource`).
- ``SEIFA.<field>``, ``ERP.<field>``, ``DSS.<field>``,
  ``ATO.<field>`` → the corresponding registered dataset's fetcher.

Loads only the datasets / tables actually referenced. PRESET features
(``PRESET.<id>``) are evaluated by :class:`FeatureEvaluator` and are
**not** handled here in v1.3 — call them separately on the enriched
DataFrame. Wiring PRESETs into this stage is on the v1.4 backlog
(needs auto-loading of the underlying numerator/denominator source
columns).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from .catalog import VariableCatalog
from .data_sources.datapacks import DataPacksDataSource
from .datasets import registry
from .datasets._registry import RegistryError

_log = logging.getLogger(__name__)


class CensusEnricher:
    """Build the SA2-keyed enrichment lookup across registered datasets.

    The ``data_dir`` argument is the per-dataset cache root — fetchers
    receive ``data_dir / <dataset_id>`` so each dataset's parquet /
    XLSX cache lives in its own subdirectory under the user cache.
    """

    def __init__(
        self,
        *,
        datapacks: DataPacksDataSource,
        catalog: VariableCatalog,
        variables: dict[str, str],
        output_prefix: str = "sa2_",
        data_dir: Path | None = None,
    ) -> None:
        self._datapacks = datapacks
        self._catalog = catalog
        self._variables = dict(variables)
        self._output_prefix = output_prefix
        self._data_dir = data_dir

    def build_lookup(self) -> pd.DataFrame:
        """Return a DataFrame indexed by SA2 code with one column per variable.

        Dispatches each variable through the registry. GCP variables go
        through the existing :class:`VariableCatalog` +
        :class:`DataPacksDataSource` path; non-GCP variables resolve
        through registered fetchers. Unknown namespaces fall through to
        the GCP catalog (which raises a helpful CatalogError listing
        near-matches).
        """
        gcp_vars: list[tuple[str, str]] = []
        # dataset_id -> [(friendly_name, field_name)]
        per_dataset: dict[str, list[tuple[str, str]]] = {}

        for friendly, ref in self._variables.items():
            try:
                spec, field = registry.resolve_variable(ref)
            except RegistryError:
                # Unknown namespace — fall through to GCP catalog,
                # which will surface a helpful error.
                gcp_vars.append((friendly, ref))
                continue
            if spec.id == "gcp_2021":
                # GCP route: the existing catalog handles G\d+.<col>.
                gcp_vars.append((friendly, ref))
            else:
                per_dataset.setdefault(spec.id, []).append((friendly, field))

        pieces: list[pd.DataFrame] = []

        # GCP: existing path, untouched.
        if gcp_vars:
            pieces.append(self._build_gcp_lookup(gcp_vars))

        # Per-dataset: each fetcher loads once, project the requested
        # columns, rename to <prefix><friendly>.
        for dataset_id, friendly_fields in per_dataset.items():
            piece = self._build_dataset_lookup(dataset_id, friendly_fields)
            pieces.append(piece)

        if not pieces:
            return pd.DataFrame()

        return pd.concat(pieces, axis=1)

    def _build_gcp_lookup(
        self, friendly_refs: list[tuple[str, str]]
    ) -> pd.DataFrame:
        """Build the GCP slice of the lookup (existing v1.0 path)."""
        by_table: dict[str, list[tuple[str, str]]] = {}
        for friendly, ref in friendly_refs:
            col_meta = self._catalog.resolve(ref)
            by_table.setdefault(col_meta.table_id, []).append(
                (friendly, col_meta.code)
            )

        if not by_table:
            return pd.DataFrame()

        pieces: list[pd.DataFrame] = []
        for table_id, fc in by_table.items():
            table_df = self._datapacks.load_table(table_id)
            codes = [code for _, code in fc]
            rename_map = {
                code: f"{self._output_prefix}{friendly}"
                for friendly, code in fc
            }
            pieces.append(table_df[codes].rename(columns=rename_map))

        return pd.concat(pieces, axis=1)

    def _build_dataset_lookup(
        self,
        dataset_id: str,
        friendly_fields: list[tuple[str, str]],
    ) -> pd.DataFrame:
        """Build the slice of the lookup for one non-GCP dataset.

        Constructs the dataset's fetcher with a per-dataset cache
        directory under ``self._data_dir``, calls ``load()``, projects
        the requested columns, and renames them to
        ``<prefix><friendly>``.
        """
        fetcher = self._make_fetcher(dataset_id)
        df = fetcher.load()
        cols = [field for _, field in friendly_fields]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"Dataset {dataset_id!r} doesn't expose columns "
                f"{missing}. Available: {sorted(df.columns)[:10]}..."
            )
        rename_map = {
            field: f"{self._output_prefix}{friendly}"
            for friendly, field in friendly_fields
        }
        result: pd.DataFrame = df[cols].rename(columns=rename_map)
        return result

    def _make_fetcher(self, dataset_id: str) -> Any:
        """Construct a fetcher for ``dataset_id`` with cache under
        ``data_dir / <dataset_id>``."""
        if self._data_dir is None:
            # Tests / direct callers may not have a data_dir; honour
            # the registry's per-instance default.
            raise ValueError(
                "CensusEnricher constructed without data_dir cannot "
                f"fetch external dataset {dataset_id!r}. Pass "
                "data_dir=... to the enricher constructor."
            )

        factory = _FETCHER_FACTORIES.get(dataset_id)
        if factory is None:
            raise ValueError(
                f"No fetcher factory registered for dataset {dataset_id!r}. "
                "Add one in census_augment.enrich or register with "
                "datasets.registry."
            )
        return factory(self._data_dir / dataset_id)

    def add_enrichment_columns(
        self,
        df: pd.DataFrame,
        sa2_code_col: str = "sa2_code",
    ) -> pd.DataFrame:
        """Merge the enrichment lookup onto ``df`` via ``sa2_code_col``.

        Returns ``df`` with new columns appended. Rows whose ``sa2_code``
        is null or doesn't match any registered-dataset row get null
        enrichment values. Input row order is preserved.
        """
        if sa2_code_col not in df.columns:
            raise ValueError(
                f"sa2_code_col {sa2_code_col!r} not in DataFrame; "
                f"got: {list(df.columns)}"
            )

        lookup = self.build_lookup()
        if lookup.empty:
            return df.copy()

        # Pull the SA2 code out of the index and align its column name with
        # the input DataFrame so merge can match on a single key.
        lookup_for_merge = lookup.reset_index()
        first_col = lookup_for_merge.columns[0]
        if first_col != sa2_code_col:
            lookup_for_merge = lookup_for_merge.rename(
                columns={first_col: sa2_code_col}
            )

        return df.merge(lookup_for_merge, on=sa2_code_col, how="left")


def _build_seifa(root: Path) -> Any:
    from .datasets._seifa import SeifaDataSource  # noqa: PLC0415

    return SeifaDataSource(root=root)


def _build_erp(root: Path) -> Any:
    from .datasets._erp import ErpDataSource  # noqa: PLC0415

    return ErpDataSource(root=root)


def _build_dss(root: Path) -> Any:
    from .datasets._dss import DssDataSource  # noqa: PLC0415

    return DssDataSource(root=root)


def _build_ato(root: Path) -> Any:
    from .datasets._ato import AtoDataSource  # noqa: PLC0415

    return AtoDataSource(root=root)


# Registered fetcher factories — dataset_id → callable taking a cache root.
_FETCHER_FACTORIES: dict[str, Callable[[Path], Any]] = {
    "seifa_2021": _build_seifa,
    "erp_by_sa2": _build_erp,
    "dss_payments": _build_dss,
    "ato_personal_income": _build_ato,
}
