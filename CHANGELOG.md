# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For *design* decisions and rationale, see [`spec.md`](spec.md) §14
(Resolved Decisions). This changelog is the user-facing complement.

## [Unreleased]

### Fixed — #99: DSS parser fails on pre-Q2-2023 releases ("No SA2 data rows")

`DssDataSource._parse_xlsx` raised `RuntimeError: No SA2 data rows`
when given a DSS Payment Demographic XLSX from December 2022 or
earlier. Root cause (probed via real CKAN downloads per the
"Real Data First" rule in `CLAUDE.md`): pre-Q2-2023 DSS releases
publish SA2 codes in the 5-digit `SA2_5DIG16` form (e.g. `11007`
Braidwood) rather than the 9-digit `SA2_MAIN16` form (`101021007`)
adopted from Q2-2023 onwards. The parser's `len(sa2) == 9` guard
silently skipped every data row, leaving zero records.

**What changed:**

- New bundled static mapping
  `src/census_augment/datasets/_dss_sa2_5digit_edition_2.py` — 2,310
  entries covering every ASGS Edition 2 SA2, generated from the live
  ABS Edition 2 boundary file. Edition 2 codes are frozen, so the
  mapping never goes stale.
- `_parse_xlsx` now detects 5-digit codes and converts them to the
  9-digit form via the bundled mapping. Unknown 5-digit codes are
  logged via `logging.warning` and skipped (consistent with how the
  parser already handles malformed rows). 9-digit codes pass through
  unchanged.
- The mapping is imported lazily inside `_parse_xlsx` so callers
  that never parse a pre-Q2-2023 file don't pay the ~63 KB cost.
- New `tools/generate_dss_sa2_5to9.py` regenerates the mapping
  reproducibly from `BoundariesDataSource(year=2016, asgs_edition=2)`.
  Verifies zero collisions (none in real Edition 2 data — every
  5-digit code maps to a unique 9-digit code).

**Verification (live data):**

- December 2022 (Q4) DSS file: 2,292 SA2s parsed (was 0); Braidwood
  (`101021007`) has 510 Age Pension recipients.
- December 2024 DSS file: 2,454 SA2s parsed — no regression on the
  9-digit format.

**Tests:**

- `test_parse_xlsx_converts_5digit_sa2_codes_to_9digit` — synthetic
  XLSX with `"11007"` / `"11008"` codes; asserts conversion to
  `"101021007"` / `"101021008"` in the resulting index.
- `test_parse_xlsx_handles_mixed_5digit_and_9digit_codes` — mixed
  formats in one workbook, both end up in the index.
- `test_parse_xlsx_unknown_5digit_code_logs_warning_and_skips` —
  uses `"99999"` (not in Edition 2); asserts warning logged, row
  skipped, known SA2 still present.

Pre-Q2-2023 DSS releases (2014-Q3 through 2022-Q4) are now usable
in temporal mode. `dss_payments.md` already routes them to the
Edition 2 GCP pair via `asgs_edition_by_release`; the cross-edition
spatial lookup produces 9-digit Edition 2 codes that join cleanly
with the parser's converted output.

### Fixed — #91 Stage 2: per-release GCP DataPacks routing in temporal mode

Completes the architectural fix for cross-edition GCP in temporal
mode. PR #94 shipped Stage 1 (loud error replacing silent NaN); this
PR wires the proper per-release routing so 2016-era rows actually
get values from the 2016 DataPack rather than failing.

**What changed:**

- `Pipeline.__init__` gains two optional kwargs:
  - `extra_gcp_datapacks: dict[str, tuple[DataPacksDataSource, VariableCatalog]]`
    — pre-populated per-release pairs (tests use this).
  - `gcp_datapacks_factory: Callable[[str], tuple[DataPacksDataSource, VariableCatalog]]`
    — lazy constructor for per-release pairs (production wires this via
    `from_config`).
- `Pipeline.from_config` builds the factory automatically, closing
  over `data_dir` and the configured base URL. The factory respects
  F.4's 2016 = short-header constraint and uses the user's configured
  descriptor for 2021.
- New `_get_gcp_datapacks(release)` helper — cache-first, factory-
  second, clear `RuntimeError` if both are missing.
- `_enricher_for_bucket` consults `_get_gcp_datapacks(release)` when
  the bucket's resolved GCP release differs from the configured
  default. The sub-enricher then reads from the right release's
  DataPacks + catalog.
- `_variables_for_datasets` drops the stale `include_gcp_and_preset`
  shortcut for GCP. GCP variables now route by their resolved
  release's source edition like every other registered dataset.
  PRESETs remain always-reference-edition per spec-temporal.md §9.
- The Stage 1 loud-error guard at the top of `_enrich_temporal` is
  removed — its job is done. The defensive `RuntimeError` from
  `_get_gcp_datapacks` covers test-built Pipelines that omit the
  factory + extras.

**Side effects:**

- The `gcp_sa2_code_source` column now correctly emits the source-
  edition SA2 code for non-reference GCP releases (Edition-2 codes for
  2016 rows). Stage 1 left this column populated with the
  reference-edition code because the orchestrator never invoked GCP
  per-edition; with the per-release path live, the column is
  meaningful.

**Tests:**

- `test_temporal_gcp_cross_edition_raises_when_no_factory_wired`
  replaces the Stage 1 test. Verifies the defensive `RuntimeError`
  fires from `_get_gcp_datapacks` when a hand-constructed Pipeline
  omits both the factory and the extras.
- `test_temporal_gcp_cross_edition_succeeds_with_per_release_extras`
  is the success-path companion. A two-row input straddling
  2017 + 2023 routes correctly to the 2016 and 2021 DataPacks
  respectively, with the values encoded by the stub enricher to
  prove the right DataPack was used per row.
- The existing `test_temporal_gcp_no_cross_edition_runs_cleanly`
  is unchanged — it exercises the no-routing-needed path.

### Added — `ERP.population_density_per_km2` column

The last item from the ERP wishlist in `datasets/erp_by_sa2.md`.
Density = `population_total / SA2 area (km²)`. Density values
range from <50/km² (remote SA2s) to >25,000/km² (inner-city
Sydney CBD).

**How it's computed:**

- `Pipeline.from_config` computes an SA2 code → area-in-km² lookup
  once per run from the already-loaded boundary GeoDataFrame, using
  the new `census_augment.spatial.compute_sa2_areas_km2()` helper
  (reprojects to EPSG:3577 / Australian Albers Equal Area Conic).
- The areas dict is passed to `CensusEnricher` via a new optional
  `sa2_areas_km2` kwarg, then threaded to the ERP fetcher at
  ``_make_fetcher`` time via the new
  `ErpDataSource.attach_sa2_areas(areas)` method.
- `ErpDataSource.load()` adds the `population_density_per_km2`
  column when areas are attached; omits it otherwise (keeps the
  fetcher standalone-usable for callers without a boundary).

**Temporal mode:** the density column reflects the same release-
projection as `population_total` — a row dated 2017 gets density
computed from `population_history_2017 / area_km2`. SA2 areas
themselves are on the reference edition (ABS doesn't publish per-
edition area lookups; the spatial drift between editions is small
enough that one area lookup serves all releases).

**Tests:**

- 4 new tests in `test_dataset_erp.py`:
  - `test_load_emits_population_density_when_areas_attached`
  - `test_load_omits_population_density_without_attach`
  - `test_load_emits_density_for_historical_release` — locks the
    interaction with the #92 historical-year projection.
  - `test_load_density_nan_for_sa2_missing_from_areas` — partial-
    coverage area lookup produces NaN density (never crashes).
- 4 new tests in `test_spatial.py` covering
  `compute_sa2_areas_km2()`: basic shape, Albers-projection sanity
  check (1° box at 32°S ≈ 10,400 km²), unknown-column raise,
  no-CRS raise.

### Tests — migrate cross-edition vehicle ERP → SEIFA (closes #92 xfails)

The three `@pytest.mark.xfail` markers in
`tests/test_pipeline_temporal.py` introduced alongside the #92 fix
are removed. The tests they covered are now reimplemented using
SEIFA as the cross-edition vehicle.

**Background.** The #92 fix corrected the ERP spec — all ERP
releases map to ASGS Edition 3 (ABS re-aggregates back-data onto
the current boundaries via concordance). That correction made ERP
unsuitable as a multi-edition test vehicle, and the three orchestrator
tests that genuinely exercised cross-edition behaviour through ERP
became architectural orphans (xfailed with a note pointing at SEIFA
as the proper vehicle for the follow-up).

This PR is that follow-up. The tests now use SEIFA, which genuinely
spans Edition 1 (2011) + Edition 2 (2016) + Edition 3 (2021) per F.6.
A 2018-dated row resolves to SEIFA 2016 / Edition 2 and exercises
the cross-edition orchestrator's per-source-edition fan-out, missing-
edition error path, and per-dataset `<dataset>_sa2_code_source`
column emission against a spec that matches reality.

New helpers in `test_pipeline_temporal.py`:

- `_make_seifa_config` — variant of `_make_config` that uses a
  SEIFA variable (`SEIFA.irsd_score`).
- `_make_seifa_pipeline` — SEIFA-vehicle Pipeline with a stubbed
  enricher that encodes the bucket's SEIFA release as the IRSD score
  so tests can assert per-bucket routing.

Tests migrated (xfail removed, assertions adjusted for SEIFA):

- `test_temporal_cross_edition_raises_without_spatial_index`
- `test_temporal_cross_edition_succeeds_with_extra_spatial_index`
- `test_temporal_mixed_edition_buckets`

No production-code changes — pure test-vehicle migration.

### Fixed — #92: ERP temporal-release resolution via historical-year projection

Temporal-mode runs that requested `ERP.*` variables for rows whose
`date_column` resolved to a non-latest publication year raised:

```
RuntimeError: ERP release '2017' not found. Available: ['2024']
```

**Root cause.** ERP has a fundamentally different release shape from
SEIFA / GCP / DSS. ABS publishes ONE annual workbook per cycle that
carries the full 2001-onwards history in `population_history_<year>`
columns. There's no separate "ERP 2017 workbook" on the ABS site —
historical data lives inside the latest publication. The temporal
resolver was treating ERP like SEIFA (one snapshot per release year)
and failing on any non-latest year.

**Fix.** ErpDataSource now serves any historical year ≤ latest via
column projection at `load()` time:

- The fetcher resolves the latest workbook URL regardless of the
  requested release; `_physical_release_year` tracks the actual
  workbook year, `_resolved_release` tracks the logical (possibly
  historical) year the caller asked for.
- `_xlsx_path` / `_parquet_path` overrides use the *physical* year so
  every historical release shares the same on-disk cache — no
  duplicate downloads.
- `load()` projects: when `_resolved_release` ≠ `_physical_release_year`,
  swaps `population_total` from `population_history_<release>` and sets
  `reference_year` to the requested year.
- Age/sex columns are sourced from the latest 3235.0 publication only
  and have no historical breakdown. For historical releases the
  fetcher nulls them out so users don't pair (e.g.) 2017 totals with
  2024 demographics. PRESETs depending on age/sex columns
  (`pct_age_pension_recipients`, etc.) therefore produce NaN for
  historical ERP releases — documented in `datasets/erp_by_sa2.md`.
- Requests for years more recent than the latest workbook still raise
  loudly — they're genuinely out-of-range.

**Spec change (related).** `datasets/erp_by_sa2.md`'s
`available_releases` now exposes the full 2001-onwards historical
range. The `asgs_edition_by_release` mapping changes:
**all years map to Edition 3** (was 2016-2021 → Edition 2,
2022-2024 → Edition 3). This reflects what ABS actually publishes —
back-data is re-aggregated onto the current ASGS edition via internal
concordance. The augmentor reads what ABS ships; it doesn't apply
additional correspondence to recover original-edition geometry.

**Test-vehicle side effect.** Three `test_pipeline_temporal.py`
tests were structurally using ERP as the multi-edition test vehicle
based on the previous (incorrect) spec. With the spec corrected,
those tests no longer trigger cross-edition behaviour. They're
`@pytest.mark.xfail` with clear notes pointing at SEIFA (which
genuinely spans Editions 1, 2, 3 per F.6) as the proper vehicle for
the migrated tests in a follow-up PR. The orchestrator's
cross-edition correctness remains covered by SEIFA-based tests.

Four new tests in `test_dataset_erp.py`:

- `test_load_with_historical_release_projects_population_total` —
  verifies a 2017 request projects from `population_history_2017`.
- `test_load_with_historical_release_nulls_age_sex_columns` — locks
  in the null-age/sex behaviour.
- `test_load_with_historical_release_outside_coverage_raises` — a
  year ≤ latest but not in workbook history raises with the right
  message.
- `test_load_latest_release_unchanged_by_projection` —
  regression-prevention: `release="latest"` returns data unchanged.

### Fixed — #91 Stage 1: loud error replacing silent NaN for GCP cross-edition

Temporal-mode runs that requested GCP variables for rows whose
`date_column` resolved to the **2016 GCP release** silently returned
NaN. The output carried a misleading `gcp_release="2016"` annotation
and a `gcp_sa2_code_source` column pointing at the reference-edition
SA2 code rather than the source-edition (Edition 2) code the 2016
DataPack is keyed by.

