"""Derived-feature (PRESET) parser + evaluator (spec §21).

Each feature is described by a markdown file at ``features/<id>.md``
with YAML front-matter declaring the numerator / denominator /
edge-case rules. The evaluator reads those specs, computes ratios
from a SA2-keyed DataFrame of source columns, and surfaces them as
new columns following the conventions in §21.3.

Public API:

- :class:`FeatureSpec` — Pydantic model of the feature front-matter.
- :func:`parse_feature_spec(path)` — parse one ``.md`` file.
- :class:`FeatureRegistry` — index + lookup of all features under
  ``features/`` at the repo root.
- :class:`FeatureEvaluator` — given a DataFrame keyed by
  ``sa2_code_2021`` with the source columns the spec references,
  compute the derived feature column with proper edge-case handling.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_log = logging.getLogger(__name__)


# ---- spec model ---------------------------------------------------------


class _FieldsExpression(BaseModel):
    """Numerator or denominator expression."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expression: Literal["field", "sum", "weighted_sum"]
    field: str | None = None
    fields: list[str] = Field(default_factory=list)
    weights: list[float] = Field(default_factory=list)

    @field_validator("expression")
    @classmethod
    def _validate_for_shape(cls, v: str) -> str:  # pragma: no cover — tiny
        return v


class _EdgeCaseRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    zero_denominator: Literal["null", "zero", "error"] = "null"
    perturbation_tolerance: Literal["warn_only", "strict"] = "warn_only"
    out_of_bounds_behaviour: Literal["clip", "warn", "error"] = "warn"

    @field_validator("zero_denominator", mode="before")
    @classmethod
    def _normalize_zero_denominator(cls, v: object) -> object:
        # YAML `null` parses as Python None; map to the literal "null"
        # string so feature spec authors can write idiomatic YAML.
        if v is None:
            return "null"
        return v


class _SourceCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    note: str = ""


class FeatureSpec(BaseModel):
    """Parsed feature spec file (front-matter + body)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: Literal["proposed", "active", "deprecated"]
    output_kind: Literal[
        "percentage", "ratio", "rate", "scalar", "index"
    ]
    bounds: tuple[float, float] | None = None
    dataset: str | list[str]
    default: bool = False
    tags: list[str] = Field(default_factory=list)
    numerator: _FieldsExpression
    denominator: _FieldsExpression
    edge_cases: _EdgeCaseRules = Field(default_factory=_EdgeCaseRules)
    sources: list[_SourceCitation] = Field(default_factory=list)
    body: str = ""
    source_path: Path | None = None


_FRONT_MATTER_RE = re.compile(
    r"\A---\s*\n(?P<front>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_feature_spec(path: Path) -> FeatureSpec:
    """Parse a feature spec markdown file."""
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError(
            f"{path} is not a valid feature spec: missing YAML front-matter"
        )
    try:
        front = yaml.safe_load(m.group("front"))
    except yaml.YAMLError as e:
        raise ValueError(
            f"{path}: front-matter is not valid YAML: {e}"
        ) from e
    if not isinstance(front, dict):
        raise ValueError(
            f"{path}: front-matter must be a YAML mapping"
        )
    try:
        return FeatureSpec(
            **front, body=m.group("body").strip(), source_path=path
        )
    except Exception as e:
        raise ValueError(f"{path}: invalid feature spec — {e}") from e


# ---- registry -----------------------------------------------------------


def _default_features_dir() -> Path:
    here = Path(__file__).resolve()
    repo_root_candidate = here.parents[2] / "features"
    if repo_root_candidate.is_dir():
        return repo_root_candidate
    package_features = here.parent / "_features"
    return package_features


class FeatureRegistry:
    """Index of all feature specs at ``features/<id>.md``.

    Parses every ``.md`` (skipping leading-underscore templates) under
    the repo's ``features/`` directory at construction. ``get(id)``
    looks up by stable id.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, FeatureSpec] = {}

    @classmethod
    def from_repo_specs(cls, features_dir: Path | None = None) -> FeatureRegistry:
        registry = cls()
        directory = features_dir or _default_features_dir()
        if directory.is_dir():
            for spec_path in sorted(directory.glob("*.md")):
                if spec_path.name.startswith("_"):
                    continue
                try:
                    spec = parse_feature_spec(spec_path)
                except ValueError:
                    _log.exception(
                        "Skipping invalid feature spec at %s", spec_path
                    )
                    continue
                registry._by_id[spec.id] = spec
        return registry

    def list_features(self) -> list[FeatureSpec]:
        return sorted(self._by_id.values(), key=lambda s: s.id)

    def get(self, feature_id: str) -> FeatureSpec:
        try:
            return self._by_id[feature_id]
        except KeyError as e:
            raise KeyError(
                f"No feature registered with id {feature_id!r}. "
                f"Known: {sorted(self._by_id)}"
            ) from e

    def __contains__(self, feature_id: str) -> bool:
        return feature_id in self._by_id


