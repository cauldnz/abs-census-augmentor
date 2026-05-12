# Backlog

Tracking ideas worth doing but not in the current release. Items here
have been thought-through enough to be actioned by anyone who picks
them up; they're not raw brainstorms.

## Temporal + spatial augmentation (somewhat urgent)

**The gap.** The tool is currently cross-sectional: one run = one
snapshot of every dataset for every row. The input has no notion of
date. A row "Sydney CBD, 2018-03-15" gets the same Census /SEIFA /
ERP / ATO values as "Sydney CBD, 2023-08-22". For any time-series
analysis with location — transaction logs, historical observations,
longitudinal studies — that's a real limitation, and users have to
bucket-by-date externally, run the tool N times with N configs, and
join the results back themselves.

**What's locked to a single snapshot today:**

| Dimension | Current handling |
|---|---|
| ASGS boundary edition | `CensusConfig.year` is `Literal[2021]`. SA2 polygons shift between 2011 / 2016 / 2021 editions; we lock to 2021. |
| Census GCP DataPack | One edition per run (2021). |
| SEIFA | One edition per run. |
| ERP | One yearly release per run (`erp.release`). |
| DSS payments | One quarterly release per run. |
| ATO personal income | One financial year per run. |
| Input rows | No `date_column`. Same lat/lon at different timestamps gets identical augmentation. |
| Boundary-edition migration | Not handled. We don't read ABS's MB-2016→SA2-2021 (etc.) correspondence tables. |

**Use cases that fall through today:**

1. **Longitudinal augmentation** — "give me 2011 / 2016 / 2021 median
   income for this same address, side by side." User has to run the
   tool three times with three configs and join.
2. **Per-row temporal augmentation** — "for each row, use the
   Census closest to its timestamp." User has to bucket rows by year
   and concat results.
3. **Boundary-stable comparisons** — "ERP 2018 vs ERP 2023 at the
   SA2 level" needs ABS's correspondence tables to migrate values
   across SA2 boundary editions; SA2 codes don't line up 1:1 across
   editions.
4. **Within-year granularity** — DSS quarterly snapshots aligned to
   monthly input data. Same shape, different cadence.

G-NAF is a related concern: addresses are created and retired over
time, so a 2010 row's address might not be in the 2024 release. The
"use the release closest to the row's date" rule extends naturally
to geocoding.

### Three levels of scope

Worth distinguishing in design so we can ship the easy bit first
without committing to the whole thing up front.

#### Level 1 — Document the current limitation

A new `docs/temporal-data.md` page that:

- States plainly that the tool is cross-sectional today.
- Shows the user-side workaround (bucket by date → N runs with N
  configs → concat).
- Cross-links from `docs/index.md` and `docs/usage-library.md` so
  anyone landing on the tool with time-series data finds the note
  immediately.

~30 min. Worth doing **now** regardless of whether we tackle Level
2/3, so users aren't surprised.

#### Level 2 — `input.date_column` + per-partition runs

Pipeline change: when `input.date_column` is set, the pipeline
buckets rows by date-resolved-to-release, fans out to
release-specific Pipeline instances under the hood, and concats
results. Each dataset declares its available time slices and a
"closest release for date X" resolver.

Bounded change. Doesn't solve boundary-edition migration (see Level
3), so each bucket's rows still come back with SA2 codes from
*that* bucket's ASGS edition — which means joining a Level-2 run's
output across buckets won't necessarily line up at the SA2 level.
That's OK for "augment historical data with the right snapshot per
row"; it's not OK for "compare values across years using SA2 as
the join key".

Estimated effort: **3-5 days** of focused work. Config schema,
per-dataset resolver protocol, output-schema decisions (does each
row gain a `<dataset>_release` column?), pipeline orchestration.

#### Level 3 — First-class temporal dimension

The real engineering:

- Datasets register their available time slices as a first-class
  concept (`fetcher.available_releases() -> list[Release]`).
- Pipeline picks per-row, per-dataset, based on `input.date_column`.
- ABS correspondence tables (MB→SA2 across editions, SA2→SA2
  across editions) are downloaded and applied so values from
  different ASGS editions can be migrated onto a common reference
  edition. This is the part that makes longitudinal SA2-level
  analysis actually usable.
- G-NAF release selection follows the same rule.
- Output schema gains explicit "as-of date" and "reference ASGS
  edition" columns so downstream consumers can verify what got
  joined.

Estimated effort: **1-2 weeks** of design + implementation. The ABS
correspondence-table side of this is its own real-data fetch
exercise (per the CLAUDE.md rule — fetch a real correspondence
file before writing any parser).

### Priority

Classed as **somewhat urgent**. The tool is otherwise polished for
cross-sectional use, and the lack of temporal handling is the
biggest gap an analyst hitting this tool with real workflow data
would notice today. Level 1 should land soon; Level 2 is the first
substantive step and unblocks the bulk of "augment historical
data" workflows; Level 3 is what makes the tool genuinely
production-grade for longitudinal location analysis.

### Open design questions to settle before Level 2 lands

1. **Per-row release vs per-bucket release?** If two rows in the
   same input land on different release boundaries (e.g. one in
   Q3 2021, one in Q1 2022 for a quarterly dataset), do they get
   different release values per row, or do we bucket aggressively
   and force the boundary at a coarser granularity?
2. **Output schema cost.** Adding `<dataset>_release` columns per
   row is honest but bloats the output. Single global "release map"
   summary in `RunSummary` might be enough for many workflows.
3. **Cache layout.** The current cache pattern is
   `<data_dir>/<dataset>/<release>/...`. That already supports
   multiple releases coexisting; the work is making the pipeline
   actually load multiple at once.
4. **`Pipeline.augment(df)` library API.** Does it grow a
   `date_column=` kwarg matching the CLI, or do we expect library
   users to do their own bucketing? CLI parity is the simpler
   contract.

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
  --datasets` / `--dataset seifa_2021` / `--features` and shows the
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