**Root cause** (traced in issue #91 root-cause comment):
`_variables_for_datasets`'s `include_gcp_and_preset` shortcut and
`_enricher_for_bucket`'s singleton `DataPacksDataSource` were never
updated when F.4 (PR #81) registered GCP 2016. The orchestrator
unconditionally routes GCP variables to the reference-edition
sub-enricher, which uses Edition-3 SA2 codes against the 2016
DataPack. SEIFA / ERP / DSS aren't affected because they go through
the registered-fetcher per-release path that F.2 wired correctly.

**Stage 1 (this PR):** detect the bad combination
(`temporal mode + GCP variable + non-reference GCP release`) at the
start of `_enrich_temporal` and raise a clear `ValueError` listing
the affected variable names, resolved releases, ASGS editions, and
two concrete workarounds (drop GCP variables, or constrain date
range). Loud failure with intent is strictly better than silent NaN.

PRESETs are unaffected because spec-temporal.md §9 explicitly
declares them as always-reference-edition; the orchestrator's
existing handling of PRESETs as reference-edition-only is correct.

The cross-dataset PRESETs from PR #86 / #90 / #93 don't touch GCP
and aren't affected by this specific bug. They hit #92 instead
(ERP temporal-release resolution — separate fix).

**Stage 2 (follow-up):** per-release `DataPacksDataSource`
construction + factory wiring, mirroring how SEIFA's per-release
fetchers work. Tracked on issue #91; estimated half-day to a day
of focused implementation.

Two new tests in `test_pipeline_temporal.py`:

- `test_temporal_gcp_cross_edition_raises_loud_error` — verifies a
  2017-dated row + `G01.Tot_P_P` variable raises with the right
  message.
- `test_temporal_gcp_no_cross_edition_runs_cleanly` —
  regression-prevention companion. A 2023-dated row + GCP variable
  on the reference edition runs without hitting the guard.

### `pct_carer_payment_recipients` PRESET + BACKLOG cleanup round 2

Two doc / spec changes:

- New PRESET spec `pct_carer_payment_recipients` —
  `DSS.carer_payment_recipients / ERP.population_15_64`. The Carer
  Payment incidence among working-age residents. Skipped in PR #90's
  initial close-out because "carer-vs-cared-for framing needs more
  thought"; the new spec defends the working-age-resident-of-carer
  framing explicitly and notes the alternative (a high-care-need
  population denominator) isn't available at SA2 on the open ABS
  portal. Brings the cross-dataset PRESET catalogue from 7 → 8 and
  the total PRESET count from 13 → 14.

- BACKLOG cleanup round 2. PR #88 corrected the "Phase G next
  priority" wording in the session checkpoint but missed a second
  stale Phase G mention deeper in the file under "Temporal mode
  follow-ups (deferred Phases F + G)". This PR removes that
  duplicate stale entry and replaces it with a pointer to the
  "Done (no longer next priority)" subsection at the top of the
  file.

Lock-door + wheel tests extended for the new PRESET id; spec.md
§21.4 cross-dataset list extended; BACKLOG "Future PRESET features"
updated (Carer Payment shipped; Carer Allowance candidate noted in
its place since the income-tested vs non-income-tested distinction
is now relevant to the catalogue).

### Four more cross-dataset PRESETs (DSS + ERP) — catalogue close-out

Four additional cross-dataset PRESET specs landed in `features/`,
completing the principal income-support / family payment coverage
from DSS at SA2 level:

- `pct_disability_support_pension_recipients` —
  `DSS.disability_support_pension_recipients / ERP.population_15_64`.
  Working-age DSP incidence; principal long-term disability uptake
  measure.
- `pct_parenting_payment_recipients` —
  sum of `DSS.parenting_payment_single_recipients` +
  `DSS.parenting_payment_partnered_recipients` /
  `ERP.population_15_64`. Composite parenting-payment incidence
  (sums both single and partnered streams since they answer
  related questions about the same low-income-families-with-young-
  children population).
- `pct_youth_allowance_recipients` —
  sum of `DSS.youth_allowance_other_recipients` +
  `DSS.youth_allowance_student_and_apprentice_recipients` /
  `ERP.population_15_64`. Under-22 analogue of
  `pct_jobseeker_recipients`.
- `pct_commonwealth_rent_assistance_recipients` —
  `DSS.commonwealth_rent_assistance_recipients / ERP.population_total`.
  CRA-supported private-rental incidence (uses population_total
  rather than working-age because CRA spans all income-support
  payment types from Youth Allowance through Age Pension).

Each spec includes the same "Why this denominator / Why not X /
Edge cases / Bounds (typical)" sections as the first three —
particularly important for the parenting-payment and youth-allowance
sums where the denominator framing is a real design choice that
downstream consumers might want to override.

The lock-door in `test_preset_columns_match_gcp_schema.py`
extended (`intentionally_non_gcp_presets` now lists all seven
cross-dataset PRESETs). The `test_wheel_bundles_specs.py` FEATURES
assertion updated to include all 13 PRESETs (six GCP + seven
cross-dataset).

`spec.md` §21.4 + `BACKLOG.md` "Future PRESET features" both
updated. BACKLOG marks the cross-dataset catalogue as closed-out;
new candidates ("Carer Payment incidence", "ATO PIA-based ratios",
narrower-denominator variants once single-year ages are available)
documented for future authoring sessions.

### Temporal Phase F.6 — SEIFA 2011 release + ASGS Edition 1 boundary support

Extends the `seifa` dataset with the 2011 release (ASGS Edition 1) and
registers Edition 1 as a first-class boundary edition in the temporal-
mode multi-edition orchestrator.

**What changed:**

- `datasets/seifa.md` `available_releases` extended with `"2011"`;
  `asgs_edition_by_release` maps `"2011": 1`.
- `DEFAULT_SEIFA_2011_URL` in `_seifa.py` points at the live ABS
  Lotus Notes openagent URL captured 2026-05-29 (catalogue
  2033.0.55.001, UNID `76D0BC44356DC34ACA257B3B001A4913`, document
  dated 12.11.2014). The 2011 filename convention is
  `2033.0.55.001 SA2 Indexes.xls` (different casing/spacing from the
  2016 `2033055001 - sa2 indexes.xls` — ABS changed the convention
  between releases).
- The existing `_parse_grids` parser handles 2011 with **zero code
  changes**. SEIFA's sheet layout (Contents, Table 1-6, Explanatory
  Notes) is stable across 2011/2016/2021. The SA2 index column name
  is now `sa2_code_2011` for the 2011 release.
- `_xlsx_path` extension selection generalised: `.xlsx` for 2021,
  `.xls` for both 2011 and 2016.
- `_read_grids` dispatches python-calamine for any non-2021 release
  (handles 2011 and 2016 .xls identically).
- New `edition_1_spec()` in `data_sources/_edition.py` returns the
  ASGS Edition 1 boundary descriptor: filename
  `1270055001_sa2_2011_aust_shape.zip`, SA2 columns `SA2_MAIN11` /
  `SA2_NAME11`, GDA94 datum (no GDA2020 — pre-dates it). Live-probed
  2026-05-29: 2,214 SA2 polygons, CRS EPSG:4283.
- `BoundaryEditionSpec.edition` Literal extended to include 1;
  `year` Literal extended to include 2011.
- `edition_spec_for(year=2011, ...)` validator added — only accepts
  `datum="GDA94"` (Edition 1 is GDA94-only).
- `tools/fetch_real_data.py --edition 1` fetches the SA2 boundary
  only. The 2011 GCP/BCP DataPack is **not** auto-fetchable — see
  "Out of scope" below.
- `tools/verify_real_parsers.py` extended:
  - SEIFA probe now exercises the 2011 release alongside 2016/2021.
    Asserts ~2,100 SA2 rows, IRSD score range 554-1196, index column
    `sa2_code_2011`.
  - New Edition 1 boundary probe: self-skipping when no cache, asserts
    schema (`SA2_MAIN11` / `SA2_NAME11`), CRS GDA94, ~2,214 polygons.

**Tests (5 new):**

- `test_seifa_2011_uses_correct_url`
- `test_seifa_2011_sa2_index_name` (locks in `sa2_code_2011`)
- `test_seifa_2011_filename_has_xls_extension`
- `test_fetch_2011_downloads_to_xls_path`
- `test_parse_grids_2011_layout` (locks the synthetic-grid parser
  produces an `sa2_code_2011`-indexed DataFrame with all 4 indexes)

Plus `test_supported_releases` extended to accept 2011 alongside
2016/2021 and reject pre-ASGS years (2006).

**Live-verification numbers (against the real ABS file):**

- SEIFA 2011 SA2 file: 2.4 MB .xls, 2,110 parsed SA2 rows × 46 columns
- IRSD score range: 554 — 1,196 (within ABS's mean-1000-sd-100 spec)
- Sample SA2 `101011001` (Goulburn NSW): IRSD score 928 → decile 2
  (consistent with regional NSW disadvantage)
- ASGS 2011 SA2 boundary: 47.7 MB ZIP, 2,214 polygons, GDA94 / EPSG:4283

**Out of scope (deliberately):**

- **GCP 2011 / BCP 2011 DataPack**: requires login at
  `https://www.censusdata.abs.gov.au/datapacks`. No public direct URL
  exists at any ABS endpoint (verified by probing multiple URL
  patterns + the live datapacks home page on 2026-05-29). Auto-fetch
  is impossible without bundling auth credentials. A future
  "user-supplied ZIP" fallback path on `DataPacksDataSource` could
  unblock this for power users; tracked in BACKLOG.
- **MB Edition 1 correspondence**: same per-state-shapefile challenge
  as Edition 2. Deferred.
- **SEIFA 2001 / 2006**: pre-ASGS geographies (CCD/SLA). Per
  spec-temporal.md §17 these stay out of scope.

## [2.0.0] - 2026-05-27

Major-version cut. The 17 weeks since v1.4.2 (2026-05-10) shipped the
**temporal-spatial capability** (per-row dataset snapshot selection
with boundary-edition correctness), **historical-data expansion**
(SEIFA 2016, GCP 2016, ERP age/sex columns), the **first cross-dataset
PRESETs**, plus extensive architectural simplification, devcontainer
hardening, and CI / docs polish.

### Migration from 1.x

Five breaking changes accumulated. Each is loud at config-load /
runtime — none silently mis-behave.

1. **Dataset id `gcp_2021` → `gcp`** (Phase F.4). The dataset now
   covers both 2016 and 2021 releases. Variable refs (`G02.*`, etc.)
   are unchanged. If you reference the dataset by id (e.g.
   `census-augment discover --dataset gcp_2021`,
   `Pipeline.augment(..., touched_datasets={"gcp_2021"})`,
   `_release` output columns), update to `gcp`.

2. **Dataset id `seifa_2021` → `seifa`** (Phase F.3). Same pattern —
   the dataset now covers 2016 and 2021. `SEIFA.*` variable namespace
   unchanged.

3. **Dataset id `ato_personal_income` → `abs_personal_income`**
   (Temporal Phase C). The variable namespace changed from `ATO.*`
   → `ABS_PIA.*`. Update both id-based and namespace-based refs.

4. **Cache directory layout: flat → per-ASGS-edition subdirs**
   (Temporal Phase D). The on-disk cache moves from
   `<cache>/boundaries/*.shp` to `<cache>/boundaries/<year>/*.shp`,
   and similarly for `mb/` and `census/`. No auto-migration —
   clear the cache (`rm -rf ~/.cache/census-augment/data` or the
   platform equivalent) and re-run `census-augment fetch` to
   repopulate. Dataset-specific caches (`seifa/`, `erp_by_sa2/`,
   `dss_payments/`, `abs_personal_income/`) already use per-release
   filenames and don't need wiping.

5. **Devcontainer no longer mounts the host Docker socket.** Only
   relevant if you used `tools/demo/render.sh --docker` from inside
   the devcontainer. Native vhs / ttyd / ffmpeg now ship in the
   container; `render.sh` defaults to `--local` mode. The
   `--docker` escape hatch is a maintainer-only diagnostic best
   run from the host.

### Highlights

- **Temporal mode** — set `input.date_column` to enable per-row
  release selection. Each row's dataset values are looked up at the
  release's contemporaneous ASGS boundary edition. Cross-sectional
  mode is the default and unchanged. Designed in `spec-temporal.md`;
  ships Phases B-H + F.1-F.4 + G.
- **Historical-data expansion** — SEIFA 2016 (`.xls` via
  python-calamine) + GCP 2016 (same DataPack parser) both registered
  alongside their 2021 counterparts. ERP age/sex columns
  (`population_male/female/0_14/15_64/65_plus`, `median_age`)
  sourced from ABS 3235.0.
- **Cross-dataset PRESETs** — first three landed:
  `pct_age_pension_recipients`, `pct_jobseeker_recipients`,
  `welfare_density_index`. Exercise the existing
  list-valued `dataset:` front-matter.
- **Devcontainer hygiene** — `.venv` now on a named volume (fixes
  Windows host filesystem collision); /tmp tmpfs (fixes Podman
  perm-drop); native vhs/ttyd/ffmpeg (no Docker socket bind);
  Chromium sandbox + IPC config; assorted post-create reliability
  fixes.
- **CI** — `Render demos` workflow tightened paths + caches ABS
  pre-warm; weekly `Real-data parser check` opens / updates a
  rolling drift issue on failure; GHA pins bumped to Node 24
  compatible majors.
- **Architectural** — `_AbsXlsxDataset` shared base + spec loader
  collapsed four ~330-line dataset modules to ~150 each;
  fetcher-registration consolidated; parsed-result caches in
  pickle / parquet sidecars cut warm-cache run from 5.4 s to
  2.2 s (#43).

### First cross-dataset PRESETs (DSS + ERP)

Three new PRESET feature specs landed in `features/`, sourcing their
numerator from `dss_payments` and denominator from `erp_by_sa2`:

- `pct_age_pension_recipients` — share of an SA2's 65+ residents
  receiving the Age Pension. Pairs naturally with `pct_aged_65_plus`.
- `pct_jobseeker_recipients` — share of working-age residents on
  JobSeeker Payment.
- `welfare_density_index` — composite: sum of nine principal DSS
  payment-type recipient counts / total resident population. A
  recipient-density index, not a unique-headcount measure (people
  on multiple payments are counted once per payment).

These are the first features that exercise the `dataset:` front-matter
accepting a list — the namespace-based dispatch in `enrich.py` fans
out to multiple registered fetchers transparently, no engine work
needed. Yesterday's ERP wishlist columns (`population_65_plus`,
`population_15_64`, `population_total`) are what unblocked this; the
PRESETs were impossible to author cleanly before.

The lock-door in `test_preset_columns_match_gcp_schema.py` was extended
with an `intentionally_non_gcp_presets` set covering the three new
specs — they're cross-dataset by design and don't need GCP catalogue
coverage.

`tests/test_wheel_bundles_specs.py` updated to assert the wheel ships
all nine PRESETs (six GCP + three cross-dataset).

`spec.md` §21.4 + `BACKLOG.md` "Future PRESET features" both updated
to reflect the new state. Additional cross-dataset PRESET candidates
documented in BACKLOG for future authoring sessions.

### ERP wishlist — age bands, gender, median age columns

Extends the `erp_by_sa2` dataset with six new columns sourced from
the companion ABS workbook **3235.0 — Regional Population by Age and
Sex** (DS0002 SA2 cube). Unblocks the cross-dataset PRESETs the
BACKLOG has been waiting on — most notably
`pct_age_pension_recipients = DSS.age_pension_recipients /
ERP.population_65_plus`.

**New `ERP.*` columns** (latest reference year only):

- `population_male` — int, gendered total
- `population_female` — int, gendered total
- `population_0_14` — int, derived from persons × pct/100
- `population_15_64` — int, derived from persons × pct/100
- `population_65_plus` — int, derived from persons × pct/100
- `median_age` — float (years, one decimal)

**Implementation:**

- The 3235.0 DS0002 workbook lives at
  `https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/{year}/32350DS0002_{year}.xlsx`.
  Probed live: HTTP 200, 305 KB, 2,454 SA2 rows.
- `ErpDataSource` now fetches DS0002 alongside the existing DS0003
  totals workbook. The two products publish on slightly different
  cadences (DS0003 path is `2024-25` financial-year-style; DS0002
  path is `2024` calendar-year-style). A second landing-page scrape
  resolves DS0002's URL pattern independently.
- The DS0002 parser (`_parse_ds0002_workbook`) reads Table 1: columns
  10-12 carry Males/Females/Persons counts; column 14 holds median
  age; columns 15-17 hold the broad-age-band percentages (0-14 /
  15-64 / 65+).
- Age-band **counts** are derived: `persons × pct / 100`, rounded to
  int. The published percentages are accurate to one decimal so
  derived counts are within ~0.5% of the underlying single-year
  counts.
- Enrichment is **best-effort**. If DS0002 can't be fetched (older
  releases when 3235.0 wasn't yet published, transient ABS outage,
  upstream layout shift), the fetcher logs a warning and emits the
  core DS0003 columns only. The output schema becomes a strict
  subset; downstream consumers using only `population_total` /
  `population_history_*` continue to work unchanged.
- `_parse_xlsx` refactored from `@staticmethod` to instance method so
  it can drive the DS0002 fetch; both `_parse_ds0003_workbook` and
  `_parse_ds0002_workbook` are top-level pure functions for
  unit-test isolation.
- Real-data check in `tools/verify_real_parsers.py` extended to spot
  the age/sex columns when DS0002 is fetched — reports a sample SA2
  with realistic values (males/females, median age, 65+ count).

**Tests:**

- Two new tests in `tests/test_dataset_erp.py`:
  - `test_load_includes_age_sex_columns_when_available` — verifies
    the merge path and the persons × pct/100 derivation arithmetic.
  - `test_load_silently_omits_age_sex_columns_when_landing_fails`
    — locks in the graceful-degradation contract: a 503 on the
    DS0002 landing page leaves the core columns intact.
- Existing three load tests updated to mock the DS0002 chain (via a
  new `_add_age_sex_mocks` helper) so the parquet-cache test still
  passes a `pd.testing.assert_frame_equal` round-trip.
- `test_spec_matches_fetcher__erp` mocks DS0002 too so the spec/fetcher
  column-set invariant holds.

Live-verification numbers: 2,454 SA2s in DS0002, sample Braidwood
NSW = 2,337 M + 2,147 F = 4,484 persons (matches DS0003), median
age 51.0y, 27% aged 65+. Total Australian residents 27.19M
(13.5M M + 13.7M F) — within rounding of ABS-published headline.

### Temporal Phase F.4 — GCP 2016 release + dataset rename

**Breaking:** the `gcp_2021` dataset id is renamed to `gcp`. Any config
YAML or code that references the dataset by id must be updated. The
variable namespace (`G##.*`) is unchanged — references like
`G02.Median_tot_hhd_inc_weekly` continue to work verbatim.

**What changed:**

- `datasets/gcp_2021.md` → `datasets/gcp.md` (`id: gcp`). The spec's
  `temporal.available_releases` now lists both `"2016"` and `"2021"`;
  `asgs_edition_by_release` maps `"2016" → 2` and `"2021" → 3`.
- The 2016 GCP DataPack uses the **same URL pattern as 2021** — only
  the year prefix changes. Probed live on 2026-05-26:
  `https://www.abs.gov.au/census/find-census-data/datapacks/download/2016_GCP_SA2_for_AUS_short-header.zip`
  returns HTTP 200 with a 35.5 MB ZIP. The existing
  `DataPacksDataSource.filename` property (`f"{year}_GCP_..."`)
  already produces the right filename for 2016 without changes.
- The 2016 ZIP layout matches 2021: CSVs in
  `2016 Census GCP Statistical Area 2 for AUST/` plus
  `Metadata/Metadata_2016_GCP_DataPack.xlsx`.
- **Only `short-header` is hosted for 2016.** The `long-header` and
  `sequential` URL variants return HTTP 404. A new config validator
  (`_edition_2_gcp_descriptor_constraint`) requires
  `census.descriptor='short-header'` when `year=2016` and surfaces
  the constraint at config load. 2021 still supports all three
  descriptors.
- Parser candidate-list extensions in `data_sources/datapacks.py`:
  - `_SA2_CODE_CANDIDATES` now includes `SA2_MAINCODE_2016`,
    `SA2_CODE_2016`, `SA2_CODE16` alongside the 2021 forms.
  - `_TABLE_SHEET_CANDIDATES` now includes
    `"Table number, name, population"` (sentence case from 2016)
    alongside the Title Case 2021 variant. The descriptor sheet
    candidate (`Cell descriptors information`) was already present.
- The `_edition_2_gcp_variables_not_supported_yet` config validator
  is removed; the matching unit test repurposed to assert the new
  short-header constraint instead.
- Column codes (Short/Long/Sequential) confirmed **identical**
  between releases for the columns sampled (G01 totals, G02
  medians) — existing variable refs like
  `G02.Median_tot_hhd_inc_weekly` resolve identically in both 2016
  and 2021. The 2021-only tables (G60–G62) are correctly absent
  from the 2016 metadata; references to them will fail with the
  catalog's existing "unknown column" error.
- Live verification: 110 metadata tables parsed, 2,310 SA2 rows in
  G02, sample median household income $1,083/wk (Daceyville NSW,
  realistic for 2016).
- `tools/fetch_real_data.py --edition 2` now fetches the GCP 2016
  DataPack in addition to the boundary file (MB correspondence is
  still deferred — per-state shapefile concat).
- `tools/verify_real_parsers.py` gains three new F.4 probes against
  the 2016 DataPack: list-tables (~59, G62 absent), parse-metadata
  (sentence-case sheet names resolve), load-G02
  (`SA2_MAINCODE_2016` index column). Self-skipping if the 2016
  cache isn't populated.
- New `tests/test_datapacks.py` cases lock in the candidate-list
  semantics: parametrized check on `_detect_sa2_column` for all six
  SA2 column name variants, plus a `build_metadata_xlsx` invocation
  with sentence-case sheet names to ensure the parser tolerates
  both 2016 and 2021 shapes.
- Spec, BACKLOG, and `docs/temporal-data.md` updated to reflect the
  rename and the multi-release `gcp` dataset.

### devcontainer: isolate `.venv` in a named volume

Fixes a host–container filesystem collision that broke the Windows host's
Python tooling every time the devcontainer ran `uv sync`.

**Root cause.** The workspace is bind-mounted from the Windows host.
`uv sync` inside the container creates a Linux `.venv/` (with `lib64`
symlink, ELF binaries) directly inside that bind-mounted path. Windows
sees the resulting `.venv/` as broken: `lib64` is a dangling POSIX
symlink, and `.exe` lookups fail. A subsequent Windows-side `uv sync`
fails with `Access is denied` trying to remove `lib64`.

**Fix.** Add a `"mounts"` entry in `devcontainer.json` that overlays
a named Docker/Podman volume at `${containerWorkspaceFolder}/.venv`.
The named volume takes precedence over the bind mount for that
subdirectory only; the rest of the workspace still comes from the
bind mount. From inside the container nothing changes. From the
Windows host, `.venv/` simply doesn't appear — it's stored in the
container runtime's own volume storage. `uv sync` (post-create) writes
into the volume; the interpreter path in VS Code settings
(`"python.defaultInterpreterPath"`) continues to point at
`${containerWorkspaceFolder}/.venv/bin/python` unchanged.

**Lifecycle.** The named volume (`abs-census-augmentor-venv`) survives
`devcontainer rebuild` (image rebuild, volume retained). To force a
clean re-provision: `docker volume rm abs-census-augmentor-venv` (or
Podman equivalent) then rebuild.

### Temporal Phase F.3 — SEIFA 2016 release + dataset rename

**Breaking:** the `seifa_2021` dataset id is renamed to `seifa`.
Any config YAML or code that references `seifa_2021` by id must be
updated. The variable namespace (`SEIFA.*`) is unchanged.

**What changed:**

- `datasets/seifa_2021.md` → `datasets/seifa.md` (`id: seifa`).
  The spec's `temporal.available_releases` now includes both
  `"2016"` and `"2021"`; `asgs_edition_by_release` maps
  `"2016" → 2` and `"2021" → 3`.
- `SeifaDataSource` now accepts `release="2016"` in addition to
  `"2021"`. The 2016 workbook is the legacy `.xls` format; the
  fetcher uses **python-calamine** (Rust-based reader, added to
  project dependencies) rather than openpyxl so that no console
  scripts land in the `.venv` (avoids the `chmod` EPERM on
  bind-mounted dev containers).
- Parser refactored into a format-agnostic `_read_grids()` /
  `_parse_grids()` split: `_read_grids(path, release)` selects the
  reader (openpyxl for 2021, CalamineWorkbook for 2016) and returns
  a `dict[sheet_name → row grid]`; `_parse_grids(grids, …)` is
  pure Python — no I/O — so 2016 parsing is covered by unit tests
  that pass raw grids directly.
- The SA2 index column is now **release-aware**:
  `"sa2_code_2016"` for the 2016 release (ASGS Edition 2 codes);
  `"sa2_code_2021"` for the 2021 release (ASGS Edition 3 codes).
  The `_AbsXlsxDataset` base's `_sa2_index_name` is now an
  instance attribute (set in `__init__`) rather than a ClassVar,
  so subclasses can override it per-release in their own
  `__init__`.
- 2016 column positions confirmed against the live ABS file on
  2026-05-22 and are identical to the 2021 layout — same sheet
  names, same fixed-position columns, same header-row preamble
  length, same null sentinels. No schema drift between releases.
- `verify_real_parsers.py` updated to probe both 2016 and 2021
  under the shared `data/seifa/` cache directory.
- `python-calamine` added to `[project.dependencies]` in
  `pyproject.toml`.

### devcontainer: fix `/tmp` permission denial under Podman

The dev container failed to attach under Podman with:

```
mkdir: cannot create directory '/tmp/.X11-unix': Permission denied
```

Root cause: Podman's buildah drops `/tmp`'s sticky + world-write bits
(1777 → 0755, root-owned) when it commits the devcontainer-feature
layers — so the non-root `vscode` user can't write to `/tmp`, and VS
Code's attach-time `/tmp/.X11-unix` setup fails. (Verified: the base
image's `/tmp` is 1777; the built image's is 0755. Docker/BuildKit
preserves 1777, so only Podman users hit this.) A Dockerfile `chmod`
doesn't stick because features layer on *after* the Dockerfile.

