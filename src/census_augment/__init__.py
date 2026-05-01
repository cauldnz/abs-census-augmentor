"""Augment Australian location datasets with ABS Census data at SA2 level.

See ``spec.md`` §18 for full library usage documentation. The conventional
notebook entry point is::

    from census_augment import Pipeline

    pipeline = Pipeline.create(
        variables={"median_age": "G02.Median_age_persons"},
        user_agent="my-app/1.0 (me@example.com)",
        latitude_column="lat",
        longitude_column="lon",
    )
    result = pipeline.augment(df)

For full programmatic control, see :class:`Config` and
:meth:`Pipeline.from_config`. For the CLI, see ``census-augment --help``.
"""

from .catalog import CatalogError, VariableCatalog
from .config import (
    CensusConfig,
    Config,
    DataSourcesConfig,
    GeocodingConfig,
    GnafConfig,
    InputConfig,
    NominatimConfig,
    OutputConfig,
    load_config,
)
from .geocoding.base import Geocoder
from .pipeline import AugmentResult, Pipeline, RunSummary

__all__ = [
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
    # Catalog (programmatic discover)
    "VariableCatalog",
    "CatalogError",
    # Protocols (for custom geocoders per spec §13)
    "Geocoder",
]
