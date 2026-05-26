# Census Augmentation Tool

Python CLI tool that augments Australian location datasets with ABS Census data at the SA2 statistical area level.

---

## Source of Truth

**`spec.md` is the source of truth for all design decisions.**

- Always consult `spec.md` before making implementation choices.
- If something isn't covered in `spec.md`, ask the user before assuming — do not invent behavior.
- If a design change is needed, update `spec.md` first, then update the code to match. Never let the code diverge silently from the spec.
- The "Resolved Decisions" section of `spec.md` (§14) explains *why* certain choices were made — read it before challenging them.

---

## Real Data First

**Hard rule.** If the tool reads, parses, or otherwise depends on the
shape of an external artifact at runtime — ABS XLSX columns, GCP
DataPack column codes, S3 bucket layout, CKAN response shape, ATO
release filename, anything fetched at runtime — fetch a real sample
first. Build production code AND test fixtures off that sample.

**Never invent the schema** from documentation, naming conventions,
intuition, or what "obviously must" be there.

**Why this section exists.** We've shipped #10, #12, #17, #19, and
#23 — five issues that are all the same bug. Synthetic fixtures
encoded the schema we *thought* was there. Tests passed. Real users
hit "column not found" / "file not in bucket" / "wrong field name"
the moment they ran against actual data. Stop the pattern.

**Operationally:**

1. Before adding a fetcher, parser, dataset spec (`datasets/<id>.md`),
   PRESET (`features/<id>.md`), or anything else that names columns /
   paths / fields from an external source, **run one real fetch**.
   Save a representative slice somewhere reviewable — `tools/`,
   `tests/fixtures/`, or `data/` for larger artifacts (gitignored,
   but the script that re-fetches it lives in `tools/`).
2. Build the synthetic fixtures in `tests/conftest.py` (and anywhere
   else) to mirror that real sample's **exact** column names and
   structure. Synthetic *values* are fine; synthetic *schema* is not.
3. Extend `tools/verify_real_parsers.py` so the new artifact is
   exercised against the live source. This catches drift the day it
   lands and gives the next round of work a known-good probe to run.
4. **If you can't run the fetch right now** — offline session,
   paywalled API, credentials you don't have, the live endpoint is
   down — say so explicitly and ask the user. Don't guess. Don't
   ship code referencing schemas you haven't seen. The cost of
   getting this wrong is large (tests pass, real runs fail later);
   the cost of asking is one chat turn.

**The acid test.** For any external column, field, path, or filename
your code or spec mentions, you should be able to point at one of:

- a fixture / sample file checked in to the repo,
- a `tools/` script that re-fetches the live artifact reproducibly,
  or
- a `verify_real_parsers.py` step that exercises the live source.

If you can't, you're guessing. Stop and fetch — or stop and ask.

---

## Tech Stack

- Python 3.11+
- Package + env management: `uv` (preferred) or `pip + venv`
- Config: `pydantic` v2 + `pyyaml`
- CLI: `typer`
- Spatial: `geopandas`, `shapely`, `pyproj`
- Tabular: `pandas`
- HTTP: `requests`
- Excel metadata: `openpyxl`
- Progress: `tqdm`
- Testing: `pytest`, `pytest-mock`, `responses` (HTTP mocking)

---

## Commands

```bash
# Install (editable + dev extras)
uv pip install -e ".[dev]"

# Run tests
pytest

# Lint + format
ruff check .
ruff format .

# Type check
mypy src/

# Run the tool
census-augment run --config config.yaml

# Discover variables
census-augment discover --search "income"
```

---

## Conventions

