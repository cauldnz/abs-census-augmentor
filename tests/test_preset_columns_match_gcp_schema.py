"""Lock-door test: every PRESET's source-column ref exists in the GCP schema.

The companion test ``tests/test_spec_matches_fetcher_columns.py`` (issue #65)
closes the dataset-spec ↔ fetcher loop. This module closes the analogous
PRESET-spec ↔ GCP-DataPack loop.

Each PRESET in ``features/<id>.md`` declares numerator / denominator GCP
column refs (e.g. ``G37.R_Tot_Total``). This test parses every PRESET's
``source_fields()`` and asserts each GCP ref resolves to a column that
exists in the checked-in GCP schema reference dumps under
``tests/fixtures/gcp-schemas/``. Those dumps were captured from a real
2021 GCP DataPack as part of the #23 fix and are the hermetic source of
truth for "what columns the live DataPack actually publishes".

If this test fails:

- **A new PRESET referenced a column that doesn't exist.** Fix the PRESET
  spec to use a real column name. The error message names the bad ref
  and the table it should have lived in; cross-check against the
  matching ``G*.txt`` fixture (which carries the column codes ABS
  publishes for that table).

- **A PRESET references a table not in the fixtures.** Add the table's
  schema dump under ``tests/fixtures/gcp-schemas/<table>.txt`` from a
  real ``census-augment discover --table <table>`` run, then re-run.

- **The GCP DataPack has changed (e.g. ABS published a 2026 edition with
  renamed columns).** Update the fixture dumps from the new release;
  expect PRESET specs to need follow-up edits where ABS renamed the
  underlying column.

The live counterpart to this test lives in
``tools/verify_real_parsers.py::verify_preset_resolution()`` — it
exercises PRESETs against the fetched-from-ABS DataPack. This module
catches the same class of drift at PR time without requiring network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from census_augment.features import features

# Repo-root anchor (matches the convention in
# tests/test_spec_matches_fetcher_columns.py).
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "gcp-schemas"

# Each schema dump has a one-line table header (``Table G##: ...``) and
# then tab-separated column rows (``CODE\tdescription``). The fixture
# files were authored by piping the ``discover --table`` output through
# the same template.
_TABLE_HEADER_RE = re.compile(r"^Table\s+(?P<table>G\d+[A-Z]?)\b")


def _load_schema(table_id: str) -> set[str]:
    """Return the set of column codes for ``table_id`` from the fixture dump.

    Returns an empty set if the fixture file is missing (the caller's
    assertion handles that case loudly).
    """
    path = _FIXTURE_DIR / f"{table_id}.txt"
    if not path.exists():
        return set()
    codes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Table "):
            continue
        # Cell rows are tab-separated: `CODE\tdescription`. The dump
        # uses tab-indenting; preserve only the column code (first
        # whitespace-separated token).
        first_token = stripped.split("\t", 1)[0].split(maxsplit=1)[0]
        if first_token:
            codes.add(first_token)
    return codes


def _gcp_refs() -> list[tuple[str, str, str, str]]:
    """Return ``[(preset_id, ref, table_id, column_code), ...]`` for every
    GCP-shaped reference declared by every registered PRESET.

    Non-GCP refs (cross-dataset features referencing ERP / SEIFA / etc.)
    are filtered out — they're covered by the dataset-spec lock-door
    test, not this one.
    """
    out: list[tuple[str, str, str, str]] = []
    for spec in features.list_features():
        for ref in spec.source_fields():
            if "." not in ref:
                continue
            table_id, _, col = ref.partition(".")
            # Recognise GCP table ids: `G##` or `G##A`/`G##B` variants
            # (e.g. G09A, G09B). Everything else is a non-GCP namespace.
            if not table_id.startswith("G"):
                continue
            rest = table_id[1:]
            # Strip a trailing single letter (A/B variants) before
            # checking the numeric portion.
            digits = rest.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            if not digits.isdigit():
                continue
            out.append((spec.id, ref, table_id, col))
    return out


@pytest.mark.parametrize(
    "preset_id,ref,table_id,column",
    _gcp_refs(),
    ids=[f"{pid}::{ref}" for pid, ref, _, _ in _gcp_refs()],
)
def test_preset_column_exists_in_gcp_schema(
    preset_id: str, ref: str, table_id: str, column: str
) -> None:
    """Each PRESET source-field GCP ref must resolve to a column code
    that appears in the checked-in GCP schema fixture for that table."""
    schema = _load_schema(table_id)
    assert schema, (
        f"PRESET {preset_id} references table {table_id} but no fixture "
        f"exists at tests/fixtures/gcp-schemas/{table_id}.txt. Capture "
        f"one with `uv run census-augment discover --table {table_id}` and "
        f"commit, or fix the PRESET ref."
    )
    assert column in schema, (
        f"PRESET {preset_id}: column ref {ref!r} not in the GCP fixture "
        f"for {table_id} (tests/fixtures/gcp-schemas/{table_id}.txt). "
        f"Either fix the PRESET to use a real column name, or refresh "
        f"the fixture if a new GCP release has renamed columns. "
        f"Available codes in this table (first 10): "
        f"{sorted(schema)[:10]}"
    )


def test_every_registered_preset_has_at_least_one_resolvable_ref() -> None:
    """Coverage guardrail: every PRESET must contribute at least one
    GCP ref the parametrized test above checks.

    A PRESET with zero GCP refs would silently bypass the lock-door (no
    failures, but no coverage either). Today every shipped PRESET is
    GCP-only; a cross-dataset PRESET landing in the future would
    legitimately have zero GCP refs — that case should add itself to
    ``intentionally_non_gcp_presets`` here with a comment.
    """
    intentionally_non_gcp_presets: set[str] = {
        # Cross-dataset PRESETs sourced from DSS + ERP (no GCP refs by
        # design). They get their own lock-door coverage via the
        # registered fetchers' column tests — no GCP catalogue
        # involvement needed.
        "pct_age_pension_recipients",
        "pct_jobseeker_recipients",
        "welfare_density_index",
    }

    gcp_covered = {pid for pid, _, _, _ in _gcp_refs()}
    registered = {spec.id for spec in features.list_features()}

    expected_covered = registered - intentionally_non_gcp_presets
    missing = expected_covered - gcp_covered
    assert not missing, (
        f"PRESET(s) {sorted(missing)} have no GCP column refs but aren't "
        f"declared non-GCP. Either: (a) add at least one GCP source field "
        f"to the PRESET spec, or (b) add the id to "
        f"`intentionally_non_gcp_presets` here with a comment."
    )
