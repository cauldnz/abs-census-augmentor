# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

---

## Session checkpoint — 2026-05-27

State of main is **clean** (669 tests passing, mypy + ruff clean,
zero open issues / PRs at session end). Now at **v2.0.0** —
reconciled `pyproject.toml` with the accumulated `[Unreleased]`
block via PR #86 + the release-cut PR.

### Shipped this session (Day 2)

- **#86** — three cross-dataset PRESETs (DSS + ERP):
  `pct_age_pension_recipients`, `pct_jobseeker_recipients`,
  `welfare_density_index`. First features to span two registered
  datasets; markdown-only authoring (framework already supported).
- **v2.0.0 release cut** — `pyproject.toml` 1.4.2 → 2.0.0.
  CHANGELOG `[Unreleased]` (48 entries) rolled into `[2.0.0]`
  with a "Migration from 1.x" section listing the five
  breaking changes.
- Plus an automatic `demo-publish.yml` run refreshing the GIFs to
  show the 9 PRESETs (was 6).

### Where to pick up tomorrow

Suggested ordering by ROI:

1. **Phase G — G-NAF release-per-row.** Self-contained engineering,
   ~2-3 days. Designed in `spec-temporal.md` §6 + §12. No external
   blocker. The biggest remaining piece of the temporal-mode
   roadmap.

2. **F.5 / F.6 historical datasets.** GCP 2011, SEIFA 2011, SEIFA
   2006/2001 — needs URL discovery (legacy `abs@.nsf` archive) and
   for SEIFA 2011 a design discussion about pre-ASGS geography
   (CCD / SLA). Could pair well with #1 if there's appetite for a
   "full temporal coverage" push.

3. **More cross-dataset PRESETs.** Candidates listed in "Future
   PRESET features" below. Each ~30 min of markdown authoring.

4. **`ERP.population_density_per_km2`.** Needs SA2-area lookup
   wiring. Small new infrastructure, moderate scope.

### Smaller deferred items

- `_template.md` wheel exclusion (4 KB hygiene; design choice in
  this BACKLOG)
- Stale remote / local branch cleanup if anything has accumulated
  again (was thorough yesterday — should still be clean unless new
  worktrees were created)

---

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

The v1.3 catalog ships six PRESETs sourced entirely from `gcp`,
and v1.4 (#18) lands first-class pipeline integration so any config
can write `variables: {pct_renters: PRESET.pct_renters}` directly.
The first three cross-dataset PRESETs shipped 2026-05-27 once the
ERP age/sex columns landed:

- `pct_age_pension_recipients` (DSS + ERP)
- `pct_jobseeker_recipients` (DSS + ERP)
- `welfare_density_index` (DSS + ERP)

Additional candidates worth authoring when there's appetite (each
markdown-only, ~30 minutes per spec):

- `pct_disability_support_pension_recipients` — DSS / ERP.population_15_64
- `pct_parenting_payment_recipients` — DSS (single + partnered) / ERP.population_15_64
- `pct_youth_allowance_recipients` — DSS (other + student) / ERP.population_15_64 (or narrower 15-24 if exposed)
- `pct_commonwealth_rent_assistance_recipients` — DSS / ERP.population_total
- ATO PIA-based ratios (median taxable income vs median household income from GCP, etc.) once a use case surfaces

## Temporal mode follow-ups (deferred Phases F + G)

Single-edition temporal mode shipped in v1.5 via PRs #58–63 (Phases
A–E). Two follow-ups remain, both deliberately deferred since the
headline use case (ASGS Edition 3 transactional data with per-row
release selection) works end-to-end today.

- **Phase F — historical datasets (pre-Edition 3).** Tracked in
  sub-phases:
  - **F.3 — SEIFA 2016** ✅ shipped (v1.6 / PR #75). `.xls` via
    python-calamine + dataset rename `seifa_2021 → seifa`.
  - **F.4 — GCP 2016** ✅ shipped (PR pending merge). 2016 GCP
    DataPack uses the same URL pattern as 2021 (probed live: HTTP 200
    on `2016_GCP_SA2_for_AUS_short-header.zip`). The existing
    `DataPacksDataSource` parser handles 2016 metadata with only
    candidate-list extensions for the sheet names (`Cell descriptors
    information` / `Table number, name, population`) and the SA2 code
    column (`SA2_MAINCODE_2016`). Dataset id `gcp_2021 → gcp`.
  - **F.5 / F.6 — earlier releases** (GCP 2011, SEIFA 2011, ERP 2001+).
    Pre-2016 sources use the legacy `abs@.nsf` archive with less
    predictable URLs. SEIFA 2011 uses CCD/SLA (pre-ASGS) geography —
    needs a separate design discussion before implementation.
    Unblocks cross-edition input spans (combined with the cross-edition
    spatial lookups already sketched in `spec-temporal.md` §9.3 step
    4b — those also need implementing).

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