Fix: added `--tmpfs /tmp:exec,mode=1777` to `runArgs`, mounting a
fresh correctly-permissioned tmpfs at runtime regardless of what the
image build left behind. Harmless no-op under Docker. Requires a
"Rebuild Container" to take effect (runArgs change).

### tools: Phase F.3 / F.4 inspection probes

Two one-off discovery scripts to capture the schema of SEIFA 2016 and
GCP 2016 *before* their fetchers are written, per CLAUDE.md's "Real
Data First" rule. The pattern: encode the maintainer's schema
questions in code, run once, paste the output, build the real fetcher
off the captured shape.

- **`tools/inspect_seifa_2016.py`** — fetches the live ABS SEIFA 2016
  SA2 `.xls` (URL captured via WebFetch from the legacy catalogue
  page 2033.0.55.001) and dumps every sheet's preamble, candidate
  data header row, and 3 sample data rows. Reads the legacy `.xls`
  via `python-calamine` (`uv pip install python-calamine`); not added
  to project deps because it's a one-off discovery prerequisite.
  calamine is preferred over the unmaintained `xlrd` because it ships
  no console scripts — `xlrd`'s `runxlrd.py` trips `Operation not
  permitted` when uv copies it into a bind-mounted `.venv` under
  Podman. The probe falls back to `xlrd` when calamine is absent and
  the venv is on a native filesystem.
- **`tools/inspect_gcp_2016.py <zip-path>`** — accepts a locally
  downloaded 2016 GCP DataPack ZIP and dumps the internal layout,
  descriptor xlsx structure, table-CSV inventory, and a
  representative table's header + sample rows. The 2016 GCP isn't
  reachable via a static URL the way the 2021 one is (ABS migrated
  it away from the modern `find-census-data/datapacks` interface
  and the historical archive doesn't expose a direct link). The
  script's docstring documents three options for obtaining the ZIP;
  if a static URL turns up later, the script gains a `_download()`
  helper mirroring `inspect_seifa_2016.py`.

`tools/README.md` updated with discovery instructions. Both scripts
are idempotent (cache the artefact under `data/`); reruns skip
re-fetch unless `--refresh` is passed. Once the F.3 / F.4 fetchers
land, the equivalent post-fetch shape checks move into
`verify_real_parsers.py` and these `inspect_*.py` scripts retire.

### CI: speed up `Render demos` PR validation

Two focused changes to the demo-render PR workflow, addressing the
"~5–7 minutes on every temporal-mode PR" cost flagged in BACKLOG
("Slow `Render demos` CI workflow"):

- **Tightened `paths:` filter.** Dropped
  `src/census_augment/pipeline.py` and `src/census_augment/enrich.py`
  from the trigger list. Internal-orchestration edits there rarely
  change what any tape records — all of the Phase F.1 / F.2 / G work
  forced a 5-7 minute render that surfaced no visual delta. PRs that
  *do* affect demos via those files can still trigger render on demand
  via `workflow_dispatch` in the Actions tab. The remaining triggers
  cover the cases where the demos actually shift: tape edits,
  `cli.py`, registered dataset / PRESET specs, the workflow file
  itself.
- **Cached the ABS pre-warm.** New `actions/cache@v4` step persists
  `~/.cache/census-augment/data/` across runs, keyed on the
  `pyproject.toml` hash plus every YAML config under `tools/demo/`.
  The render script's pre-warm becomes a no-op on cache hit, skipping
  the SA2 boundary ZIP (~50 MB), GCP DataPack ZIP (~50 MB), and each
  registered-dataset XLSX. Also reduces the workflow's exposure to
  transient ABS-download flakes on later commits in a PR.

The same cache step is mirrored into `demo-publish.yml` so the manual
post-merge render benefits from whatever cache the PR validation
built. BACKLOG entry updated to reflect what shipped; the
heavier-touch "pull render out of PR CI entirely" remains on the
table if cost regresses.

### Temporal Phase G — G-NAF release-per-row

Builds on Phase F.2 by lifting the *other* implicit single-release
assumption in temporal mode: G-NAF. Until this PR, every row in a
temporal-mode run was geocoded against the pipeline's configured
G-NAF release regardless of date — so a 2020-dated row would hit the
2025 G-NAF snapshot and silently use addresses that didn't exist in
2020 (or miss addresses that have since been retired). Per
spec-temporal.md §12, the geocoder's release should match the row's
date.

**What changed:**

- New ``resolve_gnaf_release(row_date, available_releases, rule, ...)``
  helper in ``_temporal.py`` — same shape as the dataset
  ``resolve_release`` but for the flat YYYYMM list G-NAF publishes.
  Treats each release as an instant at the first of its month
  (matches gnaf-loader's quarterly publication cadence).
- ``GnafDataSource.list_available_releases()`` — public API exposing
  whichever of cache / S3 / official can answer (cache mode prefers
  local; remote always hits S3). Used by the pipeline to know what
  the per-row resolver gets to pick from.
- ``Pipeline.augment`` now branches into a per-release dispatch path
  when (1) ``input.date_column`` is set, (2) the geocoder chain
  contains a G-NAF geocoder, and (3) ``gnaf_available_releases`` was
  wired (either supplied directly or discovered by ``from_config``).
  Rows bucket by their resolved G-NAF release; each bucket geocodes
  through a release-specific :class:`GnafGeocoder` while non-G-NAF
  chain entries (Nominatim, custom providers) pass through unchanged.
- New output column ``gnaf_release`` (temporal mode + G-NAF only) —
  the per-row YYYYMM release the dispatcher chose. Slots in directly
  after ``sa2_code_edition`` per spec-temporal.md §11 column order.
- ``Pipeline.__init__`` gains optional ``extra_gnaf_geocoders``
  ``gnaf_geocoder_factory`` and ``gnaf_available_releases`` kwargs.
  ``from_config`` wires the factory + populates the available list
  automatically from the data source; tests pre-populate the dict
  instead. ``_resolve_coordinates`` and ``_geocode_with_chain`` now
  take an optional ``geocoders`` parameter so the per-bucket chain
  can be threaded through (default behaviour unchanged).

**Backward compatibility:**

- Cross-sectional runs are bit-identical.
- Temporal runs without G-NAF (Nominatim-only chains) are
  bit-identical — Phase G's branch is gated on
  ``_has_gnaf_in_chain()``.
- Temporal runs that *do* have G-NAF but where ``from_config`` can't
  list available releases (e.g. S3 listing fails) log a WARNING and
  fall back to the configured release for every row (matching the
  Phase F.2 behaviour).
- ``RuntimeError`` surfaces only when a row resolves to a release
  that has no entry in ``extra_gnaf_geocoders`` AND no factory was
  wired — the message names the missing release.

**Deferred:**

- Address-retirement awareness (per spec-temporal.md §17): "address X
  existed in 2018 but was retired in 2022; row dated 2020 should hit
  X even though X is missing from the 2025 release." Today an
  unmatched address falls through to fuzzy / Nominatim — same as
  Phase F.2.
- Per-dataset G-NAF resolution rule (the global ``temporal.resolution``
  applies; ``temporal.per_dataset["gnaf"]`` is not yet recognised).
  Trivial to add when the need surfaces.

11 new tests (7 unit, 4 integration); no regressions; 649 pass.

### Temporal Phase F.2 — Cross-edition orchestrator (lifts the single-edition gate)

Builds on F.1's per-edition boundary scaffolding to actually *run*
temporal-mode buckets that span ASGS editions. Phase E.2 had hard-coded
a ``NotImplementedError`` for any release on an edition other than the
configured ``reference_edition``; that gate is now lifted, replaced
with per-bucket fan-out across source editions.

**Mechanics (spec-temporal.md §2, §9.3):** the orchestrator now
computes the set of editions referenced by a bucket's resolved
releases. For each non-reference edition E, it looks up the
source-edition SA2 code for every bucket row against that edition's
SpatialIndex. The bucket's variables are then split into per-edition
groups and run through a sub-enricher each — keyed against the right
edition's SA2 code. GCP and PRESET variables ride along with the
reference-edition group (GCP 2016 isn't registered yet; every PRESET's
source columns are GCP).

**New output columns (temporal mode only):**

- ``sa2_code_edition`` — the reference ASGS edition number; constant
  per run. Tells downstream consumers which edition the canonical
  ``sa2_code`` is reported in.
- ``<dataset_id>_sa2_code_source`` — per-dataset source-edition SA2
  code. Only emitted when at least one dataset's source edition
  differs from the reference edition (matches the spec-temporal.md
  §11 Q5 ruling: surface the per-dataset source SA2 so downstream
  consumers can decide whether to aggregate by canonical or by
  source).

Both slot into the output column order per spec-temporal.md §11:
``sa2_code, sa2_name, sa2_resolution, sa2_code_edition,
<dataset>_release..., <dataset>_sa2_code_source..., enrichment...``.

**Pipeline API additions** (internal — public surface unchanged):

- ``Pipeline.__init__`` gains optional ``extra_spatial_indices:
  dict[int, SpatialIndex]`` and ``spatial_index_factory:
  Callable[[int], SpatialIndex]`` kwargs. ``from_config`` wires the
  factory with a closure that constructs the right
  ``BoundariesDataSource`` per ASGS edition; tests pre-populate
  ``extra_spatial_indices`` instead.
- A clear ``RuntimeError`` surfaces when the orchestrator needs an
  edition it can't construct — most likely a test-built pipeline that
  forgot to wire the factory or supply the index for that edition.

**What still doesn't work after this PR:** Phase F.2 unblocks
*cross-edition* lookups but doesn't *populate* any Edition-2 datasets.
Until SEIFA 2016 / GCP 2016 / ERP-back-to-2016 land (Phase F.3 / F.4
in subsequent PRs), the cross-edition path is exercised in tests but
won't yield real Edition-2 values for users. Cross-edition PRESETs
(PRESETs whose source columns span editions) are still single-edition
in this PR — when Edition-2 GCP lands, we'll need per-source-column
edition resolution; not in scope here since no such PRESET exists yet.

2 new cross-edition tests; 2 existing tests updated to reflect the
lifted gate; 637 pass; ruff + mypy clean.

### Temporal Phase F.1 — ASGS Edition 2 boundary support (scaffolding)

Scaffolding to land historical (pre-2021) datasets without breaking the
Edition-3 (current) path. Per spec-temporal.md §2, point-correct
enrichment for a 2016-era release requires looking up SA2 codes against
the 2016 boundary file — not the 2021 one. This PR wires that fetch
path; the actual historical datasets (SEIFA 2016, GCP 2016, ...) land
in follow-up PRs.

**New: `BoundaryEditionSpec` + Edition 2/3 factories.** Per-edition
URL, filename, datum, and DBF column-name choices now live in
`src/census_augment/data_sources/_edition.py`. `BoundariesDataSource`
delegates to the spec instead of constructing filenames inline. The
default behaviour for Edition 3 is unchanged (still
`SA2_2021_AUST_SHP_GDA2020.zip` under the existing
`boundaries_base_url`); Edition 2 uses the ABS Lotus Notes "openagent"
URL form captured via WebFetch against the 2016 ASGS landing page
(`1270055001_sa2_2016_aust_shape.zip`, GDA94 only).

**`CensusConfig` now accepts year=2016.** `year`, `asgs_edition`, and
`datum` Literal types expanded. Cross-field validators enforce the
two valid combinations: `(2021, 3, GDA2020|GDA94)` and
`(2016, 2, GDA94)`. Misconfigurations fail loudly at config-load.

**Pipeline picks up per-edition column names automatically.** The
`SpatialIndex` constructor already accepted `code_column`/`name_column`
kwargs; `Pipeline.from_config` now passes them from the boundary
source's edition spec, so Edition 2's `SA2_MAIN16` / `SA2_NAME16`
columns flow through the spatial join without further changes.

**Deferred and called out at config-load:** Two combinations
intentionally raise rather than half-work:

- `year=2016 + 'gnaf' in geocoding.providers` — ABS publishes 2016
  Mesh Block shapefiles per state, not nationally, so the §7.3
  fast-path concat across 8 state files is follow-up work.
- `year=2016 + any GCP-shape variable` — the 2016 GCP DataPack
  lives at a different URL with a different filename pattern;
  registering it is Phase F.4 work.

Both error messages name the missing piece so users hit a hard wall
with a clear pointer rather than a 404 deep in the network layer.

**`tools/`**: `fetch_real_data.py` gains `--edition 2` for a
boundary-only fetch; `verify_real_parsers.py` adds a self-skipping
probe that opens the live Edition 2 boundary and confirms the
`SA2_MAIN16` / `SA2_NAME16` / GDA94 / EPSG:4283 contract holds. Real
Data First (CLAUDE.md): the Edition 2 URL and column names are
captured from live ABS pages; the verifier is the ongoing drift
detector if ABS changes the openagent URL or renames the DBF columns.

**What still doesn't work after this PR**: a Phase F.1 user can fetch
the 2016 boundary file via the CLI and write code against the
`BoundariesDataSource` API directly, but the pipeline can't yet *do*
anything with year=2016 because no Edition 2 dataset (SEIFA 2016, GCP
2016, etc.) is registered. The cross-edition temporal orchestrator
(currently raises `NotImplementedError` for mixed-edition runs) is
unchanged here — it lifts in Phase F.2. Historical dataset
registrations come in Phase F.3 / F.4. G-NAF release-per-row comes in
Phase G.

29 new tests; no regressions.

### Tier B follow-ups to #65 / #66

Two small post-merge follow-ups to the spec-drift lock-down work.

**PRESET ↔ GCP-schema lock-door test.** Issue #65's #66 fix closed the
spec-vs-fetcher loop for non-GCP datasets. This adds the analogous
loop for PRESETs against the checked-in GCP schema reference dumps
under `tests/fixtures/gcp-schemas/`. Every PRESET source-field GCP
ref now gets a parametrized test asserting the referenced column
exists in the matching `G*.txt` dump. Plus a coverage guardrail
(`test_every_registered_preset_has_at_least_one_resolvable_ref`)
that ensures future cross-dataset PRESETs declare themselves
explicitly when they bypass GCP. 22 new tests under
`tests/test_preset_columns_match_gcp_schema.py`. No drift found in
the current 6 PRESETs.

**SEIFA — document the 16 bonus columns.** PR #66's lock-door test
flagged that `SeifaDataSource.load()` emits 16 columns the spec didn't
document (per-index `sa1_min`, `sa1_max`, `pct_urp_no_score`, and
`sa2_name` × 4 indexes). Spec table now carries all of them with
descriptions. The `_sa1_min` / `_sa1_max` / `_pct_urp_no_score`
columns are useful within-SA2-variation signals; the per-index
`_sa2_name` variants are duplicates of the canonical key, retained
for join-debugging convenience. SEIFA's lock-door warning count
drops from 16 → 0.

### Temporal Phase H — Examples + docs polish

- New `examples/temporal_augmentation.py` — runnable 4-row script
  showing per-row release selection across a multi-year span.
- New `examples/temporal_quarterly_dss.py` — comparison of
  `closest_at_or_before` vs `closest` resolution rules for the
  quarterly DSS dataset.
- `Pipeline.create(...)` now accepts `date_column=` for notebook
  users (avoids having to construct a full `Config` to enable
  temporal mode).
- `docs/usage-library.md` mentions temporal mode + links to the
  new examples.
- `docs/configuration.md` adds a "Temporal mode" subsection with
  the YAML config schema.
- `BACKLOG.md` documents deferred Phase F (historical datasets) and
  Phase G (G-NAF release-per-row) with effort estimates and starting
  points so future implementers can pick them up cold.

The temporal-spec work is now complete for the headline use case
(single-edition temporal mode on ASGS Edition 3 data). Historical
datasets and G-NAF temporal selection remain explicitly deferred.

### Temporal Phase E.2 — Pipeline orchestrator (temporal mode is live)

End-to-end temporal mode lands. Setting `input.date_column` in a
config now does what `spec-temporal.md` §9 describes: each row picks
the dataset snapshot closest to its timestamp, rows are bucketed by
the per-dataset release tuple, and the output gains
`<dataset>_release` columns naming the release used per row.

- `Pipeline.augment(df)` branches into `_enrich_temporal()` when
  `input.date_column` is set. Cross-sectional mode stays bit-identical
  to v1.4.x.
- Per-bucket sub-enrichers are built with
  `dataset_release_overrides`, threaded through to each fetcher's
  factory (which gained an optional `release` kwarg).
- `AugmentResult.releases_used` is populated in temporal mode
  (`dict[str, list[str]]`), `None` in cross-sectional mode.
- `_reorder_output_columns` knows to slot `<dataset>_release` columns
  between the SA2 trio and the enrichment values.
- 8 new integration tests in `tests/test_pipeline_temporal.py` cover:
  single-bucket happy path, multi-bucket fan-out, cross-edition raise
  with helpful error pointing at Phase F, `closest` resolution rule,
  `out_of_range` fail / nearest, missing date column, and the
  cross-sectional-mode-unaffected case.

**Phase E.2 scope: single-edition only.** If any row resolves to a
release on an ASGS edition different from the configured
`temporal.reference_edition` (default 3), the orchestrator raises a
clear `NotImplementedError` pointing at Phase F. Cross-edition
boundary lookups land in Phase F when historical datasets register.

579 tests pass (was 571 in E.1); mypy + lint + format clean.

### Temporal Phase E.1 — Config schema + release resolver

Schema-layer additions for temporal mode. Pipeline orchestrator
(Phase E.2) follows separately.

- `InputConfig.date_column` — optional column name for temporal mode.
- New `TemporalConfig` block (Pydantic), with `resolution`,
  `out_of_range`, `reference_edition`, `per_dataset` overrides.
- New `src/census_augment/_temporal.py`:
  - `release_window(release_id, cover_basis)` — coverage-window math
    for the four `cover_basis` values (`census_reference_date`,
    `financial_year_ending`, `calendar_year_ending`,
    `quarter_ending`).
  - `resolve_release(row_date, metadata, rule, out_of_range)` —
    the per-row resolver. `closest_at_or_before` (default) and
    `closest` rules; `fail` (default) and `nearest` out-of-range.
  - `OutOfRangeDateError` carries dataset id, row date, earliest
    release, row index for actionable error messages.
  - `to_date(value)` — coerce date / datetime / pandas.Timestamp /
    ISO string to `datetime.date`.
- 22 new tests in `tests/test_temporal_helpers.py`.

No pipeline behaviour change yet. Phase E.2 wires bucketing through.

### Temporal Phase D — Cache restructure to per-ASGS-edition subdirs (BREAKING)

The boundary, census DataPack, and Mesh Block caches now live in
edition-keyed subdirectories so multiple ASGS editions can coexist
on disk. Layout change:

```
Before:
  <data_dir>/boundaries/SA2_2021_AUST_SHP_GDA2020.zip (+ extracted)
  <data_dir>/census/2021_GCP_SA2_for_AUS_short-header.zip (+ extracted)
  <data_dir>/mb/MB_2021_AUST_SHP_GDA2020.zip (+ extracted)

