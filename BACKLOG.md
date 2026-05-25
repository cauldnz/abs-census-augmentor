# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

## VHS terminal recordings (animated demos)

The README embeds VHS-rendered GIFs to show what the tool actually
does. Tape files live at `tools/demo/<slug>.tape`; rendering is
one-command via `tools/demo/render.sh` (or `.ps1`).

### Authored, ready to render

These have tape files committed. Render them via the dev container
(see `.devcontainer/`) or any host with Docker reachable. Output
lands at `docs/<slug>.gif`. Easiest:
`./tools/demo/render.sh --all`.

- **`docs/demo.gif`** — headline demo. Shows mixing GCP + SEIFA in one
  config. Replaces the v1.0-era headline demo.
- **`docs/discover-datasets.gif`** — walks `census-augment discover
  --datasets` / `--dataset seifa` / `--features` and shows the
  underlying markdown spec format. No augmentation run, so cache is
  unused.
- **`docs/preset-features.gif`** — shows a PRESET spec, then a config
  that uses three PRESETs (`pct_renters`, `pct_drive_to_work`,
  `pct_aged_65_plus`), then the computed output. Was deferred when
  PRESETs were broken (#23); unblocked by the v1.4.2 column-ref
  fixes (PR #26).

### Deferred

- **`docs/seifa-augmentation.gif`** — superseded by `docs/demo.gif`
  (the headline) which already shows SEIFA in action; revisit if
  there's appetite for a SEIFA-specific deep-dive demo with
  inner-Sydney-vs-regional contrast.

- **`docs/dss-payments-resolution.gif`** — niche. Shows DSS quarterly
  release resolution via CKAN. Lower priority than the headline
  demos.

### Notes for whoever renders these

- `tools/demo/render.ps1` (Windows) and `tools/demo/render.sh` (macOS
  / Linux / WSL / devcontainer) take an optional slug arg picking
  the tape (default: `demo`), or `--all` to render every tape in
  one batch. Each new demo needs a matching `.tape` file alongside,
  optionally with its own `*.yaml` config (the pre-warm loops over
  every config in the directory).
- Keep individual GIFs to 20–30 seconds and < 500 KB.
- The cache pre-warm pattern in the render scripts handles
  registered datasets; the tape's own `Hide`/`Show` blocks handle
  any final cache touch-ups.

## Future datasets (deferred from #15)

Tracked but not in v1.3 scope. Each is a meaningful piece of work
(per-source fetcher, schema bookkeeping, real-data validation) and
should land as its own PR when the upstream demand surfaces.

- AIHW Geographic Health Atlases (SA3-native primarily; needs SA3
  support first).
- ABS Building Approvals (LGA-native; concordance handling is the
  interesting bit).
- Geoscape Buildings (point-level; aggregation strategy choice).
- ABS National Health Survey (state / capital city level — exception
  rather than rule for SA2 data).
- NSW BOCSAR / VIC Crime Statistics / etc. (single-state datasets;
  state-by-state stitching is a separate engineering problem).

## Future PRESET features (deferred from #11)

The v1.3 catalog ships six PRESETs sourced entirely from `gcp_2021`,
and v1.4 (#18) lands first-class pipeline integration so any config
can write `variables: {pct_renters: PRESET.pct_renters}` directly.
Cross-dataset features (e.g. `pct_age_pension_recipients` =
`DSS.age_pension_recipients` / `ERP.population_65_plus`) are now
implementable end-to-end — the only remaining blocker is:

1. ERP exposing age-band breakdowns (currently total population
   only; spec example file describes age-band columns but the real
   ERP DS0003 is total-only).

When ERP gets age bands, cross-dataset PRESETs become a markdown-only
authoring exercise.

## Temporal mode follow-ups (deferred Phases F + G)

Single-edition temporal mode shipped in v1.5 via PRs #58–63 (Phases
A–E). Two follow-ups remain, both deliberately deferred since the
headline use case (ASGS Edition 3 transactional data with per-row
release selection) works end-to-end today.

- **Phase F — historical datasets (pre-Edition 3).** Register SEIFA
  2016 / SEIFA 2011, GCP 2016 / GCP 2011, ERP 2001 onwards, etc. Each
  needs its own URL bookkeeping (the legacy `abs@.nsf` archive has
  less predictable URLs than the current site), per-edition fetchers
  (probably shared base class + per-edition subclass), and real-data
  verification per `CLAUDE.md`'s Real Data First. Unblocks
  cross-edition input spans (combined with the cross-edition spatial
  lookups already sketched in `spec-temporal.md` §9.3 step 4b — those
  also need implementing). Effort: ~3-5 days.

- **Phase G — G-NAF release-per-row.** Currently temporal-mode uses
  the pipeline's configured G-NAF release for every row, regardless of
  date. Per-row G-NAF resolution needs the bucketing logic extended
  upstream of geocoding (today it sits between geocoding and
  enrichment). DuckDB connection-per-bucket adds memory cost; would
  want to default to quarterly buckets to keep that bounded. Effort:
  ~2-3 days.

Both are designed in `spec-temporal.md`; the implementation is the
pending work. When picking either up, start by reading §6 and §12
of the spec, then walk through the Phase E.2 orchestrator in
`pipeline.py::_enrich_temporal` to understand the pattern.

## Slow `Render demos` CI workflow — partial fix shipped

Original problem: the `.github/workflows/demo-render.yml`
PR-validation step took ~5–7 minutes and triggered on every PR that
touched `pipeline.py` / `enrich.py` / `cli.py` / `datasets/**` /
`features/**` — including all the temporal-mode work that didn't
actually change anything the demos record.

**Shipped (Tier-C-adjacent CI fix):**

- *Tightened `paths:` filter.* Dropped `pipeline.py` and `enrich.py`
  from the trigger list — internal orchestration edits there rarely
  change visible terminal output. PRs that genuinely affect demos
  re-trigger via `workflow_dispatch` (one click in the Actions tab).
- *Cached the ABS data pre-warm.* `actions/cache@v4` now persists
  `~/.cache/census-augment/data/` across runs, keyed on the
  pyproject deps + every YAML config under `tools/demo/`. The
  pre-warm becomes a no-op on cache hit; the actual save when the
  cache misses is the ~50 MB boundary + ~50 MB DataPack + each
  registered-dataset XLSX (~5-10 MB total).

**Still on the table if CI cost regresses:**

- *Pull render out of PR CI entirely.* Keep `demo-publish.yml`
  (manual, run from `main` post-merge) as the only render path;
  PR reviewers wanting to see visual deltas trigger
  `workflow_dispatch` against the PR's tape. Cuts CI cost to zero
  for the common path at the cost of one extra click for the rarer
  "I want to see how this PR changes the demos" review. Worth doing
  if we hit churn on the `paths:` filter or the cache stops paying.

## Other deferred items

- **Exclude `_template.md` from the built wheel.** Hatchling's
  `[tool.hatch.build.targets.wheel.force-include]` bypasses
  per-target `exclude` patterns AND the build-global
  `[tool.hatch.build] exclude` list, so the obvious one-liner
  doesn't take effect. Two viable approaches when someone wants
  to do this properly:
  1. Move `_template.md` out of `datasets/` and `features/` into
     a separate `templates/` (or `docs/spec-templates/`) tree at
     the repo root, then update `spec.md` §5 and the
     "full template at `datasets/_template.md`" reference.
  2. Write a custom hatchling build hook (`hatch_build.py`) that
     drops the templates from the wheel after force-include runs.
  Both are bigger than a typical tidy-up; runtime loaders silently
  skip `_template.md` regardless of where it lives (the leading
  underscore is the contract), so the cost of NOT doing this is
  ~4 KB of wheel space.
- Automated G-NAF S3 fetch is shipped (v1.1). The `gnaf-loader`
  bucket is the canonical default; users who want a different mirror
  can override `data_sources.gnaf_s3_https_endpoint` and
  `data_sources.gnaf_parquet_filter`.
- Messy/fuzzy address test corpus for exercising G-NAF Tiers 2/3
  against real-world ugly inputs. Sourcing AU addresses from a
  *non*-G-NAF source so we test the matcher rather than test G-NAF
  against itself is the design problem.
