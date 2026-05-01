"""Census enrichment: attach DataPack values per SA2 (spec §7.4).

Loads only the DataPack tables actually referenced by configured
``variables``, builds a single lookup keyed by SA2 code with one column
per variable (named ``{output_prefix}{friendly_name}``), and merges
that lookup onto a pipeline DataFrame.

Suppressed / missing cells in the source DataPack come through as
nulls — the pipeline classifies those rows as "partially enriched"
in the run summary (spec §10).
"""

from __future__ import annotations

import pandas as pd

from .catalog import VariableCatalog
from .data_sources.datapacks import DataPacksDataSource


class CensusEnricher:
    """Build the SA2-keyed enrichment lookup and merge it onto a DataFrame."""

    def __init__(
        self,
        *,
        datapacks: DataPacksDataSource,
        catalog: VariableCatalog,
        variables: dict[str, str],
        output_prefix: str = "sa2_",
    ) -> None:
        self._datapacks = datapacks
        self._catalog = catalog
        self._variables = dict(variables)
        self._output_prefix = output_prefix

    def build_lookup(self) -> pd.DataFrame:
        """Return a DataFrame indexed by SA2 code with one column per variable.

        Resolves each ``variables`` entry against the catalog (so unknown
        refs raise :class:`~census_augment.catalog.CatalogError`), then
        loads each *unique* DataPack table at most once.
        """
        by_table: dict[str, list[tuple[str, str]]] = {}
        for friendly, ref in self._variables.items():
            col_meta = self._catalog.resolve(ref)
            by_table.setdefault(col_meta.table_id, []).append(
                (friendly, col_meta.code)
            )

        if not by_table:
            return pd.DataFrame()

        pieces: list[pd.DataFrame] = []
        for table_id, friendly_codes in by_table.items():
            table_df = self._datapacks.load_table(table_id)
            codes = [code for _, code in friendly_codes]
            rename_map = {
                code: f"{self._output_prefix}{friendly}"
                for friendly, code in friendly_codes
            }
            pieces.append(table_df[codes].rename(columns=rename_map))

        return pd.concat(pieces, axis=1)

    def add_enrichment_columns(
        self,
        df: pd.DataFrame,
        sa2_code_col: str = "sa2_code",
    ) -> pd.DataFrame:
        """Merge the enrichment lookup onto ``df`` via ``sa2_code_col``.

        Returns ``df`` with new columns appended. Rows whose ``sa2_code``
        is null or doesn't match any DataPack row get null enrichment
        values. Input row order is preserved.
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