After:
  <data_dir>/boundaries/2021/SA2_2021_AUST_SHP_GDA2020.zip
  <data_dir>/census/2021/2021_GCP_SA2_for_AUS_short-header.zip
  <data_dir>/mb/2021/MB_2021_AUST_SHP_GDA2020.zip
```

**Breaking**, no auto-migration: existing caches in the old flat layout
remain on disk but are not read. Wipe `<data_dir>/boundaries/`,
`<data_dir>/census/`, and `<data_dir>/mb/` and run
`census-augment fetch --refresh --boundaries --census` to repopulate
the new layout. The dataset-specific caches (`seifa_2021/`,
`erp_by_sa2/`, `dss_payments/`, `abs_personal_income/`) and the G-NAF
cache (`gnaf/{YYYYMM}/`) are unaffected.

Why now: Phase E (input.date_column + bucketing) needs to load multiple
boundary editions in a single run. The per-edition layout sets that up
without further restructure later. Today's cross-sectional runs see the
same on-disk behaviour as before (just one level deeper). Documented
in `docs/cache.md`.

### Temporal Phase C — Rename `ato_personal_income` → `abs_personal_income` (BREAKING)

The dataset we called `ato_personal_income` (with namespace `ATO`)
since v1.3 was always a misnomer. What it actually fetches is **ABS
catalogue 6524.0.55.002 "Personal Income in Australia"**, a
LEED-derived ABS product that uses ATO administrative data as one
input. It is **not** ATO Taxation Statistics. Surfaced during
temporal-spec research; fixed here while the surface area is small.

**Breaking changes:**

- Dataset id: `ato_personal_income` → `abs_personal_income`.
- Variable namespace: `ATO` → `ABS_PIA`. Users with `variables: {foo:
  ATO.bar}` must update to `ABS_PIA.bar`.
- Cache directory: `<data_dir>/ato_personal_income/` →
  `<data_dir>/abs_personal_income/`. Filenames in the cache also
  change (`ato-personal-income-{release}.xlsx` →
  `abs-personal-income-{release}.xlsx`).
- Module path: `census_augment.datasets._ato` →
  `census_augment.datasets._abs_pia`.
- Class name: `AtoDataSource` → `AbsPiaDataSource`.

**To migrate:** in your config(s), replace `ATO.<field>` references
with `ABS_PIA.<field>` and run `census-augment fetch --refresh` (or
just delete `<data_dir>/ato_personal_income/`; the new cache lands
on next run).

The dataset's *content* is unchanged — same SA2 codes, same
columns, same upstream URL pattern. This rename is purely about
calling the thing what it actually is.

### Temporal Phase B — Per-dataset `temporal:` metadata blocks

Adds the schema layer the upcoming temporal-mode pipeline (see
[`spec-temporal.md`](spec-temporal.md) and [`docs/temporal-data.md`](docs/temporal-data.md))
will read at run time.

- New Pydantic model `TemporalDatasetMetadata` in
  `src/census_augment/datasets/_spec.py`. Each registered dataset's
  spec markdown may now include a `temporal:` block in its YAML
  front-matter declaring:
  - `cadence` — `per_census` / `annual` / `quarterly` / `continuous`.
  - `cover_basis` — how to compute a release's coverage window
    (`census_reference_date`, `financial_year_ending`,
    `calendar_year_ending`, `quarter_ending`).
  - `release_id_format` — informational; documents the release-id
    format the available list uses.
  - `available_releases` — list of known release ids.
  - `asgs_edition_by_release` — the per-release ASGS edition. The §2
    invariant from `spec-temporal.md` relies on this; the upcoming
    temporal pipeline uses this map to decide which boundary file
    to use for each row's per-dataset spatial lookup.

- Added the block to all four currently-registered datasets:
  - `gcp_2021` (Edition 3 only — single release)
  - `seifa_2021` (Edition 3 only — single release)
  - `erp_by_sa2` (transition: 2021 release on Edition 2, 2022+ on Edition 3)
  - `dss_payments` (transition: through 2023-Q1 on Edition 2, 2023-Q2+ on Edition 3)
  - `ato_personal_income` (transition: through 2018-19 on Edition 2, 2019-20+ on Edition 3)

- 7 new tests in `test_datasets_registry.py`: temporal block parses,
  rejects unknown cadence, rejects unknown ASGS edition, the four
  on-disk specs have well-formed temporal blocks.

No behavioural change yet — this is the schema. Phase E wires the
pipeline through.

### Phase 3 — Operational hardening

Three small additions surfaced by the v1.4.2 all-up review.

**Row-level partial-enrichment log.** `Pipeline.augment(df)` now
emits one `INFO`-level log line per enrichment column when any rows
came back partially enriched, naming the column and the
nulls-vs-total ratio. Previously the run summary only had an
aggregate "partially_enriched: N" — a user staring at a 5-of-100
partial-enrichment row couldn't tell which configured variable
nulled out without inspecting the output CSV. Now the log line
("SA2s outside SEIFA coverage", typically) names the culprit.

**`docs/cache.md` ops reference.** New documentation page covering
what's cached where, how big each subdir gets, what triggers
invalidation, and how to clear selectively vs nuke everything.
Linked from `docs/index.md`. Picks up the G-NAF "this is the 10 GB
item" callout from the existing G-NAF docs but consolidates the
data-subdir-by-data-subdir breakdown in one place.

**Scheduled real-data CI workflow.** New
`.github/workflows/real-data-check.yml` runs `fetch_real_data.py
--skip-gnaf` + `verify_real_parsers.py` weekly (Monday 04:00 UTC) +
on `workflow_dispatch`. Opens a `real-data-drift` GitHub issue on
failure (or comments on an existing open one). Catches ABS / data.gov.au
schema drift the week it lands rather than waiting for a maintainer
to bump into it locally. G-NAF is skipped (10 GB; gnaf-loader has
its own upstream monitoring) — `make verify-real` covers G-NAF for
maintainers who want it.

### Phase 2 PR-2 — Architectural simplification: fetcher-registration consolidation

Before this change there were **two** fetcher-registration mechanisms
side by side:

- `enrich._FETCHER_FACTORIES` — a module-level dict in
  `census_augment/enrich.py` mapping the four built-in dataset ids
  to local `_build_*` factory functions. Used by
  `CensusEnricher._make_fetcher`.
- `Registry.register_fetcher` / `Registry.make_fetcher` — public
  methods on the registry that already existed and were documented
  as the contract, but were **never called from production**.

The registry's docstring also claimed "Fetcher classes register
themselves separately via `Registry.register_fetcher`" — a polite
lie.

This change collapses the two:

- Each built-in dataset module (`_seifa.py`, `_erp.py`, `_dss.py`,
  `_ato.py`) ends with a `_register()` call that binds its
  `_build_fetcher(root)` factory to its dataset id on the
  process-wide registry.
- `datasets/__init__.py` imports each module after building the
  registry so the side-effecting registrations run.
- `CensusEnricher._make_fetcher` now calls
  `registry.make_fetcher(dataset_id, root=...)` — one line, no
  knowledge of which datasets exist.
- `_FETCHER_FACTORIES` and the four `_build_*` shims in
  `enrich.py` are gone.
- Three test stubs in `tests/test_enrich_dispatch.py` were updated
  to patch `registry._fetcher_factories` instead of the deleted
  dict.
- The lying docstring in `datasets/_registry.py` is now accurate.

Same number of tests pass (**543**); zero behavioural change.

### Phase 2 PR-1 — Architectural simplification: shared dataset base + spec loader

Two structural duplications from the v1.3 / v1.4 evolution have been
collapsed into shared helpers.

**`_AbsXlsxDataset` shared base.** The four registered datasets
(`_seifa.py`, `_erp.py`, `_dss.py`, `_ato.py`) all implemented the
same skeleton inline: `__init__` boilerplate, `resolved_release`
lazy property, `is_cached`, `_xlsx_path` / `_parquet_path`,
streaming `fetch()` with retry, and `load()` with parquet sidecar
caching. Pulled the shared plumbing into
`src/census_augment/datasets/_xlsx_base.py`. Subclasses now declare
only the dataset-specific bits:

- `_label`, `_cache_glob` (class attrs).
- `_filename_stem(release)` — basename without extension.
- `_resolve_release()` — landing-page scrape (ERP / ATO), CKAN
  lookup (DSS), or eager no-op (SEIFA — static URL).
- `_parse_xlsx(xlsx_path)` — dataset-specific parser.
- `_post_parse(df)` — optional hook for DSS's `release_quarter` /
  ATO's `reference_financial_year` columns.

Net effect: ~120 lines of duplicate plumbing collapsed per dataset
into the base, and the existing `DatasetFetcher` Protocol contract
is preserved — subclasses opt into the base by inheritance, but
nothing forces it (a future parquet-native source can implement the
protocol directly).

**Shared `iter_specs_from_dir` for registry loading.** Both
`Registry.from_repo_specs` (datasets) and
`FeatureRegistry.from_repo_specs` (PRESETs) had near-identical loops
walking `*.md` files, skipping leading-underscore filenames, and
logging+swallowing `ValueError` from the parser. Pulled into
`src/census_augment/_spec_loader.py::iter_specs_from_dir` — a
generic `(directory, parser, label) -> Iterator[T]` helper. Both
registries now call it with their respective parser callbacks.
Behaviour is bit-identical (verified by the existing test suite).

Full suite still passes (**543 passed, 1 skipped**); mypy clean
across 64 source files; lint + format clean. Test files unchanged —
the public API is identical, the refactor is structural only.

### Phase 1 polish — correctness, error UX, hygiene

A bundle of small wins surfaced by the v1.4.2 all-up review.

**Retry on transient ABS / data.gov.au failures (spec §10).** Spec
§10 documented "Retry with exponential backoff (3 attempts), then
abort", but only `geocoding/nominatim.py` actually did it. The five
bulk fetchers (`_AbsZipDataSource._download` for boundaries +
DataPacks; `_seifa.py`, `_erp.py`, `_dss.py`, `_ato.py`) all issued
a single `session.get()` and aborted the run on the first
transient 5xx. Added `src/census_augment/_http_retry.py` — a
shared streaming-GET helper that retries on `ConnectionError`,
`Timeout`, and HTTP 502 / 503 / 504 with 1s / 2s / 4s backoff. Wired
into all five call sites; 9 new tests cover the retry semantics
(label propagation, 404 not retried, 500 not retried, etc.).

**Input-column collision check (spec §8).** Spec §8 says "Original
input columns are preserved unchanged"; the pipeline silently
overwrote them if a user's CSV already had a column named
`sa2_code` / `geo_lat` / etc. `Pipeline.augment(df)` now raises a
`ValueError` listing every colliding name before any work happens.
8 new parametrised tests across the seven reserved column names.

**CLI run command swallows tracebacks.** `census-augment run` now
catches `CatalogError`, pydantic `ValidationError`, `HTTPError`,
`ConnectionError`, `ValueError`, `RuntimeError` and surfaces a
one-line `Error: ...` + exit 1, instead of dumping a raw Python
traceback. Bare tracebacks still available via `-v` / `--verbose`.

**`pd.read_csv` encoding fallback.** `Pipeline.run()` now tries
`utf-8-sig` → `utf-8` → `cp1252` when reading the user's input
CSV, so Excel-exported Windows-1252 inputs don't fail with an
uninformative `UnicodeDecodeError`. If all three fail, the error
message names the path and the encodings tried.

**`mb_correspondence.py` relocated** from the package root into
`data_sources/`. Logically it's been an ABS-zip data source since
v1.0; this just makes the file tree match. All importers (CLI,
pipeline, tests, three `tools/` scripts) updated; `spec.md` §5
project-structure tree updated to match.

**Trivial cleanups:**

- Dropped redundant `except (OSError, Exception)` tuple syntax in
  `data_sources/boundaries.py` — `Exception` already covers
  `OSError`. `# noqa: BLE001` retained intentionally.
