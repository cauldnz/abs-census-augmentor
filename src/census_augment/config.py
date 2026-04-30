"""Configuration models and YAML loader for census-augment.

Pydantic v2 schema for the YAML config file (see ``spec.md`` §6).

This module performs *structural* validation only: schema, types, regex
checks on friendly variable names, and shape checks on variable references.
Semantic validation of variable references against the loaded DataPack
metadata (spec.md §6.2) is performed separately once the DataPack catalog
is available.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FRIENDLY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VARIABLE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")
PREFIX_RE = re.compile(r"^[a-z0-9_]*$")

DEFAULT_BOUNDARIES_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files"
)
DEFAULT_DATAPACKS_URL = "https://www.abs.gov.au/census/find-census-data/datapacks/download"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputConfig(_StrictModel):
    # ``path`` is optional — required only by the CLI's ``run`` command.
    # Library users (``Pipeline.augment(df)``) don't need it. See spec §6.1.
    path: Path | None = None
    address_column: str | None = None
    latitude_column: str | None = None
    longitude_column: str | None = None

    @model_validator(mode="after")
    def _validate_locator_columns(self) -> InputConfig:
        has_address = self.address_column is not None
        has_lat = self.latitude_column is not None
        has_lon = self.longitude_column is not None
        if has_lat != has_lon:
            raise ValueError(
                "input.latitude_column and input.longitude_column "
                "must both be set or both omitted"
            )
        if not has_address and not (has_lat and has_lon):
            raise ValueError(
                "input must set at least one of: address_column, "
                "or both latitude_column and longitude_column"
            )
        return self


class OutputConfig(_StrictModel):
    # ``path`` is optional — required only by the CLI's ``run`` command.
    # Library users don't need it. See spec §6.1.
    path: Path | None = None
    prefix: str = "sa2_"

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not PREFIX_RE.match(v):
            raise ValueError(
                f"output.prefix must match {PREFIX_RE.pattern} "
                f"(lowercase letters, digits, underscores); got {v!r}"
            )
        return v


class CensusConfig(_StrictModel):
    year: Literal[2021] = 2021
    level: Literal["SA2"] = "SA2"
    profile: Literal["GCP"] = "GCP"
    region: Literal[
        "AUS", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"
    ] = "AUS"
    descriptor: Literal["short-header", "sequential", "long-header"] = "short-header"
    asgs_edition: Literal[3] = 3
    datum: Literal["GDA2020", "GDA94"] = "GDA2020"


class DataSourcesConfig(_StrictModel):
    boundaries_base_url: str = DEFAULT_BOUNDARIES_URL
    datapacks_base_url: str = DEFAULT_DATAPACKS_URL


class GeocodingConfig(_StrictModel):
    provider: Literal["nominatim"] = "nominatim"
    user_agent: str
    rate_limit_per_second: float = 1.0
    cache_enabled: bool = True

    @field_validator("user_agent")
    @classmethod
    def _user_agent_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "geocoding.user_agent must be a non-empty string (Nominatim policy)"
            )
        return v

    @field_validator("rate_limit_per_second")
    @classmethod
    def _rate_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("geocoding.rate_limit_per_second must be > 0")
        return v


class Config(_StrictModel):
    input: InputConfig
    output: OutputConfig
    census: CensusConfig = Field(default_factory=CensusConfig)
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    geocoding: GeocodingConfig
    variables: dict[str, str]

    @field_validator("variables")
    @classmethod
    def _validate_variables(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("variables must contain at least one entry")
        for friendly, ref in v.items():
            if not FRIENDLY_NAME_RE.match(friendly):
                raise ValueError(
                    f"variable name {friendly!r} is invalid; "
                    f"must match {FRIENDLY_NAME_RE.pattern}"
                )
            if not VARIABLE_REF_RE.match(ref):
                raise ValueError(
                    f"variable {friendly!r} reference {ref!r} is invalid; "
                    f"expected format '<table>.<column>' (e.g. 'G02.Median_age_persons')"
                )
        return v


def load_config(path: Path | str) -> Config:
    """Load and structurally validate a YAML config file.

    Semantic validation of variable references against the DataPack
    catalog (spec.md §6.2) is the caller's responsibility once the
    catalog is available.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"Config file {path} is empty")
    if not isinstance(raw, dict):
        raise ValueError(f"Config file {path} must contain a mapping at the top level")
    return Config.model_validate(raw)