# Singleton registry — loads on import.
features = FeatureRegistry.from_repo_specs()


# ---- evaluator ----------------------------------------------------------


class FeatureEvaluator:
    """Compute a single derived feature from a SA2-keyed DataFrame.

    ``df`` must contain the source columns the spec references (using
    the same ``<NAMESPACE>.<field>`` naming the spec uses, with the
    namespace prefix preserved). The evaluator computes
    numerator / denominator and applies the spec's edge-case rules.

    Returns a Series indexed like ``df``, with the feature's value per
    row. NaN where the denominator is zero (and ``zero_denominator``
    is ``null``); otherwise the computed ratio (× 100 for percentages).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        self._spec = spec

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Compute ``self._spec`` against ``df``; return the feature Series."""
        num = self._sum_expression(df, self._spec.numerator, "numerator")
        den = self._sum_expression(df, self._spec.denominator, "denominator")

        # Element-wise: where den is zero or NaN, output is NaN
        # (under the default zero_denominator='null' policy).
        # Otherwise: num / den, with × 100 for percentages.
        zero_policy = self._spec.edge_cases.zero_denominator

        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = num / den

        if zero_policy == "null":
            ratio = ratio.where(den.notna() & (den != 0))
        elif zero_policy == "zero":
            ratio = ratio.where(den.notna() & (den != 0), 0)
        elif zero_policy == "error":
            if (den == 0).any() or den.isna().any():
                raise ValueError(
                    f"Feature {self._spec.id!r}: denominator zero or NaN; "
                    "edge_cases.zero_denominator='error' refuses to "
                    "produce output."
                )

        if self._spec.output_kind == "percentage":
            ratio = ratio * 100

        # Bounds checking.
        if self._spec.bounds is not None:
            ratio = self._apply_bounds(ratio)

        ratio.name = self._spec.id
        return ratio

    # ---- internals -----------------------------------------------------

    def _sum_expression(
        self,
        df: pd.DataFrame,
        expr: _FieldsExpression,
        which: str,
    ) -> pd.Series:
        if expr.expression == "field":
            field = expr.field or ""
            if not field:
                raise ValueError(
                    f"Feature {self._spec.id!r}: {which}.expression='field' "
                    "but no `field:` value provided"
                )
            return self._field_series(df, field).astype(float)
        if expr.expression == "sum":
            if not expr.fields:
                raise ValueError(
                    f"Feature {self._spec.id!r}: {which}.expression='sum' "
                    "but no `fields:` provided"
                )
            cols = [self._field_series(df, f) for f in expr.fields]
            return sum(c.astype(float) for c in cols)  # type: ignore[return-value]
        if expr.expression == "weighted_sum":
            if not expr.fields or len(expr.fields) != len(expr.weights):
                raise ValueError(
                    f"Feature {self._spec.id!r}: {which}.weighted_sum "
                    "needs equal-length fields/weights"
                )
            total: pd.Series | None = None
            for field, weight in zip(expr.fields, expr.weights, strict=True):
                contribution = self._field_series(df, field).astype(float) * weight
                total = contribution if total is None else (total + contribution)
            assert total is not None
            return total
        raise AssertionError(f"unreachable expression: {expr.expression}")

    def _field_series(self, df: pd.DataFrame, field: str) -> pd.Series:
        """Look up ``field`` (a ``<NAMESPACE>.<field>`` string) in ``df``.

        Tries the full ``namespace.field`` form first, then the bare
        ``field`` (no prefix), then prefixed variants — to be tolerant
        of how upstream code labels the columns.
        """
        if field in df.columns:
            return df[field]
        # Strip namespace prefix.
        if "." in field:
            _, _, bare = field.partition(".")
            if bare in df.columns:
                return df[bare]
        raise KeyError(
            f"Feature {self._spec.id!r}: source column {field!r} not in "
            f"DataFrame. Available: {sorted(df.columns)[:10]}..."
        )

    def _apply_bounds(self, series: pd.Series) -> pd.Series:
        assert self._spec.bounds is not None
        lo, hi = self._spec.bounds
        behaviour = self._spec.edge_cases.out_of_bounds_behaviour
        out_of_bounds = (series < lo) | (series > hi)
        # NaN comparisons are False, so out_of_bounds excludes NaN
        # automatically — good, we don't want to warn for nulls.
        n_out = int(out_of_bounds.sum())
        if n_out == 0:
            return series
        if behaviour == "clip":
            return series.clip(lo, hi)
        if behaviour == "warn":
            _log.warning(
                "Feature %r: %d/%d values outside bounds [%g, %g]. "
                "Returning unclipped values; set "
                "edge_cases.out_of_bounds_behaviour='clip' to clamp.",
                self._spec.id,
                n_out,
                len(series),
                lo,
                hi,
            )
            return series
        if behaviour == "error":
            raise ValueError(
                f"Feature {self._spec.id!r}: {n_out} values outside "
                f"bounds [{lo}, {hi}]"
            )
        return series  # pragma: no cover