- Removed three stale "Phase 4 / Phase 6b" comments referencing
  long-shipped milestones (`geocoding/gnaf.py` GnafGeocoder
  docstring, `pipeline.py` `Pipeline.create` comment,
  `data_sources/gnaf.py` `_REQUIRED_COLUMNS` comment).
- Updated `tools/README.md` "what `verify_real_parsers.py` checks"
  list to reflect the v1.1 G-NAF coverage, v1.3 dataset coverage,
  and v1.4 PRESET-source coverage that landed but weren't
  documented there.

### Removed — Host Docker socket bind from the devcontainer

`.devcontainer/devcontainer.json` no longer enables the
`ghcr.io/devcontainers/features/docker-outside-of-docker` feature.
The devcontainer is now host-runtime agnostic — it works under
Docker Desktop, Podman Desktop, Colima, etc., with no project-side
changes.

**Why.** The socket was only ever load-bearing for one path:
`./tools/demo/render.sh --docker` invoked from inside the
devcontainer. Nothing in `src/`, the 515+ hermetic tests, or
`.github/workflows/test.yml` talks to Docker at all. The render
script's default inside the devcontainer is `--local` (native VHS
installed by `post-create.sh`), so the socket sat unused under the
common workflow. Meanwhile, hard-coding `/var/run/docker.sock`
broke under Podman Desktop — the host socket lives elsewhere and
the bind created an empty socket file that produced confusing
`docker: connection refused` errors.

**Trade-off.** From inside the devcontainer,
`./tools/demo/render.sh --docker` now fails with "Docker isn't
reachable". This is intentional — that mode is a maintainer-only
diagnostic for testing `tools/demo/Dockerfile`, and a maintainer
doing that can run it from the host shell where Docker / Podman /
Colima already live.

**Verification.** Inside the rebuilt devcontainer, `command -v docker`
returns nothing and `./tools/demo/render.sh` (default `--local`)
still produces working GIFs. The `.devcontainer/README.md` has a
new "Why no Docker socket?" section spelling this out; spec.md §14
records the decision as #33.

This builds on the earlier `[Unreleased]` "Podman Desktop noted as
a Docker Desktop alternative" entry — that one added the host-side
guidance; this one drops the now-unnecessary feature.

### Docs — Podman Desktop noted as a Docker Desktop alternative

`.devcontainer/README.md` now lists Podman Desktop as a supported
host container runtime alongside Docker Desktop. Same
`devcontainer.json`, no project-side changes — point VSCode's Dev
Containers extension at the Podman socket. New "Podman Desktop"
section covers the `dev.containers.dockerPath` setting, the rootless
seccomp/userns posture, and the one host sysctl
(`kernel.unprivileged_userns_clone`) to check if chromium sandbox
complains under rootless mode.

### Changed — Docs restructure: README as sales pitch, handbook in `docs/` (closes #46)

`README.md` is now ~120 lines — the elevator pitch, the three demo
GIFs (with their auto-generated scene strips), a hello-world snippet,
and a "where to go next" pointer. The reference material that had
accumulated in the README — full library API walkthrough, full CLI
reference, the ~100-line G-NAF setup section, the development
workflow, cache locations — has moved into a new `docs/` handbook:

- `docs/index.md` — entry point / TOC.
- `docs/usage-library.md` — `Pipeline.augment(df)`, `AugmentResult`, examples.
- `docs/usage-cli.md` — full `census-augment` command reference.
- `docs/configuration.md` — `config.yaml` schema, cache locations, variable namespaces.
- `docs/gnaf-setup.md` — cache vs remote mode, prefetch, bring-your-own parquet, attribution.
- `docs/development.md` — Make targets, dev container, contributing rules.

The handbook lives next to the existing demo assets (`docs/*.gif` and
`docs/frames/*.png`), so all user-facing documentation is in one
tree. `spec.md` §5 (Project Structure) updated to reflect the new
`docs/` layout. Future feature additions (cross-dataset PRESETs,
new datasets, etc.) land in `docs/` first rather than growing the
README.

### Performance — Parsed-result caches collapse warm-cache run from 5.4s to 2.2s (closes #43)

Two sidecar caches now sit next to the heaviest parsed artefacts and
short-circuit subsequent loads:

- **`<metadata-xlsx>.<descriptor>.parsed.pkl`** next to the DataPack
  metadata Excel. The descriptor sheet's 119-table walk via openpyxl
  takes ~1.8 s on a fast NVMe and proportionally more under
  bind-mounted filesystems. The parsed result is a small (~6 kB)
  dict-of-dataclasses that pickles in ~50 ms.
- **`<boundary>.feather`** next to the ASGS SA2 `.shp`. Reading the
  ~50 MB shapefile via geopandas/pyogrio takes ~1.3 s on Windows
  native; reading the GeoDataFrame back from feather is ~6x faster.

Both caches are keyed on the source file's mtime — `fetch(refresh=True)`
re-extracts the underlying ZIPs, bumps the source mtimes, and the
caches invalidate automatically. Corrupt or schema-mismatched caches
are silently ignored; the parser falls back to the canonical source
and overwrites the cache.

**Measured warm-cache `census-augment run` on Windows (`tools/demo/config.yaml`):**

| Phase | Before | After (warm cache) |
|---|---|---|
| import | 2.05 s | 1.74 s |
| boundaries.load | 1.28 s | 0.20 s (-84%) |
| datapacks.metadata | 1.82 s | 0.10 s (-94%) |
| augment | 0.22 s | 0.10 s |
| **TOTAL** | **5.40 s** | **2.15 s (-60%)** |

In the dev container's bind-mounted workspace (where the original
35 s symptom was measured) the same proportional drops apply to the
two file-reading phases, so the run time should fall by ~10-15 s.
Demo tape Sleeps can drop accordingly on the next render. The
remaining floor (`import` + irreducible parquet/feather I/O) is
~2 s — dominated by Python's pandas/geopandas/shapely import cost.

`tools/profile_run.py` is a small per-phase profiler that produced
these numbers; check it in for future perf regressions.

### Added — CI demo rendering (closes #38)

Two new GitHub Actions workflows under `.github/workflows/`:

- **`demo-render.yml`** — triggered on PRs that touch the tapes, the
  CLI / pipeline / enricher, the registered dataset / PRESET specs,
  or either workflow file. Renders every tape via the same
  `tools/demo/render.sh --local --all` path the devcontainer uses,
  refreshes the README scene strips, and uploads the resulting
  `docs/*.gif` + `docs/frames/*.png` as a `demo-renders` artifact
  (14-day retention). Reviewers download it to see the visual delta a
  PR would land. Nothing gets committed by this workflow.
- **`demo-publish.yml`** — manual `workflow_dispatch` only; renders
  on the latest `main` and pushes the refreshed demo assets + any
  README scene-strip change back to `main` as a
  `github-actions[bot]` commit. Use this to land a refresh of the
  committed demo assets without authoring a PR — e.g. after an ABS
  data update or a tape edit whose render-validation PR has
  already merged.

Both workflows share a composite action,
`.github/actions/install-render-deps/`, that installs `ffmpeg`,
chromium's runtime shared-library set (`libnss3`, `libatk1.0-0t64`,
`libgbm1`, etc. — `chromium` itself doesn't have an apt package on
Ubuntu 24.04 noble, where `ubuntu-latest` resolves; vhs's go-rod
downloads its own headless-chromium binary to `~/.cache/rod/`),
`ttyd`, and `vhs`. Pinned versions for `ttyd` and `vhs` match
`.devcontainer/post-create.sh` so the devcontainer (Debian bookworm,
where `apt install chromium` works) and CI render with the same
toolchain — bumping one means bumping the other.

