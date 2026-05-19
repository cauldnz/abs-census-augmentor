"""Configuration models and YAML loader for census-augment.

Pydantic v2 schema for the YAML config file (see ``spec.md`` §6).

This module performs *structural* validation only: schema, types, regex
checks on friendly variable names, and shape checks on variable references.
Semantic validation of variable references against the loaded DataPack
metadata (spec.md §6.2) is performed separately once the DataPack catalog
is available.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_log = logging.getLogger(__name__)

FRIENDLY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VARIABLE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")
PREFIX_RE = re.compile(r"^[a-z0-9_]*$")
#: Matches GCP-DataPack-shaped variable refs (``G##.<col>`` / ``G##A.<col>``).
#: Used by the Edition 2 guard below; kept here so the regex isn't
#: re-built every config load.
_GCP_VARIABLE_REF_RE = re.compile(r"^G\d+[A-Z]?\.[A-Za-z][A-Za-z0-9_]*$")

DEFAULT_BOUNDARIES_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files"
)
DEFAULT_DATAPACKS_URL = "https://www.abs.gov.au/census/find-census-data/datapacks/download"
DEFAULT_GNAF_S3_BASE_URL = "s3://minus34.com/opendata"
DEFAULT_GNAF_OFFICIAL_BASE_URL = "https://data.gov.au/data/dataset"

#: Recognised ``geocoding.providers`` entries (spec §6.1).
GeocoderName = Literal["gnaf", "nominatim"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputConfig(_StrictModel):
    # ``path`` is optional — required only by the CLI's ``run`` command.
    # Library users (``Pipeline.augment(df)``) don't need it. See spec §6.1.
    path: Path | None = None
    address_column: str | None = None
    latitude_column: str | None = None
    longitude_column: str | None = None
    #: Optional date column for temporal mode (see `spec-temporal.md` §9).
    #: When set, each row picks the dataset snapshot closest to its date,
    #: looked up at the boundary edition the release was compiled
    #: against. When ``None`` (default) the pipeline runs cross-sectionally.
    date_column: str | None = None

    @model_validator(mode="after")
    def _validate_locator_columns(self) -> InputConfig:
        has_address = self.address_column is not None
        has_lat = self.latitude_column is not None
        has_lon = self.longitude_column is not None
        if has_lat != has_lon:
            raise ValueError(
                "input.latitude_column and input.longitude_column must both be set or both omitted"
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
    #: Census / boundary year. 2021 = ASGS Edition 3 (current). 2016 =
    #: ASGS Edition 2 (historical). Older years are not supported — see
    #: ``spec-temporal.md`` §6 for scope.
    year: Literal[2016, 2021] = 2021
    level: Literal["SA2"] = "SA2"
    profile: Literal["GCP"] = "GCP"
    region: Literal["AUS", "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT"] = "AUS"
    descriptor: Literal["short-header", "sequential", "long-header"] = "short-header"
    #: ASGS edition derived from / consistent with ``year``: 2 (Jul 2016 –
    #: Jun 2021) or 3 (Jul 2021 – Jun 2026). See ``spec-temporal.md`` §2.
    asgs_edition: Literal[2, 3] = 3
    #: Geodetic datum. Edition 2 only ships GDA94; Edition 3 ships both.
    #: The combination is cross-validated below.
    datum: Literal["GDA2020", "GDA94"] = "GDA2020"

    @model_validator(mode="after")
    def _validate_year_edition_datum(self) -> CensusConfig:
        """Enforce the valid (year, edition, datum) combinations.

        ABS publishes:

        - 2021 boundaries on Edition 3, in GDA2020 (default) or GDA94.
        - 2016 boundaries on Edition 2, in GDA94 only (GDA2020 didn't
          exist as an ABS-published datum yet at the 2016 Census).
        """
        if self.year == 2021 and self.asgs_edition != 3:
            raise ValueError(
                f"census.year=2021 requires asgs_edition=3 (the current ABS edition); "
                f"got asgs_edition={self.asgs_edition}."
            )
        if self.year == 2016 and self.asgs_edition != 2:
            raise ValueError(
                f"census.year=2016 requires asgs_edition=2 (the historical ABS edition); "
                f"got asgs_edition={self.asgs_edition}."
            )
        if self.year == 2016 and self.datum != "GDA94":
            raise ValueError(
                f"census.year=2016 (ASGS Edition 2) is only published in GDA94; "
                f"got datum={self.datum!r}. Set census.datum=GDA94 or use "
                f"census.year=2021 for GDA2020 boundaries."
            )
        return self


class DataSourcesConfig(_StrictModel):
    boundaries_base_url: str = DEFAULT_BOUNDARIES_URL
    datapacks_base_url: str = DEFAULT_DATAPACKS_URL
    gnaf_s3_base_url: str = DEFAULT_GNAF_S3_BASE_URL
    #: Override the HTTPS endpoint DuckDB and boto3 hit when listing /
    #: streaming G-NAF parquet (remote mode). ``None`` (default) uses
    #: AWS's virtual-hosted style: ``https://{bucket}.s3.amazonaws.com``.
    #: Dotted bucket names (e.g. ``minus34.com``) automatically switch
    #: to path-style on the global endpoint to avoid TLS-cert mismatch.
    #: Set this for S3-compatible mirrors (MinIO, R2, ...) or test
    #: servers — path-style addressing is forced when set.
    gnaf_s3_https_endpoint: str | None = None
    #: Regex applied against the parquet's path *relative* to
    #: ``geoparquet/`` when the legacy / bring-your-own layout is in
    #: use (a single flat parquet at the release root). ``None`` (default)
    #: accepts any flat parquet. ABS / OSM boundary subdirectories are
    #: always skipped automatically. Ignored when the gnaf-loader layout
    #: is detected (``address_principal_census_{year}_boundaries/``
    #: subdirectory present) — the subdirectory itself does the scoping.
    gnaf_parquet_filter: str | None = None
    gnaf_official_base_url: str = DEFAULT_GNAF_OFFICIAL_BASE_URL


class GnafConfig(_StrictModel):
    """G-NAF provider settings (spec §6.1, §19.2)."""

    #: ``cache`` (default), ``remote``, or ``official`` — see §19.2.
    mode: Literal["cache", "remote", "official"] = "cache"
    #: ``"latest"`` (resolved at fetch time) or a 6-digit ``YYYYMM``.
    release: str = "latest"
    #: ``GDA2020`` (default) or ``GDA94``. Should match ``census.datum``;
    #: a config-load WARNING is emitted on mismatch (see §6.1).
    datum: Literal["GDA2020", "GDA94"] = "GDA2020"
    #: Tier 3 (fuzzy) match-score floor in [0.0, 1.0] (spec §19.3).
    fuzzy_threshold: float = 0.85

    @field_validator("release")
    @classmethod
    def _release_format(cls, v: str) -> str:
        if v == "latest":
            return v
        if not (len(v) == 6 and v.isdigit()):
            raise ValueError(
                f"geocoding.gnaf.release must be 'latest' or a 6-digit "
                f"YYYYMM string (e.g. '202602'); got {v!r}"
            )
        return v

    @field_validator("fuzzy_threshold")
    @classmethod
    def _fuzzy_in_unit_interval(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"geocoding.gnaf.fuzzy_threshold must be in [0.0, 1.0]; got {v!r}")
        return v


class NominatimConfig(_StrictModel):
    """Nominatim provider settings (spec §6.1).

    ``user_agent`` is required by Nominatim's usage policy and so is
    only required when ``nominatim`` is actually in
    ``geocoding.providers`` — that cross-field check lives on
    :class:`GeocodingConfig`.
    """

    user_agent: str | None = None
    rate_limit_per_second: float = 1.0

    @field_validator("user_agent")
    @classmethod
    def _user_agent_non_empty(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip():
            raise ValueError(
                "geocoding.nominatim.user_agent must be a non-empty string (Nominatim policy)"
            )
        return v

    @field_validator("rate_limit_per_second")
    @classmethod
    def _rate_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("geocoding.nominatim.rate_limit_per_second must be > 0")
        return v


class GeocodingConfig(_StrictModel):
    """Geocoder chain config (spec §6.1, §7.2).

    ``providers`` is an ordered list — first hit wins. Setting it to
    ``[nominatim]`` reproduces v0.9 behaviour for users who don't want
    G-NAF; ``[gnaf]`` is offline-only.
    """

    providers: list[GeocoderName] = Field(
        # cast via the list literal: mypy sees the bare strings as plain
        # str, not Literal — explicit annotation propagates the Literal
        # type from the parent annotation.
        default_factory=lambda: cast(list[GeocoderName], ["gnaf", "nominatim"])
    )
    cache_enabled: bool = True
    gnaf: GnafConfig = Field(default_factory=GnafConfig)
    nominatim: NominatimConfig = Field(default_factory=NominatimConfig)

    @field_validator("providers")
    @classmethod
    def _providers_non_empty_and_unique(cls, v: list[GeocoderName]) -> list[GeocoderName]:
        if not v:
            raise ValueError(
                "geocoding.providers must contain at least one provider "
                "(e.g. [gnaf, nominatim] or [nominatim])"
            )
        if len(v) != len(set(v)):
            raise ValueError(
                f"geocoding.providers contains duplicates: {v}. "
                "Each provider may appear at most once."
            )
        return v

    @model_validator(mode="after")
    def _nominatim_user_agent_required_if_used(self) -> GeocodingConfig:
        # Nominatim's usage policy requires a unique User-Agent — bail at
        # config-load if the user wired Nominatim into the chain without
        # one, rather than failing later on the first network call.
        if "nominatim" in self.providers and not self.nominatim.user_agent:
            raise ValueError(
                "geocoding.nominatim.user_agent is required when "
                "'nominatim' is in geocoding.providers (Nominatim policy). "
                "Set geocoding.nominatim.user_agent in your config."
            )
        return self


class TemporalResolutionConfig(_StrictModel):
    """Per-dataset resolution override (see ``TemporalConfig.per_dataset``)."""

    resolution: Literal["closest_at_or_before", "closest"] | None = None


class TemporalConfig(_StrictModel):
    """Temporal-mode behaviour (see ``spec-temporal.md`` §9.1).

    Active only when ``input.date_column`` is set. Ignored otherwise.

    Fields:

    - ``resolution`` — global default rule for picking a per-row release.
      ``closest_at_or_before`` (default) → most recent release whose
      coverage window starts ≤ row date. ``closest`` → release whose
      coverage-window midpoint is nearest the row date.
    - ``out_of_range`` — what to do when a row's date predates the
      earliest known release of a touched dataset. ``fail`` (default)
      aborts the run. ``nearest`` clamps to the earliest available
      release with a WARNING per affected row.
    - ``reference_edition`` — the ASGS edition all output ``sa2_code``
      values get reported in. Defaults to the latest known (currently 3
      / 2021). Per-dataset value lookups happen at each release's
      source edition (which may differ from reference) and the §2
      invariant is preserved by per-edition spatial lookups.
    - ``per_dataset`` — overrides for specific datasets, indexed by
      dataset id. For datasets with quarterly cadence (DSS), setting
      ``closest`` often makes more sense than ``closest_at_or_before``.
    """

    resolution: Literal["closest_at_or_before", "closest"] = "closest_at_or_before"
    out_of_range: Literal["fail", "nearest"] = "fail"
    reference_edition: Literal[1, 2, 3, 4] = 3
    per_dataset: dict[str, TemporalResolutionConfig] = Field(default_factory=dict)


class Config(_StrictModel):
    input: InputConfig
    output: OutputConfig
    census: CensusConfig = Field(default_factory=CensusConfig)
    data_sources: DataSourcesConfig = Field(default_factory=DataSourcesConfig)
    geocoding: GeocodingConfig
    variables: dict[str, str]
    #: Temporal-mode tuning. Ignored when ``input.date_column`` is unset.
    #: Defaults are sensible if you just set ``input.date_column`` without
    #: configuring this block.
    temporal: TemporalConfig = Field(default_factory=TemporalConfig)

    @field_validator("variables")
    @classmethod
    def _validate_variables(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("variables must contain at least one entry")
        for friendly, ref in v.items():
            if not FRIENDLY_NAME_RE.match(friendly):
                raise ValueError(
                    f"variable name {friendly!r} is invalid; must match {FRIENDLY_NAME_RE.pattern}"
                )
            if not VARIABLE_REF_RE.match(ref):
                raise ValueError(
                    f"variable {friendly!r} reference {ref!r} is invalid; "
                    f"expected format '<table>.<column>' (e.g. 'G02.Median_age_persons')"
                )
        return v

    @model_validator(mode="after")
    def _warn_on_datum_mismatch(self) -> Config:
        # Spec §6.1: a CRS mismatch between the census boundaries and
        # the G-NAF data is the kind of silent drift that turns up six
        # months later as a weird bug. Log once at config-load.
        if "gnaf" in self.geocoding.providers and self.geocoding.gnaf.datum != self.census.datum:
            _log.warning(
                "Datum mismatch: census.datum=%s but geocoding.gnaf.datum=%s. "
                "These should normally match — silent CRS mismatch can produce "
                "subtle position errors. Set them to the same value to silence "
                "this warning.",
                self.census.datum,
                self.geocoding.gnaf.datum,
            )
        return self

    @model_validator(mode="after")
    def _edition_2_gnaf_not_supported_yet(self) -> Config:
        # Edition 2 (2016) MB correspondence ships per-state — the
        # single-national ``MB_2016_AUST_SHP_GDA94.zip`` that the
        # filename pattern would imply does not exist. Wiring the 8-file
        # state-by-state concat is deferred to a follow-up PR. Fail
        # loudly at config-load rather than 404ing inside the pipeline.
        if self.census.year == 2016 and "gnaf" in self.geocoding.providers:
            raise ValueError(
                "census.year=2016 (ASGS Edition 2) is not yet supported "
                "with the G-NAF geocoder: ABS publishes the 2016 Mesh "
                "Block shapefiles per state/territory, and the §7.3 "
                "fast-path concat across them is deferred. Either set "
                "census.year=2021 or remove 'gnaf' from "
                "geocoding.providers (Nominatim-only with year=2016 is "
                "supported)."
            )
        return self

    @model_validator(mode="after")
    def _edition_2_gcp_variables_not_supported_yet(self) -> Config:
        # Edition 2 GCP DataPack lives at a different URL with a
        # different filename pattern than Edition 3 — registering it is
        # part of Phase F.4 work and not in this PR. Any GCP-shaped
        # variable ref (``G\d+.<col>``) requires a working DataPack
        # download; flag the combo at config-load to keep the failure
        # mode loud and out of the network layer.
        if self.census.year == 2016:
            gcp_refs = [
                (friendly, ref)
                for friendly, ref in self.variables.items()
                if _GCP_VARIABLE_REF_RE.match(ref)
            ]
            if gcp_refs:
                raise ValueError(
                    f"census.year=2016 (ASGS Edition 2) is not yet supported "
                    f"with GCP DataPack variables. Registering the 2016 "
                    f"DataPack is a separate follow-up. The following "
                    f"variables reference GCP tables: "
                    f"{[name for name, _ in gcp_refs]}. Either set "
                    f"census.year=2021 or use only non-GCP variables "
                    f"(SEIFA / ERP / DSS / ABS_PIA / PRESET, once those "
                    f"have year=2016 fetchers wired up)."
                )
        return self


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
