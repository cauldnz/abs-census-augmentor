# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

---

## Session checkpoint — 2026-06-01 (end of day)

State of main is **clean** (758 tests passing, mypy + ruff clean,
zero open issues / PRs at session end). Now at **v2.2.0** — cross-level
dataset support release shipped late on 2026-06-01.

### Shipped since v2.0.0 (2026-05-27)

**Day 3 (2026-05-29):** Phase F.6 — SEIFA 2011 + ASGS Edition 1
boundary support (`edition_1_spec()`, 2,214 SA2s, GDA94). #88 fixed
stale Phase G checkpoint guidance.

**Day 4 (overnight 2026-05-30 → 2026-05-31):** #91 Stage 2 — per-
release GCP DataPacks routing in temporal mode (proper fix building
on Stage 1's loud-error guard); `ERP.population_density_per_km2`
column via `compute_sa2_areas_km2` + `attach_sa2_areas` pattern;
test-vehicle migration ERP → SEIFA for cross-edition xfails.

**Day 5 morning (2026-05-31):** Two critical bug-fix unblocks landed.
**#99** — DSS parser failed on every pre-Q2-2023 release because of
5-digit `SA2_5DIG16` codes; fixed via a bundled static Edition 2
mapping (2,310 entries). Eight more years of quarterly DSS unlocked
(2014-Q3 → 2022-Q4). **#101** — `compute_sa2_areas_km2` crashed on
null pseudo-SA2 geometries in real ABS boundaries; broke every
`Pipeline.create()` since ERP density landed. Fixed with defensive
guards + WARNING on anomalously-high null fraction. **v2.1.0 cut**
mid-day to ship those unblocks for downstream consumers.

**Day 5 afternoon (2026-06-01):** Cross-level dataset thread — four
sequential PRs landing two new datasets and the parent-code +
correspondence infrastructure.

- **PR #104** — `compute_sa2_parent_codes()` foundation helper. Real-
  data finding: the SA2 boundary already carries `SA3_CODE21`,
  `SA4_CODE21`, `GCC_CODE21`, `STE_CODE21` as attributes, so cross-
  level joins onto SA2 are a pure dict lookup — no separate boundary
  fetch needed. `spec.md` §20.7 "Cross-level data" added.
- **PR #105** — `abs_building_approvals` dataset (catalogue 8731.0).
  Was on the "deferred — LGA-native" list; real-data probe showed
  ABS publishes SA2-native per-state cubes directly. 9 metric
  columns, 8 per-state XLSX cubes per monthly release.
- **PR #106** — `aihw_mh_prescriptions` dataset (NMHSPF). First
  cross-level dataset. Published at SA4 (not SA3 as AIHW prose
  suggested) and downscaled to SA2 via the boundary's `SA4_CODE21`
  attribute. 4 metric columns, 10 FYs available.
- **PR #107** — `LgaBoundariesDataSource` + `LgaSa2Correspondence`
  proactive infrastructure for future LGA-only datasets. Area-
  weighted intersection in EPSG:3577 with both downscale directions
  (counts-preserving sum, rates weighted-average) + parquet sidecar
  caching. No consumer wired today.

**Day 5 evening (2026-06-01):**

- **PR #110** — Real-data smokes for ABS BA + AIHW MH added to
  `tools/verify_real_parsers.py` for live-source drift detection.
- **v2.2.0 cut** (PR #109 → tag → GitHub release) — five additive
  entries promoted to `[2.2.0]`, `pyproject.toml` 2.1.0 → 2.2.0.
- **PR #111 / closes #108** — `tools/verify_real_parsers.py`'s
  PRESET source-check was routing cross-dataset refs (DSS.*, ERP.*)
  through the GCP-only `VariableCatalog.resolve()`. Stale check
  predating v2.0.0's cross-dataset PRESETs; fixed by splitting GCP
  refs and non-GCP refs into separate resolver paths. The 8
  "failing" PRESETs all work end-to-end in tests; the bug was in the
  verify script.

### Where to pick up next

No active engineering thread. The cross-level dataset infrastructure
is in place; the next LGA-only dataset that surfaces (NSW BOCSAR crime
stats, state planning indicators, etc.) is a small addition that
plugs into `Pipeline.from_config` + the existing
`LgaSa2Correspondence` machinery.

Candidates by ROI when motivated:

1. **First LGA-only dataset** consumer to validate the correspondence
   end-to-end against real data (e.g. add an "ABS Building Approvals
   at LGA" variant — ABS publishes both SA2 and LGA cubes; we picked
   SA2 in v2.2.0, but LGA would exercise the new correspondence
   pipeline). ~1-2 hours.

2. ~~**User-supplied DataPack ZIP fallback** for GCP 2011 unlock~~.
   Shipped post-v2.3.0. `local_zip` parameter, env var, and pre-staged-
   cache-path support all wired. See CHANGELOG "Added — user-supplied
   DataPack ZIP fallback (GCP 2011 unlock)".

3. **Address-retirement awareness** (Phase G refinement,
   spec-temporal.md §17 deferred): "address X existed in 2018, retired
   in 2022; row dated 2020 should hit X even though X is missing from
   the 2025 release." Today an unmatched address falls through to
   fuzzy / Nominatim. Bigger lift; revisit if user demand surfaces.

4. **More cross-dataset PRESETs / new AIHW datasets / new ABS
   datasets** at the marginal-cost level — each is mostly markdown
   authoring now that the cross-level plumbing is in place.

### Done (no longer "next priority")

- ~~Cross-level dataset support.~~ Shipped in **v2.2.0**: foundation
  helper (`compute_sa2_parent_codes`), ABS Building Approvals dataset
  (SA2-native), AIHW MH Prescriptions dataset (SA4-keyed via the
  foundation), LGA boundary + LGA-SA2 spatial cross-walk infrastructure.
  All four PRs (#104, #105, #106, #107) merged today.

- ~~`ERP.population_density_per_km2`.~~ Shipped in **v2.1.0** via
  `ErpDataSource.attach_sa2_areas()` + `Pipeline.from_config` wiring.
  Density computed from `population_total / SA2 area km²`; SA2 areas
  derived from the boundary GeoDataFrame at pipeline construction via
  `census_augment.spatial.compute_sa2_areas_km2()`.

- ~~Phase G — G-NAF release-per-row.~~ Shipped in **v2.0.0** via
  PR #70 (commit `063a9c0`). The `gnaf_release` output column, per-row
  release dispatch, and `Pipeline.from_config` wiring are all live.
  14 dedicated tests in `tests/test_pipeline_temporal.py` and
  `tests/test_temporal_helpers.py`.

- ~~More cross-dataset PRESETs.~~ All 8 candidates from "Future PRESET
  features" shipped through v2.1.0.

### Smaller deferred items

- `_template.md` wheel exclusion (4 KB hygiene; design choice in
  this BACKLOG)
- LGA boundary smoke missing from `tools/verify_real_parsers.py` —
  PR #110 added smokes for the two new dataset fetchers but the
  bare boundary fetcher (`LgaBoundariesDataSource`) doesn't have
  one. Drift-detection gap of ~15 minutes' fix when motivated.
- Stale remote / local branch cleanup if anything has accumulated
  again (was thorough at v2.1.0 — should still be clean unless new
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
The cross-dataset PRESET catalogue closed out 2026-05-29 once the
ERP age/sex columns landed — seven `DSS + ERP` features shipped:

- `pct_age_pension_recipients`
- `pct_jobseeker_recipients`
- `pct_disability_support_pension_recipients`
- `pct_parenting_payment_recipients`
- `pct_youth_allowance_recipients`
- `pct_commonwealth_rent_assistance_recipients`
- `pct_carer_payment_recipients`
- `welfare_density_index`

That covers every principal income-support / family payment DSS
publishes at SA2 level. Further additions worth considering when
demand surfaces:

- **Narrower-denominator variants.** `pct_youth_allowance_recipients`
  currently uses a working-age denominator; a single-year-age-band
  ERP would let us compute the same against the actual 15-24
  eligibility window. Same trick for any DSP-style metric where
  the eligible-age band differs from `population_15_64`.
- **ATO PIA-based ratios.** Median taxable income vs median
  household income from GCP, share above the income tax threshold,
  etc. — once a use case surfaces.
- **Carer Allowance.** `pct_carer_payment_recipients` only covers
  the income-tested Carer Payment. Carer Allowance is a
  non-income-tested supplementary payment for carers in
  lower-intensity caring roles. Not in the DSS dataset spec
  currently; could be added if there's demand.

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

**Phase G — G-NAF release-per-row** previously appeared here as
deferred work. Shipped in v2.0.0 via PR #70 (commit `063a9c0`);
see the "Done (no longer next priority)" subsection in the session
checkpoint at the top of this file. The remaining `--local-zip`
DataPack fallback (above) is the only "Temporal mode follow-up"
still pending.

The design and implementation rationale for the temporal-mode
orchestration lives in `spec-temporal.md` §6 + §12 + §13.

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