**Settled design choices** (from issue #38's "Open questions"):

- *Trigger filter for PR runs.* Path-filtered to the asset / source
  files that actually affect the demos. Saves CI minutes on docs-only
  and unrelated-source PRs while still catching every CLI-rename-
  broke-the-demo case.
- *ABS cache strategy.* No `actions/cache` for the ABS data. Each
  run re-fetches; an ABS upstream change becomes a CI signal rather
  than going silently stale behind a cached layer. Whole render is
  ~5-7 min — well within reason.
- *`demo-publish.yml` push mechanics.* Default `GITHUB_TOKEN` with
  `contents: write` scoped only to the publish workflow. No PAT, no
  GitHub App. Bot author keeps the audit trail clean. Workflow
  guards on `github.ref == 'refs/heads/main'` so it can't render an
  unmerged branch onto `main`.
- *Schedule.* None. Manual `workflow_dispatch` is the only trigger
  for publish — refreshes happen when a maintainer explicitly asks
  for them.

### Tier 3 tidy-up

Three independent chores bundled into one PR:

- **`ruff format` applied repo-wide.** 44 files reformatted to match
  ruff's current default style. The lint suite passed before and after
  (no rule changes), and the full test suite (517 / 1 skipped) still
  passes. CI gains `uv run ruff format --check .` as a step so
  format drift never lands again.

- **`mypy tests/` clean and wired into CI.** The `tests/` tree was
  previously ungated: 62 errors lurking. Fixed by stripping 23
  no-longer-relevant `# type: ignore` comments (mypy got better at
  inference since they were written) and adding a
  `[[tool.mypy.overrides]]` block scoped to `tests.*` that disables a
  handful of test-only noisy error codes (`no-untyped-call` from
  `pyarrow.parquet`, `union-attr` / `arg-type` from Pydantic Optional
  fields in test fixtures, `dict-item` from heterogeneous-value
  fixture dicts, etc.). Source tree stays under `strict = true`.
  CI now runs `mypy src/ tools/ tests/` (was `src/ tools/`).
  `Makefile`'s `make typecheck` target updated to match.

- **`_template.md` wheel exclusion deferred to BACKLOG.** Hatchling's
  `force-include` (used to bundle dataset / feature specs into the
  wheel) bypasses both per-target `exclude` and the build-global
  `[tool.hatch.build] exclude`, so the obvious one-liner doesn't take
  effect. Two viable approaches (move templates to a separate
  directory, or write a custom build hook) are both bigger than a
  typical tidy-up. The runtime loaders already skip `_template.md`
  regardless of where it lives, so the cost of NOT doing this is
  ~4 KB of wheel space. Captured in `BACKLOG.md`.

No behavioural changes anywhere — pure tooling.

### Added — Demo GIFs + scene strips embedded in README (closes #40)

The README now embeds all three demo GIFs at sensible places —
headline `docs/demo.gif` at the top, `docs/discover-datasets.gif`
and `docs/preset-features.gif` under a new "See it in action"
section — each followed by a collapsible `<details>` block holding
a 4-column thumbnail strip of the scene-by-scene PNGs. Each
thumbnail is a click-through to the full-resolution image
(GitHub's built-in image viewer handles the lightbox; no JS, no
CSS).

### Added — `tools/demo/refresh_readme_frames.py`

A small script (~150 lines, no new deps) that scans
`docs/frames/<slug>-<n>-<label>.png`, generates a markdown table
of clickable thumbnails per tape slug, and writes it between
matching `<!-- BEGIN demo-frames: <slug> -->` /
`<!-- END demo-frames: <slug> -->` markers in `README.md`. Run via
the new Makefile targets:

- `make demo` / `make demos` now invoke the refresh script after
  rendering, so the README scene strips always reflect what the
  tapes produced. Add a scene to a tape, re-render, the strip
  grows by a column automatically.
- `make check-readme-frames` is a CI-friendly lint mode that exits
  non-zero if the strips are stale relative to the committed PNGs.
  Not currently wired into CI but available for manual / future
  use.

Loud failure modes (per #40's "definition of done"):

- Missing marker pair → `RuntimeError` listing what to add.
- Mismatched BEGIN/END counts → `RuntimeError` flagging the
  imbalance.
- Tape exists but no rendered PNGs yet → silent skip (normal
  state before first `make demos`).

Pattern A from the design discussion (GIF + collapsible scene
strip) chosen over alternatives because it stays uncluttered for
the 80% who just want the GIF, while letting interested readers
expand the static breakdown. GIF-shy contexts (some Slack /
Discord embeds, README mirrors that don't loop GIFs) get the
direct PNG links inside the strip.

### Fixed — demo screenshots captured empty output panels

The first round of rendered demos showed `census-augment` commands typed
but with no output below them in the captured PNG frames. Repro: 7 of
the 12 frame snapshots had only the typed command line and nothing else
— including the headline demo's "Run output" and "Output table" scenes
and three of the four discover-datasets scenes. The only frames that
worked were ones that didn't invoke `census-augment` (e.g. `cat`,
`head -25`).

Two root causes stacked:

1. **Python stdout buffering** — under vhs's chromium-fronted pseudo-TTY,
   Python's TTY detection falls back to fully-buffered stdout. Output
   only flushes at process exit. The Screenshot directive fires before
   the process exits, capturing a "command typed, nothing yet" frame.
2. **Sleeps too short for the real wallclock**. `time uv run
   census-augment run --config tools/demo/config.yaml` measured 35 s
   wallclock in the dev container on warm cache (16% CPU, ~29 s of
   I/O wait on top of ~6 s of compute — see issue #43 for the perf
   investigation). Even `discover` commands pay a 5-10 s typer +
   pandas + geopandas import cost under chromium PTY.

Three-pronged fix in all three tapes:

- **`Env PYTHONUNBUFFERED "1"`** at the top forces Python to flush
  stdout/stderr line-by-line.
- **`Hide` / `Show` jump-cut pattern** for every scene that invokes
  `census-augment`. The recorded GIF goes "command typed → brief pause
  → output appears" instead of forcing the viewer to watch 35 s of
  empty terminal. VHS's `Hide` directive pauses frame capture while
  the shell keeps running underneath; `Show` resumes against the now-
  populated terminal state. Per scene:

  ```
  Type   "<command>"
  Enter
  Sleep  1s          # let the typed command sit on screen briefly
  Hide               # frame capture off
  Sleep  40s         # shell runs the actual command off-camera
  Show               # frame capture on, against populated terminal
  Sleep  3s          # let the viewer see the output
  Screenshot ...
  ```

  Sleeps for the off-camera execution windows: 40 s for `run` scenes
  (covers measured 35 s + margin), 12 s for `discover` scenes (Python
  startup + sub-second work).

- **Pre-warm Hide block in `preset-features.tape`** had `Sleep 8s`
  after a `census-augment run` that takes 35 s — the shell was still
  running when scene 1 started, which is why the user's first round
  of preset-features frames showed pre-warm command lines stacked
  with scene 1's typed commands. Bumped to `Sleep 40s`.

Pure-bash scenes (`cat`, `head -25`) keep their short Sleeps — no
Python startup to wait through.

The visible GIF length stays roughly the same as the original (~25-30 s
per demo) because the 35 s waits are now off-camera. The total
real-time render takes longer (each run scene now waits 40 s while the
shell completes the actual command) but that only affects the renderer,
not the viewer of the final GIF.

**Margin bumps for parallel-render contention.** First pass with the
Hide/Show pattern still left two frames empty
(`demo-4-output.png`, `discover-datasets-4-presets.png`) — the same
scenes worked in isolation but failed when the renderer was concurrently
executing `census-augment run` on the other two tapes. Bumped the
specific Sleeps that fired during peak contention: discover scenes'
Hide-window 12s → 18s, demo scene 4's cut Sleep 4s → 6s. Margin under
serial render is now generous; under parallel render it's sufficient.

### Updated — `tools/demo/config.yaml` comment

Removed the stale `# PRESETs are intentionally excluded ... until
issue #23 lands` block. That issue closed back in PR #26; the
preset-features.tape demo covers the PRESET namespace separately.
Comment was visible in every rendered headline demo frame —
misleading for any viewer who landed on the GIF.

### Added — Parallel demo rendering under `--all`

Each tape is fully independent at render time (own tape file, own
output GIF, own PNG frames). `tools/demo/render.sh --all` (and the
PowerShell equivalent) now spawn one vhs per tape concurrently and
wait for the batch to finish, instead of rendering them
sequentially. For the current three-tape bundle that's roughly a
3× wall-clock reduction.

Concurrency is implicit (spawn-all, wait-all). With three tapes
each running its own chromium (~200-400 MB RAM each), the dev
container handles fine. If the tape count grows enough to thrash,
this turns into a worker-pool problem — easy to add a concurrency
cap later.

### Changed — Per-tape vhs log files

Render scripts now write each tape's vhs output to
`tools/demo/.last-render-<slug>.log` instead of all teeing into a
shared file. Required for parallel mode (interleaved stdout
across tapes would be unreadable). The aggregate
`tools/demo/.last-render.log` is rebuilt at the end by
concatenating per-tape logs in slug order, so the diagnostic UX
(`cat tools/demo/.last-render.log`) is unchanged. All log files
are gitignored.

### Fixed — `bash: census-augment: command not found` in rendered demos

When PR #29 switched dev-container demo rendering from a Docker
image (which had `census-augment` apt-installed system-wide) to
native vhs (which runs in the host process tree), every tape that
invokes `census-augment` failed inside the recorded subshell:

```
> census-augment run --config tools/demo/config.yaml
bash: census-augment: command not found
```

vhs spawns its own bash subshell to capture each tape. That
subshell inherits the parent's PATH — and `render.sh`'s parent
PATH didn't include `.venv/bin/`. The pre-warm worked (it used
`uv run census-augment ...`) but the tape's raw `census-augment`
commands didn't.

The symptom was particularly nasty: `vhs` itself exits 0 (it
successfully recorded the failure). Caught only when squinting
at the rendered GIF or PNGs.

Fixed in `render.sh` and `render.ps1` by wrapping the vhs
invocation in `uv run`:

```bash
uv run vhs "$tape_path"   # was: vhs "$tape_path"
```

`uv run` prepends `.venv/bin/` to PATH for the entire process
tree, so vhs's recorded subshell now finds `census-augment` on
PATH. No tape change needed.

### Added — `.last-render.log` for post-render diagnostics

Both `render.sh` and `render.ps1` now tee their vhs output to
`tools/demo/.last-render.log` (gitignored, overwritten per
invocation). Catches the class of breakage above — a tape that
records a `command not found` error inside the captured shell
won't surface as a non-zero exit, but the log makes it visible.
Per-tape sections are timestamped so the log stays useful when
re-rendering with `--all`.

### Added — Per-scene PNG snapshots from every demo tape

Each VHS tape in `tools/demo/` now writes per-scene `Screenshot
<path>.png` snapshots alongside the animated GIF. 12 PNGs total
(4 per tape) land in `docs/frames/`. Useful for:

- Embedding in static contexts (blog posts, Slack previews,
  anywhere GIF animation doesn't reliably autoplay).
- Sharing a specific frame without forcing the reader to wait for
  the GIF to loop.
- Letting LLM-based code review actually see what the demo shows
  (single frames are viewable; animated GIFs aren't).

Render is the same as before — `make demos` (or
`./tools/demo/render.sh --all`) produces both the GIFs and the
PNGs in one pass. `docs/frames/README.md` documents the file
naming and per-scene mapping.

### Fixed — `uv` hardlink warning on every operation inside the dev container

Every `uv sync` / `uv run` inside the dev container printed:

```
warning: Failed to hardlink files; falling back to full copy.
This may lead to degraded performance.
```

Cause: uv's cache (`~/.cache/uv/` on the container's overlayfs) and
the project venv (`.venv/` under the bind-mounted workspace) are on
different filesystems. Cross-filesystem hardlinks fail, so uv falls
back to copy and warns every time.

Fixed by setting `UV_LINK_MODE: "copy"` in `devcontainer.json`'s
`containerEnv` — tells uv "we know, use copy mode quietly". The
"performance degradation" is negligible for our dependency tree
(the cache still avoids re-downloads; only the install hop changes).

### Added — Tool excludes for `.claude/` agent scratch space

`pyproject.toml` now tells ruff, mypy, and pytest to skip
`.claude/worktrees/<slug>/`. Without these, each tool re-scans
every source file once per active worktree AND honours each
worktree's own `pyproject.toml` via nested-config discovery — so a
rule disabled on `main` keeps firing inside in-flight branch
worktrees, producing a wave of phantom findings.

| Tool | Block | Setting |
| --- | --- | --- |
| ruff | `[tool.ruff]` | `extend-exclude = [".claude/"]` |
| mypy | `[tool.mypy]` | `exclude = ['^\.claude/']` |
| pytest | `[tool.pytest.ini_options]` | `testpaths = ["tests"]` |

`CLAUDE.md` documents this as a project convention and lists the
current exclusion set, so future agents adding a new tool that
auto-walks the tree (coverage, black, pre-commit, etc.) extend the
list rather than re-discover the bug.

Pattern documented in
[cauldnz/aus-fuel-forecaster#17](https://github.com/cauldnz/aus-fuel-forecaster/issues/17).

### Added — `Makefile` for common workflows

A `Makefile` at the repo root wraps the everyday dev commands:

```
$ make
Usage: make <target>
  help            Show this help

Setup:
  install         Install project + dev deps into .venv/
  clean           Remove caches and build artefacts (keeps .venv/)
  clean-all       clean + remove .venv/ (full reset)

Test & quality:
  test            Run hermetic pytest suite
  test-fast       pytest -x --ff (fail fast, failed-first)
  lint            ruff check .
  format          ruff format . (writes files)
  typecheck       mypy src/ tools/
  check           lint + typecheck + test (CI-equivalent)

Smoke & real-data:
  smoke           Quick wire-up check (CLI, registries, PRESET specs)
  verify-real     Real-data parser check (hits live ABS endpoints)

Demos:
  demo            Render docs/demo.gif (headline)
  demos           Render every tape in tools/demo/

Build:
  build           Build the wheel
  build-test      Build wheel + run wheel-install regression test
```

`make` with no args lists targets. Help text is parsed from `## ...`
doc comments after each target — add a new target with an inline
doc comment and it shows up automatically.

POSIX/bash assumptions throughout; Windows users run inside the
dev container or WSL. Underlying `uv run` commands still work
directly for anyone who'd rather skip Make.

### Fixed — Chromium sandbox blocked by Docker's default seccomp profile

PR #32 installed `chromium-sandbox` so chromium had its setuid
helper. The next render attempt got past that point but hit a
different failure:

```
Failed to move to new namespace: PID namespaces supported,
Network namespace supported, but failed: errno = Operation not
permitted
```

Chromium's sandbox creates user namespaces via the `clone(2)`
syscall with `CLONE_NEWUSER`. Docker's default seccomp profile
blocks this syscall for non-root containers — and the dev
container deliberately runs as `vscode` (non-root) so file
permissions on bind-mounted host paths stay sane.

(This is why the original Docker-based render path worked: the
VHS image ran as root, and chromium auto-skips the sandbox when
running as root. Same code, different security posture.)

Fixed by adding two flags to `devcontainer.json`'s `runArgs`:

- `--security-opt seccomp=unconfined` — lifts the seccomp filter
  so chromium's namespace clone succeeds.
- `--ipc=host` — chromium's recommended IPC mode under Docker
  per Playwright's docker docs, avoiding shared-memory issues
  during GIF encoding.

This is the same posture every Playwright / Puppeteer Docker
workflow uses. The dev container runs trusted user-attached code;
not appropriate for multi-tenant CI running untrusted browser
content. Documented in `.devcontainer/README.md`.

### Fixed — Native VHS render failed with "No usable sandbox!"

After PR #31 installed the chromium runtime libs, the next render
attempt aborted at chromium startup:

```
No usable sandbox! If this is a Debian system, please install the
chromium-sandbox package to solve this problem.
```

Inside a container, unprivileged user namespaces are typically
disabled, so chromium needs its setuid sandbox helper to start.
The `chromium` package only *recommends* `chromium-sandbox`, and
`apt-get install --no-install-recommends` (which we use to keep
the image lean) drops recommended-but-not-required deps. Result:
chromium gets installed but its sandbox helper doesn't.

Fixed by naming `chromium-sandbox` explicitly in the apt-install
list. Verified against
[packages.debian.org/bookworm/chromium-sandbox](https://packages.debian.org/bookworm/chromium-sandbox)
— ~377 kB installed, in bookworm main.

### Fixed — Native VHS render in devcontainer failed for missing chromium libs

After the dev container's post-create finished installing vhs +
ttyd + ffmpeg, `tools/demo/render.sh --all` failed at the first
render with:

```
could not launch browser: ... /home/vscode/.cache/rod/browser/
chromium-1321438/chrome: error while loading shared libraries:
libatk-1.0.so.0: cannot open shared object file: No such file or
directory
```

VHS uses `go-rod` under the hood, which downloads its own headless
chromium build to `~/.cache/rod/`. That binary needs the same
shared libraries any chromium build needs (`libatk`, `libnss`,
`libgbm`, `libgtk`, ...) — but the base devcontainer Python image
doesn't ship them.

Fixed by adding `chromium` to the apt-install list in
`post-create.sh`. We don't actually use the apt-installed chromium
binary (vhs has its own); we install it for its dependency closure,
which pulls in every shared library go-rod's chromium needs at
runtime. Verified `chromium` is in Debian bookworm main and depends
on `libatk1.0-0 (>= 2.32.0)` against
[packages.debian.org/bookworm/chromium](https://packages.debian.org/bookworm/chromium).

### Fixed — Devcontainer post-create failed to install `ttyd` from apt

PR #29 added a step to `post-create.sh` that ran:

```
apt-get install -y --no-install-recommends ttyd ffmpeg bsdmainutils
```

ttyd is **not** in Debian bookworm's main repo, so apt aborted with
`E: Package 'ttyd' has no installation candidate` and the
post-create halted with exit 100 — leaving the dev container
half-configured (uv installed but no native vhs).

Fixed by following the same pattern we already use for vhs itself:
download the upstream static binary from the ttyd GitHub release
(verified against the live release listing —
`https://github.com/tsl0922/ttyd/releases/tag/1.7.7` ships
`ttyd.x86_64` and `ttyd.aarch64` as standalone binaries; no
archive to extract). `ffmpeg` and `bsdmainutils` continue to come
from apt, which does have them.

Author note: this was a Real Data First miss — I assumed ttyd was
available via apt without checking. CLAUDE.md's rule applies to
package availability the same way it applies to schema columns:
verify against the real source, don't guess. Lesson learned.

### Fixed — Demo Dockerfile missing `features/` and `datasets/` (regression from v1.4.1)

`tools/demo/Dockerfile` only copied `pyproject.toml`, `README.md`,
and `src/` into the build context. v1.4.1's wheel-bundles-specs fix
(closes #19) added `[tool.hatch.build.targets.wheel.force-include]`
entries for `datasets/` and `features/` to `pyproject.toml`. Inside
the demo image's `pip install /work` step, hatchling failed:

```
FileNotFoundError: Forced include not found: /work/features
```

That broke `tools/demo/render.sh` end-to-end (anyone, not just
dev-container users). Fix: extend the Dockerfile's `COPY` lines to
also copy `datasets/` and `features/` into `/work/`. Comment notes
the dependency on the pyproject.toml force-include block so the two
don't drift again.

### Added — `--local` / `--docker` flags on `tools/demo/render.sh` and `render.ps1`

Demo rendering now supports two modes:

- **`--local`** — runs `vhs` natively from PATH. Requires `vhs`,
  `ttyd`, `ffmpeg`, and `column` available on the host. Fast; no
  Docker dependency.
- **`--docker`** — builds the custom VHS image and renders through
  it. Works on any host with Docker reachable.

The default is auto: prefer `--local` if `vhs` is on PATH, else
fall back to `--docker`.

This pairs with the dev container update below: rendering from
inside the dev container no longer round-trips through host Docker
(via `docker-outside-of-docker`), removing one layer of indirection.

### Added — Native VHS in the dev container

`.devcontainer/post-create.sh` now installs `vhs` (Go release
binary), `ttyd`, `ffmpeg`, and `bsdmainutils` so
`tools/demo/render.sh` renders demos natively inside the dev
container by default. The host Docker socket is still mounted (via
the `docker-outside-of-docker` feature), so `--docker` mode remains
available for testing the Dockerfile path.

### Fixed — Dev Container build broken by yarn apt-source GPG key rotation

VSCode `Reopen in Container` failed against the Python base image
with:

```
The following signatures couldn't be verified because the public
key is not available: NO_PUBKEY 62D54FD4003F6525
```

`mcr.microsoft.com/devcontainers/python:1-3.11-bookworm` ships a yarn
apt source whose upstream signing key was rotated; the cached key in
the image no longer validates, so every `apt-get update` run by the
devcontainer features fails. Yarn isn't used by anything in this
project.

`.devcontainer/devcontainer.json` now builds from a tiny local
`.devcontainer/Dockerfile` that bases on the Microsoft image and
removes `/etc/apt/sources.list.d/yarn.list` before features run.
Feature installs proceed cleanly.

The Dockerfile can be deleted once Microsoft refreshes the upstream
image with the new yarn key.

## [1.4.2] - 2026-05-10

### Fixed — PRESET column refs against real GCP DataPack (closes #23)

Every PRESET shipped in v1.3 (`pct_renters`, `pct_drive_to_work`,
`pct_aged_65_plus`, `pct_employed_full_time`, `pct_one_parent_family`,
`motor_vehicles_per_dwelling`) referenced column names that don't
exist in the real 2021 GCP DataPack. Hermetic tests passed because
the synthetic test fixtures encoded the same broken names; first
real-data run hit `CatalogError: column 'X' not found in table 'Y'`.

Each PRESET's `numerator:` / `denominator:` block has been rewritten
against the actual DataPack column names, captured in
`tests/fixtures/gcp-schemas/G##.txt` as a reviewable artifact.
Highlights:

- **`pct_renters`** — `G37.R_Tot` → `G37.R_Tot_Total`,
  `G37.OPDs_Total` → `G37.Total_Total` (G37 is implicitly OPD-scoped).
- **`pct_drive_to_work`** — camelCase `OneMethod_*_P` →
  snake_case `One_method_*_P` across all four numerator fields.
- **`motor_vehicles_per_dwelling`** — there's no `Total_motor_vehicles`
  column; rewrote as a `weighted_sum` over the per-bucket counts
  (0/1/2/3/4mo with weights 0..4). Denominator switched from the
  fictional `Total_dwellings` to `Num_MVs_per_dweling_Tot` (excludes
  the not-stated bucket).
- **`pct_employed_full_time`** — collapsed M+F sums to the
  pre-summed `_P` columns (`lfs_Emplyed_wrked_full_time_P` /
  `lfs_Tot_LF_P`).
- **`pct_aged_65_plus`** — referenced a non-existent `G04` table
  (the GCP DataPack splits this into `G04A` males / `G04B` females,
  and neither has a 65+ total). Rewrote as a `sum` over G01's three
  65+ age bands (`Age_65_74_yr_P`, `Age_75_84_yr_P`, `Age_85ov_P`).
- **`pct_one_parent_family`** — completely fictional column names.
  Rewrote: numerator = `G29.OPF_ChU15_a_Total_F`, denominator =
  sum of `G29.CF_ChU15_a_Total_F` + `G29.OPF_ChU15_a_Total_F`
  (families with children under 15).

End-to-end validated: a single `census-augment run` with all six
PRESETs against real ABS data produces sensible values for the five
demo SA2s (Sydney CBD 65.9% renters, 14.8% aged 65+, 0.62
MVs/dwelling; etc.).

### Added — Acid-test verifier step for PRESETs

`tools/verify_real_parsers.py` gains a "PRESET source-column
resolution" step that loads every registered PRESET, walks its
`source_fields()`, and asks the live GCP catalog to resolve every
ref. Fails loudly on any unresolved column. This is the gate that
should have caught #23 the day v1.3 shipped — its presence now
catches future drift the day it lands.

This is the first practical application of the v1.4.1
[Real Data First](CLAUDE.md#real-data-first) rule's "acid test"
clause: every external column ref in a spec or fixture must be
backed by a fixture file, a re-fetch script, or a live verifier
probe. PRESETs now have all three.

### Added — Reviewable schema reference dumps

`tests/fixtures/gcp-schemas/G##.txt` now contains the
`census-augment discover --table <id>` output for every GCP table
referenced by a registered PRESET (G01, G02, G04A, G04B, G29, G34,
G37, G43, G62). Captured 2026-05-10. Future PRESET authors can diff
their column refs against these dumps before pushing; future
releases (e.g. 2026 GCP) can use them as the v2021 baseline. See
`tests/fixtures/gcp-schemas/README.md` for re-generation steps.

### Updated — Test fixtures mirror real schemas

`tests/test_features.py` and `tests/test_enrich_presets.py` synthetic
DataFrames previously used the broken column names (so tests passed
against the broken specs). Both updated to mirror the real GCP
schema; tests would now fail if a PRESET drifts away from real ABS
column names.

### Added — VSCode Dev Container

`.devcontainer/` configures a Linux Python 3.11 sandbox (matching the CI
environment) with `uv`, `gh`, build tooling, and host-Docker access for
VHS demo rendering. VSCode users can open the repo and `Reopen in
Container` for a one-command development setup that bypasses Windows /
Python / venv friction. See [`.devcontainer/README.md`](.devcontainer/README.md).

### Added — `.gitattributes` to enforce LF line endings on shell scripts

Prevents `\r: command not found` errors when shell scripts are checked
out on Windows hosts and then run from Linux (devcontainer / CI).
Affects `*.sh` and `*.tape` files only.

## [1.4.1] - 2026-05-10

### Fixed — wheel install ships dataset/feature spec markdown (closes #19)

v1.3 / v1.4 wheels were built with `[tool.hatch.build.targets.wheel]
packages = ["src/census_augment"]`, which copies only the `*.py` files
under the package. The 13 markdown spec files at `datasets/*.md` and
`features/*.md` (the actual content of the pluggable framework) sat at
the repo root and never made it into the wheel — so anyone installing
via `pip install abs-census-augmentor @ git+...` ended up with empty
registries:

```python
>>> from census_augment.datasets import registry
>>> list(registry.list_datasets())
[]   # should be 5: gcp_2021, seifa_2021, erp_by_sa2, dss_payments, ato_personal_income
>>> from census_augment.features import features
>>> list(features.list_features())
[]   # should be 6 PRESETs
```

Source checkouts and `pip install -e .` worked because the runtime
resolver's primary path looks at the repo-root `datasets/` /
`features/` directories. The wheel-install fallback path (private
mirrors at `<package>/datasets/_specs/` and `<package>/_features/`)
existed in the resolver code but the build didn't put any files there.

Fixed by adding `force-include` to `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"datasets" = "census_augment/datasets/_specs"
"features" = "census_augment/_features"
```

The new wheel ships all 13 markdown files at the paths the resolver
already expects. No code changes needed — only the build config.

Verified end-to-end against a fresh isolated venv: `pip install
abs-census-augmentor==1.4.1` (wheel-only, no source checkout) produces
all 5 registered datasets and all 6 PRESET features.

### Added — Wheel-install regression test + CI step

`tests/test_wheel_bundles_specs.py` adds two `pyproject.toml`
lock-down checks (force-include destinations match what the runtime
resolver expects) plus one end-to-end test that builds a wheel,
installs it in a subprocess venv, and confirms both registries
populate. The E2E test is gated on `WHEEL_E2E=1` because it's slow
(~10s) — local `pytest` runs skip it, but CI sets the flag and runs
it on every push.

## [1.4.0] - 2026-05-10

### Added — PRESET features as first-class pipeline variables

`PRESET.<id>` is now a usable variable reference in any
config — alongside the existing `G\d+.<col>` (GCP), `SEIFA.<field>`,
`ERP.<field>`, `DSS.<field>`, and `ATO.<field>` namespaces.

```yaml
variables:
  pop_total:    G01.Tot_P_P
  renters_pct:  PRESET.pct_renters         # NEW: PRESET as first-class ref
  drove_pct:    PRESET.pct_drive_to_work
  irsd_decile:  SEIFA.irsd_aus_decile
```

Behind the scenes, `CensusEnricher.build_lookup()` now:

1. Detects every `PRESET.<id>` in the configured variables.
2. Looks each one up in the `FeatureRegistry` and walks
   numerator + denominator to collect the underlying source columns
   (e.g. `G37.R_Tot`, `G37.OPDs_Total` for `pct_renters`).
3. Auto-loads those source columns through the existing GCP /
   registered-dataset dispatch — deduplicated across PRESETs, so two
   PRESETs that share `G01.Tot_P_P` only fetch it once.
4. Runs `FeatureEvaluator` against the workspace and surfaces the
   derived column as `<output_prefix><friendly>` (e.g.
   `sa2_renters_pct`).
5. Drops the synthetic source columns from the result so callers see
   only the variables they explicitly asked for.

In v1.3 the same workflow was possible but required the user to
manually request the source columns and apply `FeatureEvaluator`
themselves; `examples/library_with_preset_features.py` showed the
manual recipe. v1.4 makes both `Pipeline.run()` (file in/out via the
CLI) and `Pipeline.augment(df)` (library in/out) treat
`PRESET.<id>` like any other variable namespace.

The standalone `FeatureEvaluator` / `FeatureRegistry` API is unchanged
and still available for direct use against an existing
SA2-keyed DataFrame.

### Added — `FeatureSpec.source_fields()` helper

A new `set[str]` accessor on `FeatureSpec` that returns every
`<NAMESPACE>.<field>` ref the evaluator will look up — used internally
by the pipeline integration above and useful for downstream tooling
that wants to introspect what GCP / dataset columns a PRESET depends
on.

### Verified

`tools/verify_real_parsers.py` confirmed against the live ABS /
data.gov.au endpoints that all v1.3 datasets (SEIFA, ERP, DSS, ATO)
still parse cleanly post-#16. No fixtures or schemas changed in
v1.4.

## [1.3.0] - 2026-05-09

### Fixed — G-NAF remote/cache mode against the real ``minus34.com`` bucket (closes #17)

The v1.2.2 (#9) and v1.2.3 (#14) fixes both targeted the wrong
subdirectory inside the gnaf-loader bucket. Both releases worked
against synthetic test fixtures but failed against the real bucket
with a DuckDB BinderException — the previously-targeted
``address_principal_census_<year>_boundaries/`` carries only
boundary-ID columns (``mb_code_<year>``, ``sa1_code_<year>``, ...,
``lga_code_<year>``, ``poa_code_<year>``, ...) and **no address column,
no lat/lon**.

The actual G-NAF Core data lives in ``address_principals/``, which
carries one row per address with all the columns the geocoder needs
(``gnaf_pid``, ``address``, ``latitude``, ``longitude``, ``postcode``,
``mb_2016_code``, ``mb_2021_code``). v1.3 targets that directory
correctly.

While in the area, two related fixes shipped in the same change:

- **Regional endpoint for dotted bucket names.** v1.2.2's fix for the
  ``minus34.com`` TLS-cert mismatch used path-style on the *global*
  endpoint (``https://s3.amazonaws.com/{bucket}/{key}``). That works
  for boto3 (which follows the 301 redirect to the bucket's region)
  but fails under DuckDB ``httpfs`` which doesn't follow redirects.
  v1.3 resolves the bucket's region once via ``head_bucket`` and
  uses ``https://s3.{region}.amazonaws.com/{bucket}/{key}`` directly
  (path-style, regional, no redirect needed).

- **Full ADDRESS_LABEL via component concatenation.** The
  ``address_principals.address`` column carries only the street
  portion (e.g. ``"115 LAWRENCE ROAD"``) — locality, state, and
  postcode are separate columns. The view's SELECT clause now
  ``CONCAT_WS(' ', address, locality_name, state, postcode)`` to
  produce a normalised label that matches what
  :func:`normalize_address` produces from user input. Without this,
  Tier 1 exact-match would silently miss for almost every input.

Two new regression tests use moto fixtures with the production
bucket's full sibling layout (``address_principals/`` +
``address_principal_admin_boundaries/`` +
``address_principal_census_2016_boundaries/`` +
``address_principal_census_2021_boundaries/``) and assert that the
parser picks the right subdirectory. Plus an explicit-failure test
that fails loudly when only boundary subdirectories are present
(rather than letting DuckDB surface the BinderException).

Verified end-to-end against the live ``minus34.com`` bucket
(``geoscape-202602`` release): 15,015,573 rows in the ``gnaf`` view;
sample query for "GEORGE STREET" addresses in postcode 2000 returns
correctly-formatted ADDRESS_LABEL strings with MB_CODE / lat / lon
all populated.

### Added — pluggable dataset framework (closes #15)

The pipeline (geocode → SA2 resolve → enrich) keeps the same shape, but
the *enrich* stage now dispatches across a registry of datasets rather
than hard-coding the GCP DataPack. The 2021 GCP DataPack is now one
entry in the registry alongside four new datasets:

- **`seifa_2021`** *(closes #10)* — ABS Socio-Economic Indexes for
  Areas. 4 indexes (IRSD, IRSAD, IER, IEO) × 7 flavours (score, Aus
  rank/decile/percentile, State rank/decile/percentile) + URP +
  state_abbreviation. ~2,366 SA2s, ~150 KB compressed source.
- **`erp_by_sa2`** — ABS Estimated Resident Population by SA2 (annual,
  long-history series 2001 onwards). Latest year's `population_total`
  + per-year `population_history_<year>` columns.
- **`dss_payments`** — DSS Payment Demographic Data (quarterly).
  Recipients per payment type (Age Pension, JobSeeker, etc.) with a
  `release_quarter` column carrying the YYYY-Qn snapshot identifier.
- **`ato_personal_income`** — ABS Personal Income (Table 1 SA2
  summary). Median / mean / sum total income + earners count + median
  age of earners.

Variable namespaces (`SEIFA.*`, `ERP.*`, `DSS.*`, `ATO.*`) route
through the registry. The existing GCP convention (`G02.foo`,
`G62.bar`, ...) is unchanged.

Each dataset is described by a markdown spec at `datasets/<id>.md`
with YAML front-matter declaring custodian / licence / cadence /
schema (spec §20.1). The `census-augment discover --datasets`
command lists registered datasets; `--dataset <id>` shows one's
schema.

### Added — derived features / PRESETs (closes #11)

Curated catalog of ratios with the right denominator pre-baked. Six
PRESETs ship in v1.3, each as a markdown spec at `features/<id>.md`:

- `pct_drive_to_work`, `pct_renters`, `pct_aged_65_plus`,
  `pct_employed_full_time`, `pct_one_parent_family`,
  `motor_vehicles_per_dwelling`.

The `FeatureEvaluator` (in `src/census_augment/features.py`) reads a
spec and computes the ratio against a SA2-keyed DataFrame of source
columns. Edge-case handling matches the spec's
`zero_denominator: null|zero|error` and
`out_of_bounds_behaviour: clip|warn|error` knobs. Default
`out_of_bounds_behaviour: warn` rather than `clip` so
denominator-mismatch bugs surface rather than being silently masked.

`census-augment discover --features` lists the PRESET catalogue.

### Changed

- **`CensusEnricher.build_lookup()` dispatches across datasets.**
  GCP refs (`G\d+.<col>`) keep the existing path through
  `VariableCatalog` + `DataPacksDataSource`. Non-GCP refs route to
  the registered dataset's fetcher. Mixed configs (GCP + SEIFA + ATO
  in one `variables` dict) work transparently.
- **`Pipeline.from_config()`** now pre-validates only GCP-shape
  variables against the catalog. Non-GCP variables are validated
  lazily when the enricher hits each dataset's fetcher.
- **CLI `discover`** gained `--datasets`, `--dataset <id>`, and
  `--features` flags.

### Internals

- New module: `src/census_augment/datasets/` with `_spec.py`,
  `_registry.py`, `_protocol.py`, plus per-dataset fetchers
  (`_seifa.py`, `_erp.py`, `_dss.py`, `_ato.py`).
- New module: `src/census_augment/features.py` (FeatureSpec,
  FeatureEvaluator, FeatureRegistry).
- `CensusEnricher` constructor gained an optional `data_dir`
  argument (required when non-GCP variables are present, since
  per-dataset cache directories live under it).

### Tests

501 hermetic tests pass (was 422 in v1.2.3; +79 new tests for
specs, registry, fetchers, evaluator, and dispatch). Real-network
smoke verified during development against ABS / data.gov.au:

- SEIFA real source: 2,366 SA2s, all 4 indexes parsed.
- DSS real source: 2,454 SA2s, 22 payment-type columns, latest
  release auto-resolved (2025-Q4 at time of testing).
- ERP real source: 2,454 SA2s, 25-year history series, latest
  reference year auto-detected.
- ATO real source: 2,450 SA2s, FY 2022-23 release, summary stats
  (sample SA2 "Braidwood" median income $49,963 — plausible).

### Breaking changes

v1.3 is **mostly non-breaking** for existing v1.2.x callers, but a few
internal contracts shifted in service of the registry refactor:

- `CensusEnricher.__init__()` gained an optional `data_dir` parameter.
  Existing callers without it continue to work for **GCP-only**
  variable configs. Non-GCP variables now require the parameter; an
  explicit error is raised if it's missing. v1.2.x downstreams that
  use only GCP keep working unchanged.
- `enrich.py` no longer exports a public per-table loader API — the
  registry is the public surface for variable resolution. v1.2.x
  callers that imported internal helpers from `enrich.py` (none in
  the public API) may need to migrate.

### Deferred to v1.4

- **PRESET integration into the pipeline.** v1.3 ships PRESETs as a
  standalone API (`FeatureEvaluator`); using one in the pipeline
  config requires the user to also request the underlying
  numerator/denominator source columns in the same `variables` dict.
  v1.4 will auto-load the source columns so users can write
  `variables: {pct_renters: PRESET.pct_renters}` without thinking
  about the underlying GCP fields.
- **Cross-dataset features** (e.g. DSS recipients / ERP population).
  The format supports `dataset:` as a list, but the evaluator's
  multi-dataset handling needs the auto-load step to land first.
- **Tables 2–9 of ATO Personal Income** (age/sex breakdowns, income
  distribution, employee/investment/super/own-business income).
- **`Pipeline.create(releases=...)`** for per-dataset release pinning
  (spec §20.4) — defaults work for now (latest of each).

## [1.2.3] - 2026-05-07

### Fixed

- **G-NAF remote / cache mode now reads from the actual gnaf-loader
  layout.** Follow-up to #8 / v1.2.2. The previous fix assumed G-NAF
  Core lived as flat parquets at the root of `geoparquet/` with ABS
  boundary tables in subdirectories. The real bucket layout is the
  opposite: every dataset (including G-NAF) is in a subdirectory,
  and the v1.2.2 "no subdirectories" filter rejected everything,
  failing with `No .parquet files found at .../geoparquet/`. (Resolves
  #12.)

  v1.2.3 auto-detects two layouts:

  * **gnaf-loader** *(default for the production bucket)*: data lives
    at `geoparquet/address_principal_census_{year}_boundaries/` —
    gnaf-loader's denormalised join of address principals with the
    ABS census boundary IDs. Column names are PostgreSQL-lowercase
    (`gnaf_pid`, `address`, `latitude`, `mb_{year}_code`, ...); the
    `gnaf` view aliases them to the uppercase
    `ADDRESS_DETAIL_PID` / `MB_CODE` etc. that the geocoder queries.
  * **legacy / bring-your-own** *(fallback)*: a flat parquet at the
    release root with already-uppercase columns. Used by the
    existing test fixtures and by users who pre-build their own
    G-NAF parquet from the official Geoscape PSV.

  Detection runs on every `open_connection()` — listing the
  appropriate paths in S3 or the local cache. If neither layout is
  present, a clear `RuntimeError` lists what was tried.

### Added

- **`census_year` parameter on `GnafDataSource`** (default `2021`),
  also wired through as `cfg.census.year` in YAML configs. Selects
  which year's boundaries subdirectory to read under the gnaf-loader
  layout — letting users targeting the 2016 census pull `mb_2016_code`
  instead.
- New tests covering the gnaf-loader path end-to-end against a moto
  S3 server: layout auto-detection, year selection, mixed-layout
  preference (gnaf-loader wins over legacy when both are present),
  cache-mode download preserving subdirectory structure on disk so
  the next run's detector finds it, legacy fallback for buckets
  without the gnaf-loader subdir.

### Changed

- `_validate_schema_local` removed; schema validation now runs as a
  `DESCRIBE gnaf` against the constructed view (cheap — parquet
  footer metadata only). Catches column-mismatch errors after
  aliasing has applied for the gnaf-loader layout, which is what
  callers actually care about.
- `_download_release_from_s3` preserves subdirectory structure when
  it sees a gnaf-loader layout — files land at
  `<release_dir>/address_principal_census_{year}_boundaries/{filename}`
  rather than flat at the release root. Required for cache-mode
  layout detection to find them.
- `data_sources.gnaf_parquet_filter` semantics tightened: still a
  regex against the relative key, but now only consulted under the
  legacy code path. Under gnaf-loader the subdirectory itself does
  the scoping. Setting the regex explicitly also re-enables
  subdirectory parquets in the legacy path (the v1.2.2 default
  flat-only behaviour applies only when no regex is set).

## [1.2.2] - 2026-05-07

### Fixed

- **G-NAF remote (and cache) mode no longer pulls in non-G-NAF
  parquets from the gnaf-loader bucket.** The bucket co-locates
  G-NAF Core flat parquets at the root of `geoparquet/` with ABS /
  OSM boundary tables in named subdirectories (e.g.
  `abs_2016_gccsa/part-00000-*.snappy.parquet`). Previously,
  `_list_parquet_objects_on_s3` returned everything; remote mode
  fed the lot to DuckDB's `read_parquet([...])` and the schema
  validator caught the boundary parquet's `gcc_16code` /
  `gcc_16name` / `geom` columns as a "missing required columns"
  failure. Cache mode had the same bug latent — it'd download all
  the boundary files alongside G-NAF.

  The default filter now accepts only flat parquets directly under
  `geoparquet/`. Override via the new `parquet_filter` constructor
  arg (or `data_sources.gnaf_parquet_filter` in YAML) — a regex
  matched against the relative key — for buckets with a different
  layout. Resolves #8.

- **Dotted bucket names (e.g. `minus34.com`) auto-switch to
  path-style URLs in remote mode.** AWS's wildcard cert
  `*.s3.amazonaws.com` only matches a single subdomain level, so
  `minus34.com.s3.amazonaws.com` (virtual-hosted) fails TLS hostname
  verification — libcurl reports `SEC_E_WRONG_PRINCIPAL` /
  hostname-mismatch. `_build_object_url` now detects dots in the
  bucket name and constructs `https://s3.amazonaws.com/{bucket}/{key}`
  (path-style on the global endpoint, which redirects to the
  bucket's region). Users no longer need to set
  `s3_https_endpoint` manually for the gnaf-loader default.
  Also from #8.

## [1.2.1] - 2026-05-07

### Fixed

- **`pyproject.toml` distribution name now matches the GitHub repo.**
  Was `census-augment`; is now `abs-census-augmentor`. uv (and modern
  pip) enforce name-equality between the requested distribution and
  the metadata reported by the build backend, so `uv add
  abs-census-augmentor @ git+https://github.com/cauldnz/abs-census-augmentor.git`
  was rejected with a name-mismatch error. Resolves #7. The
  CLI command (`census-augment`) and the import name
  (`from census_augment import ...`) are unchanged — distribution
  name is independent of both.
- **`pyproject.toml` `version` field is now in sync with the
  CHANGELOG history.** Was stuck at `1.0.0` while the changelog
  documented 1.1.0 and 1.2.0 features — anyone who installed from
  main between those releases got artefacts metadata-tagged as
  v1.0.0. Bumped straight to `1.2.1` to align with reality and
  treat the name-fix above as the patch release. Going forward,
  CLAUDE.md's contributor conventions require pyproject.toml's
  `version` and the CHANGELOG to move together.

## [1.2.0] - 2026-05-06

### Added

- **`mode: remote` is now implemented.** DuckDB streams G-NAF parquet
  directly from S3 via the `httpfs` extension — no local cache,
  queries pull only the bytes they need. Set `geocoding.gnaf.mode:
  remote` in your config; everything else (release resolution, the
  geocoder tiers, the MB fast path) works unchanged.
- New config knob: `data_sources.gnaf_s3_https_endpoint` lets you
  point at an S3-compatible mirror (MinIO, Cloudflare R2, ...) or a
  test server. When set, path-style addressing is forced; default
  `None` uses AWS's virtual-hosted style.
- `census-augment gnaf-info` now reports remote-mode connectivity
  details (resolved release, configured endpoint, S3 base) instead
  of the cache-mode "not cached" message that's meaningless when
  streaming.

### Changed

- `census-augment fetch --gnaf` now exits with a friendly error in
  remote mode (rather than silently no-opping or downloading) — the
  fetch is meaningless when you're not caching.
- `_resolve_release` ignores local cache when `mode='remote'`. The
  whole point of remote mode is to skip the download; preferring a
  stale local cache for resolution would be confusing.

### Internals

- `GnafDataSource` now dispatches `open_connection` on mode:
  `_open_cache_connection` (existing path) and `_open_remote_connection`
  (new). `_validate_schema` is split into `_validate_schema_local`
  (parquet footer via pyarrow) and `_validate_schema_remote`
  (DuckDB `DESCRIBE gnaf` over httpfs).
- New `_build_object_url` helper — virtual-hosted by default,
  path-style when an endpoint override is configured.
- New `_list_parquet_objects_on_s3` shared between cache (download)
  and remote (URL construction) paths.
- `moto[s3,server]>=5` (was `moto[s3]>=5`): the `server` extra
  pulls in Flask, needed for `ThreadedMotoServer` which the new
  end-to-end remote-mode tests use to exercise DuckDB's httpfs
  against a real HTTP server (moto's `@mock_aws` only intercepts
  boto3, not httpfs's libcurl).

## [1.1.0] - 2026-05-06

### Added

- **Anonymous S3 fetch for G-NAF.** `census-augment fetch --gnaf` now
  actually downloads the `gnaf-loader` GeoParquet snapshot from
  `s3://minus34.com/opendata/` (configurable). v1.0 left this as a
  manual `aws s3 sync --no-sign-request ...` step; the same operation
  is now built in. ~10 GB across ~50 parquet files; idempotent on
  re-run (skips files already on disk, byte-for-byte).
- **`release: "latest"` resolves against S3** when there's no local
  cache. Pick up newly-published quarterly releases without editing
  the config.
- **`fetch --gnaf --refresh` re-checks S3 for newer releases** even
  when a cache exists, so a quarterly drop is one command away.
- **`tools/fetch_real_data.py` now downloads G-NAF too** (with a
  `--skip-gnaf` flag for iterating on the smaller paths).

### Changed

- README's "G-NAF setup" section trimmed: no more "you must populate
  the cache yourself" instructions. The auto-download path is the
  primary flow; bring-your-own-parquet is documented as a fallback.
- `census-augment fetch --gnaf` help text reflects the new behaviour.

### Internals

- `moto[s3]>=5` added to dev dependencies; new tests (`test_gnaf.py`)
  cover S3 listing, atomic-write, partial-resume, and the
  cache-then-fallback-to-S3 logic. Hermetic — no real network.
- New `_parse_s3_url`, `_list_releases_on_s3`,
  `_download_release_from_s3` and `_download_one` helpers on
  `GnafDataSource`. `_make_s3_client` uses anonymous `botocore`
  config (`signature_version=UNSIGNED`).

## [1.0.0] - 2026-05-02

v1.0 implements `spec.md` v1.0 — G-NAF as the primary geocoder with the
Nominatim fallback, and a mesh-block fast path for SA2 resolution.

### ⚠️ Breaking changes (output schema + config)

The output CSV's `geo_source` column now uses provider-prefixed values:

| Before (v0.1) | After (v1.0)                                                 |
| ------------- | ------------------------------------------------------------ |
| `input`       | `input` (unchanged)                                          |
| `cache`       | `nominatim_cache`                                            |
| `fresh`       | `nominatim_fresh` (or `gnaf_exact` / `gnaf_component` / `gnaf_fuzzy`) |
| `failed`      | `failed` (unchanged)                                         |

Two new columns appear in the output CSV: `geo_match_score` (populated
for `gnaf_fuzzy` rows) and `sa2_resolution` (`mb_code` |
`spatial_join` | `unmatched`).

The `geocoding:` section of `config.yaml` has restructured. Old:

```yaml
geocoding:
  provider: nominatim
  user_agent: "..."
  rate_limit_per_second: 1
  cache_enabled: true
```

New (v1.0):

```yaml
geocoding:
  providers: [gnaf, nominatim]
  cache_enabled: true
  gnaf:
    mode: cache
    release: latest
    datum: GDA2020
    fuzzy_threshold: 0.85
  nominatim:
    user_agent: "..."
    rate_limit_per_second: 1
```

To reproduce v0.1 behaviour without G-NAF: `providers: [nominatim]`
plus the new `nominatim:` subsection.

### Added

#### Geocoding
- **G-NAF Core integration** as the primary geocoder. Three offline
  match tiers (spec §19.3):
  - `gnaf_exact` — exact `ADDRESS_LABEL` match after normalisation.
  - `gnaf_component` — postcode-pre-filtered substring match on the
    canonical `<num> <street> <type>` form.
  - `gnaf_fuzzy` — `rapidfuzz` token-set similarity above a configurable
    `fuzzy_threshold`, with the score recorded in `geo_match_score`.
- Cascading provider chain (spec §7.2): `geocoding.providers` is an
  ordered list; first non-failed result wins.
- AU address normaliser (rules-based, no NLP dependencies). Handles
  AS4590 street-type abbreviations (ST→STREET etc.), state names,
  punctuation, casing.
- DuckDB-backed lookup with native Parquet reads. ~15.86 M addresses
  queried via local GeoParquet files.
- New CLI command: `census-augment gnaf-info` reports the resolved
  release, mode, on-disk path, and cache size.
- New CLI flag: `census-augment fetch --gnaf` validates the cache
  layout and downloads the Mesh Block correspondence shapefile.

#### MB → SA2 fast path (spec §7.3)
- When G-NAF returns an `mb_code`, SA2 is resolved via an O(1) dict
  lookup against the Mesh Block shapefile's `.dbf` attribute table —
  bypassing the spatial-join entirely. Resolution path is recorded
  per-row in the new `sa2_resolution` column.
- Lat/lon-input rows and Nominatim-resolved rows continue using the
  spatial-join fallback.
- `MbCorrespondenceDataSource` builds the lookup lazily by reading
  only the .dbf attribute columns via
  `pyogrio.read_dataframe(read_geometry=False)` — no geometry parsing.

#### RunSummary (spec §7.5)
- Per-tier histogram (`geo_per_tier`) and per-resolution-path counts
  (`sa2_resolution_counts`) joined the existing aggregates.
- Human-readable summary picks up "Per-tier breakdown" and "SA2
  resolution path" sections.

#### Public API
- New top-level exports: `GnafConfig`, `NominatimConfig`.

### Dependencies
- `duckdb` — G-NAF indexing + analytical queries.
- `pyarrow` — GeoParquet I/O for G-NAF.
- `rapidfuzz` — Tier 3 fuzzy matching.
- `boto3` — anonymous S3 access for `s3://minus34.com/opendata/`
  (G-NAF distribution).
- `pyogrio` — promoted from transitive to explicit (we call it
  directly to read the Mesh Block .dbf without geometry).

### Notes
- G-NAF is licensed under Geoscape's Open G-NAF EULA. The README, the
  `tools/fetch_real_data.py` output, and `census-augment fetch --gnaf`
  all carry the required attribution string. See spec §19.5.
- `mode: remote` and `mode: official` for `geocoding.gnaf` raise
  `NotImplementedError` with migration messages — only `mode: cache`
  is shipped in v1.0. Drop GeoParquet files into
  `<data_dir>/gnaf/{YYYYMM}/` (e.g. from
  `s3://minus34.com/opendata/geoscape-{YYYYMM}/geoparquet/`) to
  populate the cache; automated S3 fetching lands in a follow-up.

## [0.1.0] - 2026-05-01

Initial release. v1 implementation against `spec.md` v0.9.

### Added

#### Library API
- `Pipeline.create()` factory for one-line notebook construction.
- `Pipeline.from_config(config)` for full programmatic / YAML-driven setup.
- `Pipeline.augment(df) -> AugmentResult` for DataFrame-in / DataFrame-out
  use without file I/O.
- `AugmentResult` dataclass with the augmented DataFrame, a `RunSummary`,
  `added_columns`, and three boolean Series (`is_fully_enriched`,
  `geocoding_failed`, `sa2_unmatched`) indexed like `df` for natural
  pandas filtering (`result.df[~result.geocoding_failed]`).
- Per-call column overrides on `augment(df)` with three-state semantics:
  omit kwarg → use config default; `"col_name"` → use that column; explicit
  `None` → disable that locator for the call.
- Lenient column resolution: configured-but-absent locator columns are
  dropped with a warning rather than raising; absent columns are listed
  on `RunSummary.unused_configured_columns`.
- Public API surface (`Pipeline`, `AugmentResult`, `RunSummary`, `Config`,
  `InputConfig`, `OutputConfig`, `CensusConfig`, `DataSourcesConfig`,
  `GeocodingConfig`, `load_config`, `VariableCatalog`, `CatalogError`,
  `Geocoder`) — see spec §18.4.

#### CLI
- `census-augment run --config <yaml>` — file-in / file-out pipeline.
- `census-augment discover --config <yaml> --search <term>` /
  `--table <id>` — search the DataPack metadata.
- `census-augment fetch --config <yaml> --boundaries [--census] [--refresh]`
  — pre-fetch ABS data.
- `census-augment validate --config <yaml> [--full]` — config validation
  (structural always; semantic against the live DataPack with `--full`).
- Global `--verbose` / `-v` toggles DEBUG logging.
- `--data-dir` / `--cache-dir` flags on every command for cache overrides.

#### Data sources & cache
- ASGS SA2 boundary download from the real ABS endpoint
  (`SA2_2021_AUST_SHP_GDA2020.zip`, ~50 MB) — Shapefile, GDA2020.
- Census 2021 GCP DataPack download
  (`2021_GCP_SA2_for_AUS_short-header.zip`, ~40 MB) — CSVs +
  metadata Excel.
- DataPack metadata parser handles real ABS Excel layout (title-row
  prefix, descriptor-mode-aware code lookup, Columnheadingdescription
  field for human-readable names).
- Default cache locations use the platform user cache via `platformdirs`
  (Linux `~/.cache/census-augment/`, macOS `~/Library/Caches/...`,
  Windows `%LOCALAPPDATA%\census-augment\Cache\`). Overridable via
  `CENSUS_AUGMENT_DATA_DIR` / `CENSUS_AUGMENT_CACHE_DIR` env vars.
- `geocoding.cache_enabled = false` now genuinely disables the cache
  (uses `NullCache` no-op).

#### Geocoding
- Nominatim implementation with rate-limit + back-off (HTTP 429/503
  exponential retry up to 3 attempts; failed lookups treated as null
  coords + flagged in summary, not cached).
- Sharded JSON cache keyed by SHA-256 of the normalised address;
  atomic writes (temp file + rename) tolerate crashes mid-write.
- Pluggable `Geocoder` Protocol for future G-NAF / Google / Mapbox
  implementations (see spec §13).

#### Variable catalog
- `VariableCatalog` with `resolve(ref)`, `validate_variables(dict)`,
  `search(term)`, `list_table(table_id)`, plus `suggest_tables` /
  `suggest_codes_in_table` (`difflib`-based near-match suggestions).
- `CatalogError` raised with helpful suggestions for typos like
  `G02.Mediaan_age` → `did you mean: Median_age_persons, ...?`.

#### Verification & docs
- 258 hermetic tests (no real network); ruff + mypy strict clean
  across 18 source files.
- `tools/fetch_real_data.py` + `tools/verify_real_parsers.py` for
  opt-in real-data verification (kept out of the test suite to keep
  CI fast and ABS-uptime-independent).
- GitHub Actions CI on Python 3.11 / 3.12 / 3.13.
- `examples/library_basic.py`, `examples/library_with_overrides.py`,
  `examples/cli/` walkthroughs verified runnable against real ABS data.
- README, CLAUDE.md, full spec.md (v0.9 with 17 logged decisions).

### Known limitations
- v1 supports SA2 only. Architecture supports SA1 / SA3 — see spec §13.
- Census 2021 GCP only. Architecture supports other years and profiles
  — see spec §13. The 2026 Census DataPack format is the next real
  test (open question §15.1).
- CSV input/output only. Other formats deferred per spec §2.
- Geocoding via Nominatim only.
- Computed/derived variables (ratios, percentages) explicitly out of
  scope per spec §14 #3 — that's downstream feature engineering.

[Unreleased]: https://github.com/cauldnz/abs-census-augmentor/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cauldnz/abs-census-augmentor/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/cauldnz/abs-census-augmentor/releases/tag/v0.1.0
