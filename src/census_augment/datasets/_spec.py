"""Dataset spec parser: ``datasets/<id>.md`` → :class:`DatasetSpec` (spec §20.1).

Parses a markdown file with YAML front-matter and a markdown body. The
front-matter holds machine-parseable metadata (id, namespace, custodian,
licence, ...); the body holds rationale + a schema table that lists the
variables the dataset exposes.

The schema table is parsed into :class:`VariableSpec` rows so
``census-augment discover --dataset <id>`` can render them and so the
registry can fail loudly if a config references an undeclared variable.

Both parsing and validation surface clear errors — bad markdown, bad
front-matter, missing required fields — so contributors get a useful
message rather than a runtime KeyError when their spec lands.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class VariableSpec(BaseModel):
    """One row of the dataset spec's schema table.

    Mirrors the markdown table format::

        | Variable | Type | Description |
        |---|---|---|
        | `<namespace>.<field>` | int | <description> |

    The ``namespace.`` prefix is stripped; ``field`` carries just the
    bare field name (which the registry maps back to the namespace).
    """

    model_config = ConfigDict(frozen=True)

    field: str
    type: str
    description: str


class TemporalDatasetMetadata(BaseModel):
    """Per-dataset temporal capability declaration (`spec-temporal.md` §9.2).

    Datasets opt into temporal mode by including a ``temporal:`` block in
    their spec markdown front-matter. Datasets without this block fall
    back to their configured single release for every row in temporal-
    mode runs.

    Field reference:

    - ``cadence`` — how often new releases publish. Drives the default
      resolution rule when one isn't explicitly configured.
    - ``cover_basis`` — how to compute a release's coverage window from
      its release-id string.
    - ``release_id_format`` — informational; documents the format
      ``available_releases`` entries take.
    - ``available_releases`` — known release ids the temporal resolver
      can pick from. May be empty for datasets that resolve releases
      dynamically (e.g. ERP/ATO PIA scrape a landing page; DSS queries
      CKAN). For those, the dataset's fetcher exposes a
      ``known_releases()`` helper that returns this list at runtime.
    - ``asgs_edition_by_release`` — maps each release id to the ASGS
      edition it was compiled against. The §2 invariant in
      ``spec-temporal.md`` relies on this — the pipeline does the
      spatial lookup at the edition this map names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cadence: Literal["per_census", "annual", "quarterly", "continuous"]
    cover_basis: Literal[
        "census_reference_date",
        "financial_year_ending",
        "calendar_year_ending",
        "quarter_ending",
    ]
    release_id_format: str
    available_releases: list[str] = Field(default_factory=list)
    asgs_edition_by_release: dict[str, int] = Field(default_factory=dict)

    @field_validator("asgs_edition_by_release")
    @classmethod
    def _validate_editions(cls, v: dict[str, int]) -> dict[str, int]:
        for release_id, edition in v.items():
            if edition not in (1, 2, 3, 4):
                raise ValueError(
                    f"asgs_edition_by_release[{release_id!r}] = {edition} "
                    f"is not a valid ASGS edition (1, 2, 3, or 4)"
                )
        return v


class DatasetSpec(BaseModel):
    """Parsed dataset spec file (front-matter + schema)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    status: Literal["proposed", "active", "deprecated"]
    custodian: str
    licence: str
    update_cadence: str
    geography_level: str
    geography_edition: str
    geography_native: bool
    join_key: str
    landing_page: str
    fetch_size_compressed: str | None = None
    tags: list[str] = Field(default_factory=list)
    namespace: str

    #: Markdown body of the spec, post-front-matter.
    body: str

    #: Variable list parsed from the schema table in the body.
    variables: list[VariableSpec] = Field(default_factory=list)

    #: Optional temporal-capability declaration. When absent, the
    #: dataset is cross-sectional-only — temporal-mode runs use the
    #: dataset's configured `release` for every row with a WARNING.
    temporal: TemporalDatasetMetadata | None = None

    #: Source path (for error messages).
    source_path: Path | None = None

    @field_validator("id", "namespace")
    @classmethod
    def _no_whitespace(cls, v: str) -> str:
        if not v or any(c.isspace() for c in v):
            raise ValueError(f"must be a non-empty whitespace-free token; got {v!r}")
        return v


_FRONT_MATTER_RE = re.compile(
    r"\A---\s*\n(?P<front>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


# Match a markdown table row: leading | + cells + trailing |
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)


# A "Schema" h2 header (case-insensitive — also accept # variants).
_SCHEMA_HEADER_RE = re.compile(
    r"^#{1,3}\s+Schema\b.*?$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_dataset_spec(path: Path) -> DatasetSpec:
    """Read ``path`` and return a fully-validated :class:`DatasetSpec`.

    Raises ``ValueError`` if the file isn't shaped like a spec: missing
    YAML front-matter, malformed front-matter, missing required keys,
    etc. The error message includes the source path so downstream
    callers can point users at the broken file.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        raise ValueError(
            f"{path} is not a valid dataset spec: expected YAML front-matter "
            "delimited by '---' lines at the top of the file."
        )

    front_text = m.group("front")
    body = m.group("body").strip()

    try:
        front = yaml.safe_load(front_text)
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: front-matter is not valid YAML: {e}") from e

    if not isinstance(front, dict):
        raise ValueError(f"{path}: front-matter must be a YAML mapping; got {type(front).__name__}")

    variables = _parse_schema_table(body, namespace=front.get("namespace", ""))

    try:
        return DatasetSpec(
            **front,
            body=body,
            variables=variables,
            source_path=path,
        )
    except Exception as e:  # pydantic.ValidationError or similar
        raise ValueError(f"{path}: invalid dataset spec — {e}") from e


def _parse_schema_table(body: str, *, namespace: str) -> list[VariableSpec]:
    """Extract the schema table from a dataset spec's markdown body.

    The schema is identified by a heading whose text starts with
    ``Schema`` (case-insensitive). The first markdown table after that
    heading is parsed. Each row produces one :class:`VariableSpec`;
    the leading ``namespace.`` prefix is stripped from the variable
    column to keep ``field`` bare.

    Returns an empty list if no schema heading or table is found —
    that's allowed (a dataset with status=proposed and no variables
    yet declared).
    """
    schema_match = _SCHEMA_HEADER_RE.search(body)
    if not schema_match:
        return []

    rest = body[schema_match.end() :]

    # Find the first table (block of lines starting with |).
    table_lines: list[str] = []
    in_table = False
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            in_table = True
            table_lines.append(stripped)
        elif in_table and not stripped:
            # Blank line ends the table.
            break
        elif in_table:
            # Non-table content after a table; stop.
            break

    # Drop separator rows (---), header rows.
    rows: list[list[str]] = []
    for line in table_lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Skip the header separator row (---|---|---).
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)

    if len(rows) < 2:
        # Header but no data rows.
        return []

    # First row is header; rest are data.
    header = [c.lower() for c in rows[0]]
    if header[:3] != ["variable", "type", "description"]:
        return []

    out: list[VariableSpec] = []
    namespace_prefix = f"{namespace}." if namespace else ""
    for row in rows[1:]:
        if len(row) < 3:
            continue
        var_cell, type_cell, desc_cell = row[0], row[1], row[2]
        # Strip the markdown backticks and the namespace prefix.
        bare = var_cell.strip("` ")
        if namespace_prefix and bare.startswith(namespace_prefix):
            bare = bare[len(namespace_prefix) :]
        out.append(
            VariableSpec(
                field=bare,
                type=type_cell,
                description=desc_cell,
            )
        )
    return out