- **Type hints everywhere.** `mypy` should pass before committing.
- **Pydantic models** for config and any structured intermediate data.
- **Tests next to features.** Every new module gets a corresponding `tests/test_<module>.py`. No new logic merges without a test.
- **Mock all network calls in tests.** Never hit ABS, Nominatim, or S3 from the test suite. We use `responses` for HTTP mocking and `moto` (`@mock_aws`) for S3.
- **Small functions, pure where possible.** Side effects (I/O, network) live at the edges.
- **Errors should be loud and helpful.** Use Pydantic validation errors with context, not bare exceptions.
- **Logging over print.** Use the `logging` module; the CLI configures handlers.
- **`.ps1` scripts must be pure ASCII.** PowerShell 5.1 (the default on Windows) reads `.ps1` files as Windows-1252 unless they have a BOM, so UTF-8 em dashes / smart quotes / arrows in comments produce mojibake and break the parser. Use plain hyphens, straight quotes, and `->` instead of `→`.
- **`pyproject.toml` version and `CHANGELOG.md` move together.** Whenever you cut a new release section in `CHANGELOG.md` (anything that's not under the `[Unreleased]` heading), update `pyproject.toml`'s `[project].version` to the same value in the same commit. The two drifted between 1.0.0 and 1.2.0 because PRs added CHANGELOG entries without bumping pyproject — anyone installing from `main` got artefacts metadata-tagged as 1.0.0 even though the code had moved on. Don't repeat that.

---

## Branch + PR workflow

**Default: work on a branch, open a PR, merge through GitHub.** Direct commits to `main` are reserved for small patches only — README/doc typo fixes, broken-link repairs, CHANGELOG entries documenting already-merged work, single-bullet `BACKLOG.md` housekeeping. If in doubt, branch.

Substantive work that **must** go through a PR:

- Anything under `src/` (logic, types, imports, fetcher behaviour).
- Anything under `tests/`, `tools/`, `datasets/`, or `features/`.
- Anything under `.github/workflows/` or `.devcontainer/`.
- `pyproject.toml` dependency or version changes.
- Anything that closes a tracked issue or backlog entry.

For the branch + PR path:

1. Branch off `main` with a slug naming the work: `feat/<scope>`, `fix/<scope>`, `docs/<scope>`, `chore/<scope>`, `style/<scope>`.
2. Push the branch, open a PR with a body explaining *why* (not just *what*).
3. Wait for CI green before merging. Don't disable checks to land work — fix the underlying issue.
4. Squash-merge unless the branch's commit history is itself useful review material. Use `gh pr merge <n> --squash --delete-branch` so the source branch is cleaned up.
5. If you rebase a branch with an open PR, force-push with `--force-with-lease` (never plain `--force`), and ask the user to authorize each force-push the auto-mode classifier challenges.

The auto-mode classifier enforces this: pushes to `main`, PR merges, and force-pushes to PR branches all require explicit per-action user authorization. Treat the prompt as the guardrail, not friction — when it fires, pause and confirm before retrying.

---

## What Not To Do

- Do **not** check in anything under `data/` or `cache/`. Per-folder `.gitignore` files handle this; do not override them.
- Do **not** commit secrets, API keys, or any user data files.
- Do **not** implement features that aren't in `spec.md` without asking the user first.
- Do **not** skip writing tests for new logic.
- Do **not** hit live ABS or Nominatim endpoints in unit tests. Use fixtures and mocks.
- Do **not** bypass the variable resolver in `catalog.py` to access DataPack files directly from other modules.
- Do **not** parse the ABS download HTML pages. Filenames are constructed deterministically from config (see `spec.md` §4).
- Do **not** invent column names, file paths, bucket layouts, or any other external schema detail from documentation, intuition, or naming conventions. Fetch a real sample first; see "Real Data First" above. This is the rule that closes #10 / #12 / #17 / #19 / #23 — don't add #N+1 by guessing again.

---

## Tooling and `.claude/`

`.claude/worktrees/<slug>/` is where the Claude Code agent hosts
parallel git checkouts while working on multiple PRs at once. Each
subdirectory is an independent clone of this repo on a different
branch, with its own `pyproject.toml`.

**Hard rule.** Any tool that walks the file tree from the repo
root must be configured to skip `.claude/`. Without that, the tool
will:

1. Re-scan every source file once per active worktree (duplicate
   findings).
2. Honour each worktree's own `pyproject.toml` via nested-config
   discovery — so rules disabled on `main` keep firing inside
   in-flight branch worktrees.

Current exclusions:

| Tool | Where | How |
| --- | --- | --- |
| git | `.gitignore` | `.claude/` |
| ruff | `pyproject.toml` `[tool.ruff]` | `extend-exclude = [".claude/"]` |
| mypy | `pyproject.toml` `[tool.mypy]` | `exclude = ['^\.claude/']` |
| pytest | `pyproject.toml` `[tool.pytest.ini_options]` | `testpaths = ["tests"]` |

When adding a new tool that auto-walks the tree from the project
root (coverage, black, pre-commit, anything that does nested-config
discovery), give it an equivalent exclude in the same commit.
Pattern is documented in
[cauldnz/aus-fuel-forecaster#17](https://github.com/cauldnz/aus-fuel-forecaster/issues/17).

---

## Project Layout

See `spec.md` §5 for the full tree. Key entry points:

- `src/census_augment/cli.py` — Typer entry point (CLI commands)
- `src/census_augment/pipeline.py` — Orchestrates input → geocode → spatial → enrich → output. Two entry points: `Pipeline.run()` (file in/out) and `Pipeline.augment(df)` (DataFrame in/out, library use).
- `src/census_augment/__init__.py` — Public library API surface (spec §18.4).
- `src/census_augment/config.py` — Pydantic config models + YAML loader.
- `src/census_augment/paths.py` — Default cache directory resolution (env var → platformdirs).
- `src/census_augment/catalog.py` — Resolves friendly variable names against DataPack metadata.
- `tests/conftest.py` — Shared fixtures (synthetic SA2 polygons + DataPack ZIP).
- `tools/` — Scripts for verifying parsers against real ABS data (spec §17).

## Two ways the tool gets used

- **CLI** — file in / file out via `census-augment run --config config.yaml`. See spec §11.
- **Library / notebook** — DataFrame in / DataFrame out via `Pipeline.augment(df)` returning an `AugmentResult`. See spec §18.

Both share the same Pipeline implementation; the file-I/O is only at the edges of `Pipeline.run`.

---

## When Stuck

1. Re-read the relevant section of `spec.md`.
2. Check the "Resolved Decisions" log (§14) — your question may already be answered.
3. **For anything involving an external schema** (ABS column names, file structures, bucket layouts, response shapes), follow the "Real Data First" rule above — fetch a real sample, or ask. Never guess.
