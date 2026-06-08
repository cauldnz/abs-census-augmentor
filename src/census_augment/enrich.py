"""Census enrichment: attach SA2-keyed dataset values to the pipeline (spec §7.4).

In v1.3 this module dispatches across registered datasets (spec §20).
The variable string's namespace tells us which dataset provides it:

- ``G\\d+.<col>`` → ``gcp`` dataset (existing GCP path via
  :class:`VariableCatalog` + :class:`DataPacksDataSource`).
- ``SEIFA.<field>``, ``ERP.<field>``, ``DSS.<field>``,
  ``ABS_PIA.<field>`` → the corresponding registered dataset's fetcher.

v1.4 adds first-class PRESET integration: variables of the form
``PRESET.<id>`` are looked up in the :class:`FeatureRegistry`,
their underlying numerator/denominator source columns are fetched
transparently (deduplicated across PRESETs), and the
:class:`FeatureEvaluator` produces the derived column. The synthetic
source columns are dropped from the final lookup so callers see only
the PRESETs they asked for.

Loads only the datasets / tables actually referenced.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .catalog import VariableCatalog
from .data_sources.datapacks import DataPacksDataSource
from .datasets import registry
from .datasets._registry import RegistryError
from .features import FeatureEvaluator, FeatureSpec, features

_log = logging.getLogger(__name__)

#: Internal prefix for synthetic source-column entries the enricher
#: injects when it auto-loads PRESET inputs. The chance a user names
#: a friendly variable starting with this prefix is essentially zero;
#: if they do, build_lookup() raises (see _SyntheticPrefixCollision).
_PRESET_SOURCE_PREFIX = "__preset_src__"


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
        dataset_release_overrides: dict[str, str] | None = None,
        sa2_areas_km2: dict[str, float] | None = None,
        sa2_to_sa4: dict[str, str] | None = None,
        lga_sa2_correspondence: object | None = None,
    ) -> None:
        self._datapacks = datapacks
        self._catalog = catalog
        self._variables = dict(variables)
        self._output_prefix = output_prefix
        self._data_dir = data_dir
        #: Optional per-dataset release overrides (temporal mode).
        #: When set, _make_fetcher passes the override through to the
        #: registry's factory. Cross-sectional mode leaves this empty
        #: and fetchers construct with their default release.
        self._dataset_release_overrides: dict[str, str] = dict(dataset_release_overrides or {})
        #: Optional SA2-code → area-km² lookup. When provided, the ERP
        #: fetcher gets it attached via ``attach_sa2_areas`` so its
        #: ``load()`` emits the ``population_density_per_km2`` column.
        #: ``Pipeline.from_config`` computes this from the boundary GDF
        #: and threads it through. Other datasets ignore it.
        self._sa2_areas_km2: dict[str, float] | None = (
            dict(sa2_areas_km2) if sa2_areas_km2 is not None else None
        )
        #: Optional SA2-code → SA4-code lookup (the bare 3-digit SA4
        #: code, matching ABS Edition 3's SA4_CODE21 attribute). When
        #: provided, the AIHW MH Prescriptions fetcher gets it attached
        #: via ``attach_sa2_to_sa4_mapping`` so its ``load()`` returns
        #: SA2-keyed rows downscaled from SA4. ``Pipeline.from_config``
        #: derives this from the boundary GDF via
        #: ``compute_sa2_parent_codes(boundaries)["SA4"]``. Other
        #: datasets ignore it.
        self._sa2_to_sa4: dict[str, str] | None = (
            dict(sa2_to_sa4) if sa2_to_sa4 is not None else None
        )
        #: Optional LGA → SA2 area-weighted spatial correspondence
        #: (a :class:`census_augment.correspondence.LgaSa2Correspondence`).
        #: When provided, the ABS BA LGA fetcher gets it attached via
        #: ``attach_correspondence`` so its ``load()`` downscales
        #: LGA-keyed values to SA2 rows. Pipeline.from_config derives
        #: this from the LGA boundary + SA2 boundary. Other datasets
        #: ignore it. Typed as ``object`` so this module doesn't need
        #: to import ``correspondence``.
        self._lga_sa2_correspondence: object | None = lga_sa2_correspondence
        self._validate_no_synthetic_prefix_collision()

    def build_lookup(self) -> pd.DataFrame:
        """Return a DataFrame indexed by SA2 code with one column per variable.

        Dispatches each variable through the registry. GCP variables go
        through the existing :class:`VariableCatalog` +
        :class:`DataPacksDataSource` path; non-GCP variables resolve
        through registered fetchers; ``PRESET.<id>`` refs are expanded
        to their numerator/denominator source columns, which are fetched
        through the same dispatch logic and then collapsed by the
        :class:`FeatureEvaluator` into the derived column. Unknown
        namespaces fall through to the GCP catalog (which raises a
        helpful CatalogError listing near-matches).
        """
        preset_vars, non_preset_vars = self._split_presets(self._variables)
        synthetic_sources = self._collect_synthetic_sources(preset_vars)

        combined_vars = {**non_preset_vars, **synthetic_sources}
        base_lookup = self._build_base_lookup(combined_vars)

        if not preset_vars:
            return base_lookup

        workspace = self._build_preset_workspace(base_lookup, synthetic_sources)
        for friendly, preset_id, spec in preset_vars:
            self._evaluate_preset_into(base_lookup, workspace, friendly, preset_id, spec)

        return self._drop_synthetic_columns(base_lookup, list(synthetic_sources))

    # ---- dispatch ------------------------------------------------------

    def _build_base_lookup(self, variables: dict[str, str]) -> pd.DataFrame:
        """Run the GCP-vs-dataset dispatch over an arbitrary vars dict.

        Public ``build_lookup`` calls this on the user's vars *plus*
        any synthetic PRESET-source entries we needed to inject.
        Variables themselves are unchanged from the v1.3 dispatch logic.
        """
        gcp_vars: list[tuple[str, str]] = []
        # dataset_id -> [(friendly_name, field_name)]
        per_dataset: dict[str, list[tuple[str, str]]] = {}

        for friendly, ref in variables.items():
            try:
                spec, field = registry.resolve_variable(ref)
            except RegistryError:
                # Unknown namespace — fall through to GCP catalog,
                # which will surface a helpful error.
                gcp_vars.append((friendly, ref))
                continue
            if spec.id == "gcp":
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

    def _build_gcp_lookup(self, friendly_refs: list[tuple[str, str]]) -> pd.DataFrame:
        """Build the GCP slice of the lookup (existing v1.0 path)."""
        by_table: dict[str, list[tuple[str, str]]] = {}
        for friendly, ref in friendly_refs:
            col_meta = self._catalog.resolve(ref)
            by_table.setdefault(col_meta.table_id, []).append((friendly, col_meta.code))

        if not by_table:
            return pd.DataFrame()

        pieces: list[pd.DataFrame] = []
        for table_id, fc in by_table.items():
            table_df = self._datapacks.load_table(table_id)
            codes = [code for _, code in fc]
            rename_map = {code: f"{self._output_prefix}{friendly}" for friendly, code in fc}
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
            field: f"{self._output_prefix}{friendly}" for friendly, field in friendly_fields
        }
        result: pd.DataFrame = df[cols].rename(columns=rename_map)
        return result

    def _make_fetcher(self, dataset_id: str) -> Any:
        """Construct a fetcher for ``dataset_id`` with cache under
        ``data_dir / <dataset_id>``.

        Delegates to :meth:`census_augment.datasets.Registry.make_fetcher`,
        which dispatches to the factory each dataset module registered
        at import time. If no factory is registered the registry
        raises :class:`RegistryError` with a useful diagnostic.

        When a per-dataset release override is set (temporal mode,
        :class:`Pipeline._augment_temporal` populates this), the
        override is passed through to the factory.
        """
        if self._data_dir is None:
            # Tests / direct callers may not have a data_dir; honour
            # the registry's per-instance default.
            raise ValueError(
                "CensusEnricher constructed without data_dir cannot "
                f"fetch external dataset {dataset_id!r}. Pass "
                "data_dir=... to the enricher constructor."
            )

        from .datasets import registry  # noqa: PLC0415

        kwargs: dict[str, Any] = {"root": self._data_dir / dataset_id}
        if dataset_id in self._dataset_release_overrides:
            kwargs["release"] = self._dataset_release_overrides[dataset_id]
        fetcher = registry.make_fetcher(dataset_id, **kwargs)
        # ERP density support: when the enricher knows SA2 areas (wired
        # by Pipeline.from_config from the boundary GDF), attach them
        # to the ERP fetcher so its ``load()`` emits the
        # ``population_density_per_km2`` column.
        if (
            dataset_id == "erp_by_sa2"
            and self._sa2_areas_km2 is not None
            and hasattr(fetcher, "attach_sa2_areas")
        ):
            fetcher.attach_sa2_areas(self._sa2_areas_km2)
        # SA4-keyed cross-level datasets (AIHW MH Prescriptions, AIHW
        # Admitted Patient Care, …) need the SA2 -> SA4 mapping wired so
        # load() can downscale. Gated on the fetcher exposing the attach
        # method rather than a specific dataset id, so any future
        # SA4-downscale dataset picks it up automatically. Without the
        # attachment, the fetcher raises a clear error at load() time.
        if self._sa2_to_sa4 is not None and hasattr(fetcher, "attach_sa2_to_sa4_mapping"):
            fetcher.attach_sa2_to_sa4_mapping(self._sa2_to_sa4)
        # ABS BA LGA cross-level downscale: LGA-keyed source needs the
        # LGA -> SA2 area-weighted correspondence attached so load() can
        # downscale. Same shape as the SA4 attachment above.
        if (
            dataset_id == "abs_building_approvals_lga"
            and self._lga_sa2_correspondence is not None
            and hasattr(fetcher, "attach_correspondence")
        ):
            fetcher.attach_correspondence(self._lga_sa2_correspondence)
        return fetcher

    # ---- PRESET integration --------------------------------------------

    def _split_presets(
        self, variables: dict[str, str]
    ) -> tuple[list[tuple[str, str, FeatureSpec]], dict[str, str]]:
        """Partition ``variables`` into (preset_entries, non_preset_vars).

        Each preset_entry is ``(friendly_name, preset_id, FeatureSpec)``.
        Non-preset entries pass through to the existing dispatch logic
        unchanged.

        Raises :class:`ValueError` if a ``PRESET.<id>`` ref is malformed
        or if ``id`` isn't registered.
        """
        preset_vars: list[tuple[str, str, FeatureSpec]] = []
        non_preset_vars: dict[str, str] = {}
        for friendly, ref in variables.items():
            if not ref.startswith("PRESET."):
                non_preset_vars[friendly] = ref
                continue
            preset_id = ref[len("PRESET.") :]
            if not preset_id:
                raise ValueError(
                    f"Variable {friendly!r}: malformed PRESET ref {ref!r}; expected 'PRESET.<id>'."
                )
            try:
                spec = features.get(preset_id)
            except KeyError as e:
                known = [s.id for s in features.list_features()]
                raise ValueError(
                    f"Variable {friendly!r}: unknown PRESET id "
                    f"{preset_id!r}. Known PRESETs: {known}"
                ) from e
            preset_vars.append((friendly, preset_id, spec))
        return preset_vars, non_preset_vars

    def _collect_synthetic_sources(
        self,
        preset_vars: list[tuple[str, str, FeatureSpec]],
    ) -> dict[str, str]:
        """Build a synthetic ``{friendly_name: source_ref}`` dict that the
        normal dispatch loop will pull through alongside the user's vars.

        Source refs are deduplicated across PRESETs — if two PRESETs
        share ``G01.Tot_P_P``, we fetch it once. Synthetic friendly
        names use a fixed prefix that will not collide with user names
        (the constructor validates this).
        """
        synthetic: dict[str, str] = {}
        for _, _, spec in preset_vars:
            for source_ref in spec.source_fields():
                synthetic[f"{_PRESET_SOURCE_PREFIX}{source_ref}"] = source_ref
        return synthetic

    def _build_preset_workspace(
        self,
        base_lookup: pd.DataFrame,
        synthetic_sources: dict[str, str],
    ) -> pd.DataFrame:
        """Return a copy of ``base_lookup`` whose synthetic-source columns
        are renamed back to the bare ``<NAMESPACE>.<field>`` refs the
        :class:`FeatureEvaluator` expects to find.

        The original ``base_lookup`` is not mutated — we keep its
        prefixed synthetic columns around so they can be dropped in
        a single pass at the end of ``build_lookup``.
        """
        rename: dict[str, str] = {}
        for synth_friendly, source_ref in synthetic_sources.items():
            prefixed = f"{self._output_prefix}{synth_friendly}"
            if prefixed in base_lookup.columns:
                rename[prefixed] = source_ref
        if not rename:
            return base_lookup
        return base_lookup.rename(columns=rename)

    def _evaluate_preset_into(
        self,
        base_lookup: pd.DataFrame,
        workspace: pd.DataFrame,
        friendly: str,
        preset_id: str,
        spec: FeatureSpec,
    ) -> None:
        """Run the evaluator and mutate ``base_lookup`` in place.

        Errors get a friendly wrapper that names the offending PRESET
        and friendly so the user can find it in their config.
        """
        try:
            result = FeatureEvaluator(spec).evaluate(workspace)
        except KeyError as e:
            raise ValueError(
                f"Failed to evaluate PRESET {preset_id!r} (friendly name "
                f"{friendly!r}): {e}. The PRESET's source columns may not "
                "have been loaded — confirm the underlying dataset is "
                "registered and the ref shapes match."
            ) from e
        base_lookup[f"{self._output_prefix}{friendly}"] = result.values

    def _drop_synthetic_columns(
        self,
        base_lookup: pd.DataFrame,
        synth_friendlies: list[str],
    ) -> pd.DataFrame:
        """Strip synthetic ``<prefix>__preset_src__*`` columns from the result."""
        cols_to_drop = [
            f"{self._output_prefix}{name}"
            for name in synth_friendlies
            if f"{self._output_prefix}{name}" in base_lookup.columns
        ]
        if not cols_to_drop:
            return base_lookup
        return base_lookup.drop(columns=cols_to_drop)

    def _validate_no_synthetic_prefix_collision(self) -> None:
        """Reject user-friendly names that start with the internal prefix.

        Defensive: the prefix is `__preset_src__` and is unlikely to
        collide with a real column name, but we want a clean error if
        it does instead of silent drop.
        """
        collisions = [f for f in self._variables if f.startswith(_PRESET_SOURCE_PREFIX)]
        if collisions:
            raise ValueError(
                f"Variable names starting with {_PRESET_SOURCE_PREFIX!r} "
                "are reserved for internal PRESET source-column "
                f"injection. Rename: {sorted(collisions)}."
            )

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
                f"sa2_code_col {sa2_code_col!r} not in DataFrame; got: {list(df.columns)}"
            )

        lookup = self.build_lookup()
        if lookup.empty:
            return df.copy()

        # Pull the SA2 code out of the index and align its column name with
        # the input DataFrame so merge can match on a single key.
        lookup_for_merge = lookup.reset_index()
        first_col = lookup_for_merge.columns[0]
        if first_col != sa2_code_col:
            lookup_for_merge = lookup_for_merge.rename(columns={first_col: sa2_code_col})

        return df.merge(lookup_for_merge, on=sa2_code_col, how="left")


# Fetcher-factory wiring moved into each dataset module's tail
# (see `datasets/_seifa.py::_register()` and siblings). The pipeline
# resolves factories via `registry.make_fetcher(dataset_id, root)`.
