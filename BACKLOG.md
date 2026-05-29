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

### Shipped this session (Day 3)

- **#88** — corrected the stale Phase G "next priority"
  guidance in the previous checkpoint. Phase G was already done in
  v2.0.0 (PR #70).
- **F.6 — SEIFA 2011 + ASGS Edition 1 boundary support**. SEIFA
  2011 release added to the `seifa` dataset's `available_releases`;
  same `.xls` parser as 2016, no core code changes needed.
  `edition_1_spec()` registered for ASGS Edition 1 (2,214 SA2s,
  GDA94). `tools/fetch_real_data.py --edition 1` fetches the
  boundary. GCP 2011 documented as auto-fetch-out-of-scope (ABS
  login wall); a "user-supplied ZIP" fallback path is in the
  backlog for power users.

### Where to pick up tomorrow

Suggested ordering by ROI:

1. **More cross-dataset PRESETs.** Candidates listed in "Future
   PRESET features" below. Each ~30 min of markdown authoring.
   Smallest wins; consolidates yesterday's ERP age/sex unlock
   further. Likely 3-5 PRESETs land in a single PR.

2. **`ERP.population_density_per_km2`.** Needs SA2-area lookup
   wiring. Small new infrastructure, moderate scope (~half day).

3. **User-supplied DataPack ZIP fallback** (for GCP 2011 unlock —
   see new section below). ~2 hours of clean engineering.

4. **Address-retirement awareness** (Phase G refinement,
   spec-temporal.md §17 deferred): "address X existed in 2018,
   retired in 2022; row dated 2020 should hit X even though X is
   missing from the 2025 release." Today an unmatched address
   falls through to fuzzy / Nominatim — same fallback as Phase F.2.
   Marked out-of-scope in the original Phase G design but worth
   revisiting if user demand surfaces.

### Done (no longer "next priority")

- ~~Phase G — G-NAF release-per-row.~~ Shipped in **v2.0.0** via
  PR #70 (commit 063a9c0). The `gnaf_release` output column, per-row
  release dispatch, and `Pipeline.from_config` wiring are all live.
  14 dedicated tests in `tests/test_pipeline_temporal.py` and
  `tests/test_temporal_helpers.py`. Yesterday's checkpoint
  incorrectly promoted this as next priority — it was already in
  the 48 `[Unreleased]` entries that rolled into v2.0.0. Corrected
  to avoid the same loop next session.

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
  - **F.5 — GCP 2011.** Out of scope at the auto-fetch layer: ABS
    gates the 2011 DataPack behind login auth at
    `https://www.censusdata.abs.gov.au/datapacks`. A future
    "user-supplied ZIP" fallback on `DataPacksDataSource` could let
    power users drop a manually-downloaded ZIP into the cache and
    have the rest work; ~2 hours of clean engineering when motivated.
    Tracked separately under "User-supplied DataPack ZIP fallback"
    below.
  - **F.6 — SEIFA 2011** ✅ shipped 2026-05-29. `.xls` via the same
    python-calamine path as 2016; ASGS Edition 1 boundary support
    landed alongside (`edition_1_spec()` in
    `data_sources/_edition.py`). SEIFA 2001/2006 stay out of scope
    (CCD/SLA pre-ASGS geography per spec-temporal.md §17).

## User-supplied DataPack ZIP fallback (unblock GCP 2011 for power users)

ABS gates the 2011 GCP/BCP DataPack behind a login at
`https://www.censusdata.abs.gov.au/datapacks` — no public direct URL.
The augmentor's auto-fetch can't ride that. But the parser is the
same `DataPacksDataSource` machinery that handles 2016 + 2021, so a
power user who manually downloads from the login portal should be
able to drop the ZIP into the cache and use it.

Concrete shape:

- Add a `--local-zip <path>` (or env var) hook on
  `DataPacksDataSource.fetch()` that bypasses the URL fetch and uses
  a user-supplied ZIP from disk.
- Document in `datasets/gcp.md` or a new `docs/historical-data.md`
  page how to obtain the 2011 ZIP and where to drop it.
- ~2 hours of work when motivated by a real user request.

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
