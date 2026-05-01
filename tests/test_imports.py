"""Smoke test: the documented public API is importable from ``census_augment``.

Locks down spec §18.4 — top-level imports promote a small, deliberately
chosen surface. Internal subsystems remain importable from submodules but
are not promoted here.
"""

from __future__ import annotations


def test_public_api_importable() -> None:
    """Every name listed in spec §18.4 must import from the top level."""
    from census_augment import (  # noqa: F401
        AugmentResult,
        CatalogError,
        CensusConfig,
        Config,
        DataSourcesConfig,
        Geocoder,
        GeocodingConfig,
        GnafConfig,
        InputConfig,
        NominatimConfig,
        OutputConfig,
        Pipeline,
        RunSummary,
        VariableCatalog,
        load_config,
    )


def test_all_attribute_lists_full_surface() -> None:
    """``__all__`` must contain exactly the documented names."""
    import census_augment

    expected = {
        # Main entry point
        "Pipeline",
        "AugmentResult",
        "RunSummary",
        # Config schema
        "Config",
        "InputConfig",
        "OutputConfig",
        "CensusConfig",
        "DataSourcesConfig",
        "GeocodingConfig",
        "GnafConfig",
        "NominatimConfig",
        "load_config",
        # Catalog
        "VariableCatalog",
        "CatalogError",
        # Protocols
        "Geocoder",
    }
    assert set(census_augment.__all__) == expected


def test_internal_subsystems_not_promoted() -> None:
    """Internal subsystems should *not* be top-level — importable from
    submodules only."""
    import census_augment

    not_promoted = {
        "BoundariesDataSource",
        "DataPacksDataSource",
        "SpatialIndex",
        "CensusEnricher",
        "NominatimGeocoder",
        "GeocodeCache",
        "GeocodeResult",
    }
    public = set(census_augment.__all__)
    assert not (not_promoted & public)
