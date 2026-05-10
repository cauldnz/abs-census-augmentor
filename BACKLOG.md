# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

## VHS terminal recordings (animated demos)

The README currently embeds one VHS-rendered GIF (the v1.0 demo; see
`tools/demo/`). Several v1.3+ surfaces would benefit from their own
demo GIFs — backlogging here so we can render them in batches rather
than one-by-one.

Each item below has the demo content sketched; rendering needs a
~30-line `.tape` file (model from `tools/demo/demo.tape`) plus the
Docker render path the existing demo uses. Output GIFs land in
`docs/`.

### `discover-datasets.gif` — pluggable framework introduction

Short demo (~20 s) showing the new dataset registry:

1. `census-augment discover --datasets` — lists the five registered
   datasets with namespace / status / cadence.
2. `census-augment discover --dataset seifa_2021` — shows the schema.
3. `cat datasets/seifa_2021.md | head -30` — shows the markdown spec
   format.
4. `census-augment discover --features` — shows the PRESET catalogue.

Audience: someone evaluating whether the tool is right for them; this
demo answers "what's actually available?" in 20 seconds.

### `seifa-augmentation.gif` — real SEIFA data

Demo (~25 s) showing SEIFA augmentation end-to-end on a small input:

1. Show a 5-row CSV of locations.
2. `census-augment run --config seifa.yaml` (config has
   `variables: {seifa_decile: SEIFA.irsd_aus_decile, ...}`).
3. Show the enriched output with SA2 names + IRSD deciles.

Worth picking 5 SA2s with visibly different SEIFA scores (e.g. inner
Sydney high-SEIFA vs. outer-suburban low-SEIFA) so the demo's payoff
is legible.

### `preset-features.gif` — PRESET ratio computation

Demo (~25 s) showing the FeatureEvaluator standalone:

1. `import` + load a small DataFrame with G37 source columns.
2. Apply `pct_renters` PRESET → renders the spec, computes the ratio.
3. Show that out-of-bounds values produce a WARN log.

This one is more Python-REPL flavoured than CLI — easier to record
with a `>>> ` prompt in the tape than with a shell session.

### `dss-payments-resolution.gif` — release resolution

Demo (~15 s) showing DSS quarterly resolution via CKAN:

1. `census-augment discover --dataset dss_payments`.
2. `python -c "from census_augment.datasets._dss import DssDataSource; print(DssDataSource(release='latest', root='/tmp').resolved_release)"` — shows the auto-resolved quarter.

Niche. Lower priority than the others.

### Notes for whoever renders these

- `tools/demo/render.ps1` (Windows) and `tools/demo/render.sh` (macOS / Linux)
  drive Docker. Each new demo needs a `.tape` file alongside.
- Keep individual GIFs to 20–30 seconds and < 500 KB.
- The cache pre-warm pattern in the existing tape works — use `Hide`
  / `Show` blocks around any download steps.

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

The v1.3 catalog has six PRESETs sourced entirely from `gcp_2021`.
Cross-dataset features (e.g. `pct_age_pension_recipients` =
`DSS.age_pension_recipients` / `ERP.population_65_plus`) are
supported by the format (spec §21) but waiting on:

1. PRESET integration into the pipeline (spec §21.2 / v1.4 plan).
2. ERP exposing age-band breakdowns (currently total population
   only; spec example file describes age-band columns but the
   real ERP DS0003 is total-only).

Once both land, cross-dataset PRESETs become straightforward.

## Other deferred items

- Automated G-NAF S3 fetch is shipped (v1.1). The `gnaf-loader`
  bucket is the canonical default; users who want a different mirror
  can override `data_sources.gnaf_s3_https_endpoint` and
  `data_sources.gnaf_parquet_filter`.
- Messy/fuzzy address test corpus for exercising G-NAF Tiers 2/3
  against real-world ugly inputs. Sourcing AU addresses from a
  *non*-G-NAF source so we test the matcher rather than test G-NAF
  against itself is the design problem.
