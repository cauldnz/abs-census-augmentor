"""Registry for pluggable datasets (spec §20.2).

Indexes :class:`DatasetSpec` instances loaded from ``datasets/<id>.md``
files at the repo root, plus any registered programmatically via
:meth:`Registry.register_spec`.

Variable resolution dispatches by namespace:

- ``SEIFA.irsd_decile`` → ``seifa`` dataset, field ``irsd_decile``
- ``ERP.population_total`` → ``erp_by_sa2`` dataset, field
  ``population_total``
- ``G02.Median_age_persons`` → ``gcp`` dataset, field
  ``Median_age_persons`` (the GCP variable convention preserves the
  table id as the namespace, since GCP exposes ~62 tables under one
  dataset).

The registry's own knowledge is intentionally minimal — it knows about
specs, not about how to fetch the data. Built-in fetchers register
themselves via :meth:`Registry.register_fetcher` at module-import
time (each dataset module ends with a registration call), and the
pipeline retrieves them via :meth:`Registry.make_fetcher`. The spec
layer stays Python-import-free for tooling that just needs to list /
search the catalogue without instantiating fetchers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._spec_loader import iter_specs_from_dir
from ._spec import DatasetSpec, parse_dataset_spec

if TYPE_CHECKING:  # pragma: no cover
    from ._protocol import DatasetFetcher

_log = logging.getLogger(__name__)


# Resolve the repo's `datasets/` directory at import time. The package
# layout is `<repo>/src/census_augment/datasets/_registry.py`; specs
# live at `<repo>/datasets/`. Walking up from this file gets us there
# in both editable installs and source checkouts. For wheel installs
# (where there's no datasets/ dir alongside the package) we fall back
# to the package-internal `_specs/` directory which we ship as data.
def _default_spec_dir() -> Path:
    here = Path(__file__).resolve()
    # repo-root candidate: src/census_augment/datasets/_registry.py
    # → repo-root/datasets/
    repo_root_candidate = here.parents[3] / "datasets"
    if repo_root_candidate.is_dir():
        return repo_root_candidate
    # Wheel-install fallback: shipped specs inside the package.
    package_specs = here.parent / "_specs"
    return package_specs


class RegistryError(ValueError):
    """Raised by registry resolution / lookup methods."""


class Registry:
    """Process-wide pluggable-dataset registry (spec §20.2).

    Public usage::

        from census_augment.datasets import registry

        registry.list_datasets()           # all specs
        registry.get("seifa")              # one spec by id
        registry.resolve_variable("SEIFA.irsd_decile")
                                           # -> (spec, "irsd_decile")
    """

    def __init__(self) -> None:
        self._by_id: dict[str, DatasetSpec] = {}
        self._fetcher_factories: dict[str, Callable[..., "DatasetFetcher"]] = {}

    # ---- construction ---------------------------------------------------

    @classmethod
    def from_repo_specs(cls, spec_dir: Path | None = None) -> Registry:
        """Construct a registry pre-populated with every ``.md`` file
        under ``spec_dir`` (defaults to the repo's ``datasets/``).

        Skips leading-underscore filenames (templates) and logs+skips
        any spec that fails to parse — see
        :func:`census_augment._spec_loader.iter_specs_from_dir`.
        """
        registry = cls()
        directory = spec_dir or _default_spec_dir()
        for spec in iter_specs_from_dir(directory, parse_dataset_spec, label="dataset spec"):
            registry.register_spec(spec)
        return registry

    # ---- spec registration ----------------------------------------------

    def register_spec(self, spec: DatasetSpec) -> None:
        """Add ``spec`` to the registry. Duplicate ids overwrite (the
        last registration wins) — this lets users override a built-in
        spec from their own config tree if needed.
        """
        if spec.id in self._by_id:
            _log.info(
                "Replacing existing dataset spec %r in registry (was: %s; new: %s)",
                spec.id,
                self._by_id[spec.id].source_path,
                spec.source_path,
            )
        self._by_id[spec.id] = spec
        _log.debug(
            "Registered dataset spec: id=%s namespace=%s status=%s",
            spec.id,
            spec.namespace,
            spec.status,
        )

    def register_fetcher(
        self,
        dataset_id: str,
        factory: Callable[..., "DatasetFetcher"],
    ) -> None:
        """Bind a fetcher factory to a dataset id. The factory is called
        when the pipeline needs a fetcher (lazily); arguments are
        passed through verbatim.
        """
        if dataset_id not in self._by_id:
            raise RegistryError(
                f"Cannot register fetcher for unknown dataset {dataset_id!r}; "
                f"known: {sorted(self._by_id)}"
            )
        self._fetcher_factories[dataset_id] = factory

    # `make_fetcher` is defined further down (in the "fetcher access"
    # section); this position is the spec-side of the API.

    # ---- lookup --------------------------------------------------------

    def list_datasets(self) -> list[DatasetSpec]:
        """All registered specs, sorted by id."""
        return sorted(self._by_id.values(), key=lambda s: s.id)

    def __iter__(self) -> Iterator[DatasetSpec]:
        return iter(self.list_datasets())

    def get(self, dataset_id: str) -> DatasetSpec:
        """Look up by id. Raises :class:`RegistryError` on miss."""
        try:
            return self._by_id[dataset_id]
        except KeyError as e:
            raise RegistryError(
                f"No dataset registered with id {dataset_id!r}. Known: {sorted(self._by_id)}"
            ) from e

    def resolve_variable(self, ref: str) -> tuple[DatasetSpec, str]:
        """Map a variable reference like ``"SEIFA.irsd_decile"`` to its
        ``(spec, field_name)``.

        Resolution rule: the namespace is everything before the first
        ``.``; the field is everything after. The namespace is matched
        case-sensitively against each registered spec's
        :attr:`namespace` attribute.

        For GCP the convention is slightly different — every GCP table
        (G01, G02, ...) is its own logical "namespace" but they all
        belong to the single ``gcp`` dataset. The GCP spec
        declares ``namespace: G`` and the registry treats any
        ``G\\d+`` prefix as routing to that dataset.

        Raises :class:`RegistryError` if no dataset claims the
        namespace, or if the ref is malformed.
        """
        if "." not in ref:
            raise RegistryError(
                f"Variable reference {ref!r} has no namespace; expected '<NAMESPACE>.<field>' form."
            )
        namespace, _, field = ref.partition(".")
        if not namespace or not field:
            raise RegistryError(f"Variable reference {ref!r} has empty namespace or field.")

        # Direct namespace match (SEIFA, ERP, DSS, ATO, PRESET, ...).
        for spec in self._by_id.values():
            if spec.namespace == namespace:
                return spec, field

        # GCP-style: the namespace is the table id (e.g. "G02"). Match
        # against any registered dataset whose namespace is "G" — used
        # by the GCP DataPack spec.
        if namespace and namespace[0] == "G" and namespace[1:].isdigit():
            for spec in self._by_id.values():
                if spec.namespace == "G":
                    # Pass the full ref (table.field) so the GCP fetcher
                    # can split it the way the existing catalog expects.
                    return spec, ref

        raise RegistryError(
            f"No dataset registered for namespace {namespace!r}. "
            f"Known namespaces: "
            f"{sorted({s.namespace for s in self._by_id.values()})}"
        )

    # ---- fetcher access -------------------------------------------------

    def make_fetcher(self, dataset_id: str, **kwargs: Any) -> "DatasetFetcher":
        """Construct a fetcher for ``dataset_id`` via its registered
        factory. ``**kwargs`` are passed through to the factory.

        Raises :class:`RegistryError` if no fetcher factory has been
        registered — typically a signal that the dataset module was
        never imported (built-in datasets are imported by
        :mod:`census_augment.datasets`'s ``__init__``).
        """
        if dataset_id not in self._fetcher_factories:
            raise RegistryError(
                f"No fetcher registered for dataset {dataset_id!r}. "
                f"Datasets with factories: {sorted(self._fetcher_factories)}. "
                f"If this is a built-in dataset, ensure its module is imported "
                f"in census_augment.datasets.__init__."
            )
        return self._fetcher_factories[dataset_id](**kwargs)
