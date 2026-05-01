"""Variable catalog: resolves config variable refs against DataPack metadata.

This is the semantic-validation layer deferred from :mod:`config.py` — it
takes a :class:`~census_augment.data_sources.datapacks.DataPackMetadata`
(the parsed metadata Excel) and:

- Resolves ``"G02.Median_age_persons"`` references to a
  :class:`~census_augment.data_sources.datapacks.ColumnMetadata`.
- Validates a whole ``variables`` mapping at once, aggregating errors.
- Backs ``discover --search`` / ``discover --table`` (spec §6.2, §11) by
  exposing ``search`` and ``list_table`` helpers.

Errors include near-match suggestions via :mod:`difflib` so typos like
``G99.Median_age`` produce ``did you mean: G09A, G09B, G02?`` instead of
silent bewilderment (spec §10).
"""

from __future__ import annotations

import difflib
import re

from .data_sources.datapacks import (
    ColumnMetadata,
    DataPackMetadata,
    DataPacksDataSource,
)

# Same shape as the ref regex in config.py — single dot, both halves
# alphanumeric/underscore. config.py validates structurally; catalog.py
# validates semantically.
_REF_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z][A-Za-z0-9_]*)$")

_NEAR_MATCH_LIMIT = 3
_NEAR_MATCH_CUTOFF = 0.6


class CatalogError(ValueError):
    """Raised when a variable reference can't be resolved or validated."""


class VariableCatalog:
    """Validates and searches DataPack columns.

    Construct from existing metadata, or via the
    :meth:`from_data_source` factory which fetches the metadata from a
    :class:`DataPacksDataSource`.
    """

    def __init__(self, metadata: DataPackMetadata) -> None:
        self._metadata = metadata

    @classmethod
    def from_data_source(cls, datapacks: DataPacksDataSource) -> VariableCatalog:
        return cls(datapacks.load_metadata())

    @property
    def metadata(self) -> DataPackMetadata:
        return self._metadata

    # ---- core resolution -------------------------------------------------

    def resolve(self, ref: str) -> ColumnMetadata:
        """Return the :class:`ColumnMetadata` for ``ref`` (e.g. ``"G02.Median_age_persons"``).

        Raises :class:`CatalogError` with near-match suggestions when the
        table or column doesn't exist, or when the ref isn't shaped like
        ``<table>.<column>``.
        """
        table_id, code = self._parse_ref(ref)
        if not self._metadata.has_table(table_id):
            raise CatalogError(self._table_missing_message(table_id))
        if not self._metadata.has_column(table_id, code):
            raise CatalogError(self._column_missing_message(table_id, code))
        return self._metadata.tables[table_id].columns[code]

    def validate_variables(self, variables: dict[str, str]) -> None:
        """Validate every entry in a config ``variables`` mapping.

        Aggregates *all* failures into one :class:`CatalogError` so the
        user sees every problem in one go (config debugging is much
        nicer that way than fixing one ref at a time).
        """
        errors: list[str] = []
        for friendly_name, ref in variables.items():
            try:
                self.resolve(ref)
            except CatalogError as e:
                errors.append(f"  - {friendly_name!r} = {ref!r}: {e}")
        if errors:
            raise CatalogError(
                "Variable validation failed:\n" + "\n".join(errors)
            )

    # ---- search / listing ------------------------------------------------

    def search(self, term: str, *, limit: int = 20) -> list[ColumnMetadata]:
        """Find columns whose code or description contains ``term`` (case-insensitive).

        Results are sorted: code matches first, then description matches,
        each group preserving table/column order from the metadata.
        """
        term_lower = term.lower()
        code_hits: list[ColumnMetadata] = []
        desc_hits: list[ColumnMetadata] = []
        for col in self._metadata.all_columns():
            if term_lower in col.code.lower():
                code_hits.append(col)
            elif term_lower in col.description.lower():
                desc_hits.append(col)
        return (code_hits + desc_hits)[:limit]

    def list_table(self, table_id: str) -> list[ColumnMetadata]:
        """List all columns in ``table_id`` in metadata order.

        Raises :class:`CatalogError` with near-match suggestions if the
        table doesn't exist.
        """
        if not self._metadata.has_table(table_id):
            raise CatalogError(self._table_missing_message(table_id))
        return list(self._metadata.tables[table_id].columns.values())

    # ---- near-match suggestions -----------------------------------------

    def suggest_tables(self, table_id: str) -> list[str]:
        return difflib.get_close_matches(
            table_id,
            list(self._metadata.tables.keys()),
            n=_NEAR_MATCH_LIMIT,
            cutoff=_NEAR_MATCH_CUTOFF,
        )

    def suggest_codes_in_table(self, table_id: str, code: str) -> list[str]:
        if not self._metadata.has_table(table_id):
            return []
        return difflib.get_close_matches(
            code,
            list(self._metadata.tables[table_id].columns.keys()),
            n=_NEAR_MATCH_LIMIT,
            cutoff=_NEAR_MATCH_CUTOFF,
        )

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _parse_ref(ref: str) -> tuple[str, str]:
        m = _REF_PATTERN.match(ref)
        if m is None:
            raise CatalogError(
                f"invalid reference format {ref!r}; "
                f"expected '<table>.<column>' (e.g. 'G02.Median_age_persons')"
            )
        return m.group(1), m.group(2)

    def _table_missing_message(self, table_id: str) -> str:
        suggestions = self.suggest_tables(table_id)
        msg = f"table {table_id!r} not found"
        if suggestions:
            msg += f"; did you mean: {', '.join(suggestions)}?"
        return msg

    def _column_missing_message(self, table_id: str, code: str) -> str:
        suggestions = self.suggest_codes_in_table(table_id, code)
        msg = f"column {code!r} not found in table {table_id!r}"
        if suggestions:
            msg += f"; did you mean: {', '.join(suggestions)}?"
        return msg
