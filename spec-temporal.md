# Temporal-Spatial Capability — Design Specification

> **Status:** Approved 2026-05-13 (reviewer feedback applied). Implementation in progress.
>
> **Relationship to main spec:** This document supplements [`spec.md`](spec.md) — it does not replace any section. The main spec describes the cross-sectional baseline (v1.0 → v1.4.2); this document describes the additions that turn the tool into a *temporal-spatial* augmentor. Decisions land in this document first; once implemented they get summarised into `spec.md` §14 (Resolved Decisions).
>
> **Companion artefacts:**
> - [`BACKLOG.md`](BACKLOG.md) "Temporal + spatial augmentation" entry — the higher-level framing this document expands.

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [The core semantic](#2-the-core-semantic)
3. [Use cases](#3-use-cases)
4. [Design principles](#4-design-principles)
5. [Definitions](#5-definitions)
6. [Per-dataset temporal semantics](#6-per-dataset-temporal-semantics)
7. [ABS correspondence tables (deferred)](#7-abs-correspondence-tables-deferred)
8. [Level 1 — Document the limitation](#8-level-1--document-the-limitation)
9. [Level 2 — Temporal mode](#9-level-2--temporal-mode)
10. [Level 3 — Cross-edition aggregation (deferred)](#10-level-3--cross-edition-aggregation-deferred)
11. [Output schema](#11-output-schema)
12. [G-NAF temporal handling](#12-g-naf-temporal-handling)
13. [Caching strategy](#13-caching-strategy)
14. [Error cases](#14-error-cases)
15. [Real-data verification additions](#15-real-data-verification-additions)
16. [Backward compatibility](#16-backward-compatibility)
17. [Out of scope](#17-out-of-scope)
18. [Resolved decisions](#18-resolved-decisions)
19. [Implementation roadmap](#19-implementation-roadmap)
20. [Side-quest: `ato_personal_income` → `abs_personal_income`](#20-side-quest-ato_personal_income--abs_personal_income)

---

## 1. Purpose

Add **per-row temporal selection** to `census-augment` so that each input row picks the dataset snapshot appropriate for *its* timestamp, rather than the entire input receiving one global snapshot determined by config.

The headline workflow: *for each transaction row with a date, give me the demographic snapshot closest to its timestamp — including using the SA2 boundaries that the dataset release was actually compiled against.*

Today this is impossible — the tool is cross-sectional and treats all rows uniformly.

---

## 2. The core semantic

Single most important sentence in this document — everything else follows from it:

> **The SA2 (mesh block) boundaries used for a row's enrichment lookup must match the boundary edition the dataset release was originally compiled against.**

Concretely:

- A row dated 2017-06-01 needs SEIFA 2016 values (closest available release).
- SEIFA 2016 was compiled against **ASGS Edition 2** (the 2016 SA2 boundaries).
- So the spatial lookup for that row's SEIFA enrichment must use the **2016 SA2 boundary file**, not the 2021 one.
- The 2016 spatial lookup gives a 2016 SA2 code — exactly the key SEIFA 2016 was indexed by.

This propagates: a single row can pull values from multiple datasets, each potentially compiled against a different ASGS edition. The pipeline does **one spatial lookup per ASGS edition** referenced by that row's dataset bucket, not just one against the latest edition.

The reference SA2 code reported in output (the row's *canonical identity*) is per the user-configured `temporal.reference_edition` (default: latest known, currently ASGS Edition 3 / 2021). This is a separate lookup from the per-dataset value lookups.

Why this matters:

- A 2016 SA2 boundary may have split into multiple 2021 SA2s in a growth corridor. Using the 2021 boundary to look up SEIFA 2016 values would pick the wrong SA2 — the lat/lon falls in a 2021 SA2 that didn't exist in 2016.
- The reverse — using a 2016 boundary file to look up a 2021-edition release — would give a stale SA2 code that's not a key in the 2021 release.
- The correct answer is: *look up at the boundary edition the release was actually keyed by*.

---

## 3. Use cases

### UC-1 — Per-row temporal augmentation

> "For each transaction row with a date, give me the snapshot closest to its timestamp."

**Today:** Hand-bucket input by year, run N times, concat.

**Target:** Set `input.date_column: transaction_date`. Each row is augmented against the closest-available snapshot per registered dataset, with the spatial lookup happening at the *release's* boundary edition.

**The headline use case for v1 of temporal capability.**

### UC-2 — Cross-edition transaction logs

> "I have 5 years of transactions spanning 2018-2024. Datasets across that span shifted from ASGS 2016 to ASGS 2021. I want each row enriched correctly with the right-edition spatial lookup."

UC-1 plus the additional constraint that **the row's `sa2_code` in output should be in a single consistent edition** (the configured `reference_edition`), so downstream consumers can groupby cleanly.

The pipeline performs:

- Per-bucket per-edition spatial lookups (might be 2 editions: 2016 + 2021).
- Per-dataset value lookups at each dataset's release edition.
- Single canonical `sa2_code` per row in the reference edition.

No value migration via correspondences needed for this use case — each enrichment value is correctly the value at that lat/lon in that dataset's release.

### UC-3 — Longitudinal SA2-level aggregation (deferred)

> "Aggregate ERP 2018 vs ERP 2023 at the SA2 level."

For each row this produces one ERP 2018 value (looked up at ASGS 2016 SA2) and one ERP 2023 value (looked up at ASGS 2021 SA2). For per-row analysis this is fine.

For **groupby canonical SA2 across rows**: SA2 codes split/merged between editions; SUM(ERP 2018 by canonical SA2) needs the correspondence ratios to redistribute values. This is the part Level 3 (deferred) handles.

For point-based enrichment (the tool's primary use case), Level 3 is not needed.

### UC-4 — Within-year granularity

> "I have monthly transaction data; DSS is quarterly; pick the nearest DSS release per row."

Same UC-1 mechanic. The resolution rule (`closest` vs `closest_at_or_before`) is configurable.

---

## 4. Design principles

1. **Boundary correctness.** The §2 invariant — boundary edition for the spatial lookup matches the dataset release's compiled edition. Not row-date-derived; release-derived.

2. **Cross-sectional default unchanged.** A config without `input.date_column` runs cross-sectionally with the exact same code path it does today. Bit-identical output for v1.4.x configs.

3. **Opt-in via a single config field.** Users add `input.date_column: <colname>` to turn temporal mode on. Per-dataset release resolution rules derive from sensible defaults.

4. **Per-row resolution, per-bucket execution.** The pipeline conceptually resolves a release per row; physically it fans out per-bucket (rows sharing the same release-tuple) for cache / fetcher efficiency. Most workloads bucket aggressively to a small number of (release_per_dataset) tuples.

5. **Datasets opt in.** A registered dataset declares its temporal capability via its spec markdown (`temporal:` block with `cadence`, `cover_basis`, `asgs_edition_by_release`). Datasets without it run cross-sectionally even in temporal mode (graceful degradation).

6. **Open horizon, dataset-specific cadence.** No upper / lower bound on supported date range. Each dataset declares the releases it covers; the tool resolves per row against that. SEIFA's earliest is 2001; ATO PIA's is 2010-11; DSS's is 2014. They don't have to agree.

7. **Real Data First (CLAUDE.md).** Every URL, filename, column-name claim in this document is verified against a live ABS / data.gov.au fetch before parsing code lands. Section 15 enumerates the new probes.

8. **Output additions are additive in temporal mode only.** Cross-sectional output is unchanged. New columns (`<dataset>_release`, `sa2_code_source` etc.) appear only when `input.date_column` is set.

---

## 5. Definitions

- **Release.** A specific published instance of a dataset. SEIFA 2021. ERP 2023-24. DSS Payments June 2024.
- **Cadence.** How frequently a dataset publishes new releases. Per-Census, annual, quarterly, continuous.
- **Snapshot.** Equivalent to a release. We use "snapshot" when emphasising the time-frozen aspect.
- **Release window.** The time period a release "covers". ERP 2023-24 covers calendar year 1 Jul 2023 – 30 Jun 2024. ATO PIA 2022-23 covers financial year 2022-23. DSS June 2024 quarter covers calendar Q2 2024.
- **ASGS edition.** A Statistical Geography Standard issue: Edition 1 (2011), Edition 2 (Jul 2016 – Jun 2021), Edition 3 (Jul 2021 – Jun 2026), Edition 4 (from Jul 2026). Each edition has its own SA2 boundaries; codes change between editions.
- **Source ASGS edition (per release).** The edition the dataset release was originally compiled against. Stored per-release because some datasets (DSS, ERP, ATO PIA) transitioned mid-history — e.g. DSS uses ASGS 2016 for Q2-2015 through Q1-2023, ASGS 2021 from Q2-2023 onwards.
- **Reference ASGS edition (configurable).** The edition all output `sa2_code` values are reported in. Defaults to the latest the tool knows about (currently Edition 3 / 2021).
- **Release resolution rule.** How the pipeline picks the right release for a given row date. Two supported: `closest_at_or_before` (default) and `closest`.
- **Bucket.** A group of input rows that share the same per-dataset release tuple.
- **Temporal mode.** A pipeline run with `input.date_column` set. Contrast with **cross-sectional mode** (no `date_column`).

---

## 6. Per-dataset temporal semantics

What each currently-registered dataset publishes, the time windows each release covers, and which ASGS edition each is on. This drives the per-dataset `temporal:` metadata block.

### 6.1 GCP DataPack

- **Cadence:** per-Census (5-yearly). 2011, 2016, 2021 published.
- **Coverage:** Census reference date. 2021 = 10 Aug 2021. 2016 = 9 Aug 2016. 2011 = 9 Aug 2011.
- **Source ASGS edition:** GCP 2011 → Edition 1; GCP 2016 → Edition 2; GCP 2021 → Edition 3.
- **Current state in the tool:** GCP 2021 only. Adding 2016 and 2011 requires per-edition fetcher parameterisation (the existing one is templated on `census.year` already — the rest is URL bookkeeping and ASGS-edition-specific boundary support).

### 6.2 SEIFA

- **Cadence:** per-Census. 2001, 2006, 2011, 2016, 2021 all have IRSAD/IRSD/IEO/IER published.
- **Coverage:** Census reference date.
- **Source ASGS edition:** each SEIFA release is on its contemporaneous edition.
- **Current state:** `seifa` dataset registers **2016 + 2021** releases (Phase F.3 shipped). `seifa_2011` remains a tractable future addition; older (2006, 2001) are on pre-ASGS geographies (CCD / SLA) — out of scope.
- **URLs:** Both 2016 and 2021 use the legacy `abs@.nsf/mf/2033.0.55.001` archive with deterministic URLs verified against the live source. 2011 will need similar URL discovery + real-fetch verify before landing.

### 6.3 ERP (Regional Population, catalogue 3218.0)

- **Cadence:** annual; 30 June snapshot.
- **Coverage:** financial year ending 30 June.
- **Historical depth:** 2001 onwards at SA2.
- **Source ASGS edition: per-release.** 2001-2021 series on Edition 2; 2021-22 onwards on Edition 3. ABS back-restates 1-2 years on the new geography at edition transitions.

### 6.4 DSS Payment Demographic Data

- **Cadence:** quarterly (Mar / Jun / Sep / Dec). Annual prior to mid-2014.
- **Coverage:** quarter-end date. Sep 2025 quarter covers Jul-Sep 2025.
- **Historical depth:** SA2-coded from 2015 onwards. Earlier quarters at state / postcode / LGA only — out of scope.
- **Source ASGS edition:** Q3-2015 through Q1-2023 on Edition 2; Q2-2023 onwards on Edition 3.

### 6.5 ABS Personal Income in Australia (catalogue 6524.0.55.002)

(See §20 — what we currently call `ato_personal_income` is actually ABS Personal Income in Australia, not ATO Taxation Statistics. Rename is part of this work.)

- **Cadence:** annual financial-year.
- **Coverage:** financial year. 2022-23 release covers 1 Jul 2022 – 30 Jun 2023.
- **Historical depth:** 2010-11 through 2022-23 at SA2 on live ABS. Earlier vintages on predecessor catalogue with older geographies — out of scope.
- **Source ASGS edition:** per-release. 2010-11 through ~2018-19 on Edition 2; 2019-20 onwards on Edition 3.

### 6.6 G-NAF

- **Cadence:** quarterly via gnaf-loader (since 2014).
- **Coverage:** the quarter-end snapshot. Addresses created / retired between releases.
- **Source ASGS edition:** the MB_CODE in each G-NAF release is per-release; gnaf-loader names directories `address_principal_census_{year}_boundaries` for each ASGS edition (2016, 2021) and the data source already supports both via the `census_year` parameter.

### 6.7 Worked example — row dated 2019-06-01

A `closest_at_or_before` resolver picks:

| Dataset | Resolved release | Source ASGS edition |
|---|---|---|
| GCP DataPack | 2016 | 2 |
| SEIFA | 2016 | 2 |
| ERP | 2018-19 | 2 |
| DSS | 2019-Q2 | 2 |
| ABS PIA | 2018-19 | 2 |
| G-NAF | latest quarterly ≤ 2019-06-01 | 2 (via census_year=2016) |

All on ASGS Edition 2. **One** spatial lookup against the 2016 boundary file for the whole row. Output `sa2_code` is in reference edition (default 3 / 2021); per-dataset value columns are sourced from the 2016 lookup.

### 6.8 Worked example — row dated 2023-09-01

| Dataset | Resolved release | Source ASGS edition |
|---|---|---|
| GCP DataPack | 2021 | 3 |
| SEIFA | 2021 | 3 |
| ERP | 2022-23 | 3 |
| DSS | 2023-Q3 | 3 |
| ABS PIA | 2022-23 | 3 |
| G-NAF | latest quarterly ≤ 2023-09-01 | 3 |

All on Edition 3. One spatial lookup against the 2021 boundary file. Output `sa2_code` matches the lookup.

### 6.9 Worked example — row dated 2022-06-01 (transition straddle)

| Dataset | Resolved release | Source ASGS edition |
|---|---|---|
| GCP DataPack | 2021 | 3 |
| SEIFA | 2021 | 3 |
| ERP | 2021-22 | 3 |
| DSS | 2022-Q2 | **2** (transition not complete) |
| ABS PIA | 2021-22 | 3 |

**Mixed editions.** Two spatial lookups required for this row's bucket: one against 2016 boundaries (for DSS) and one against 2021 (everything else). The row gets DSS values from the 2016 lookup; the other datasets from the 2021 lookup; the canonical `sa2_code` from the reference-edition lookup.

This is correct behaviour — DSS Q2-2022 was indexed by ASGS 2016 SA2 codes, so we must look up at that edition's boundaries.

---

## 7. ABS correspondence tables (deferred)

ABS publishes per-level correspondence tables (`CG_<from-level>_<from-year>_<to-level>_<to-year>.csv`) for migrating values across ASGS editions. Confirmed by research:

- URL pattern: `https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/correspondences/<filename>`
- CSV format with `RATIO_FROM_TO` population-weighted shares.
- SA2 2016 → 2021 file ~170 KB; MB 2016 → 2021 file ~14 MB.

**Status:** Not needed for the headline use case (point-based per-row enrichment — §2 invariant handles correctness via per-edition spatial lookups). Required only for SA2-level cross-edition aggregation (UC-3) and for value migration when downstream consumers need a single canonical SA2-edition coding for *aggregated* values.

**Deferred** to a future PR. Spec sections that previously described Level 3 mechanics are retained in §10 as a forward-pointing sketch.

---

## 8. Level 1 — Document the limitation

**Status before any code: ship a documentation note explaining the current limitation and forward-pointing to the planned capability.**

- New file: `docs/temporal-data.md` (~120 lines).
- Cross-linked from `docs/index.md` and `docs/usage-library.md`.
- Covers: what cross-sectional means, the workaround pattern (bucket externally + run N times), forward reference to this spec.

Lands first; small standalone PR; not blocked on the rest.

---

## 9. Level 2 — Temporal mode

The headline implementation: per-row temporal selection, per-bucket execution, per-edition spatial lookups, single canonical SA2-edition output.

### 9.1 Config additions

```yaml
input:
  path: transactions.csv
  date_column: transaction_date     # NEW. Optional. ISO 8601 dates.

temporal:                            # NEW. Optional block.
  resolution: closest_at_or_before   # closest_at_or_before (default) | closest
  out_of_range: fail                  # fail (default) | nearest
  reference_edition: 3                # NEW. Default: latest known ASGS edition.
  per_dataset:                        # optional per-dataset resolution overrides
    dss_payments:
      resolution: closest
```

**`input.date_column` semantics:**

- Must reference a column in the input DataFrame.
- Parsed via `pd.to_datetime(..., errors="raise")`. Bad rows fail the run with a clear message.
- Absent → cross-sectional mode (today's behaviour).

**`temporal.resolution` values:**

- `closest_at_or_before` (default) — picks the most recent release whose coverage-window start ≤ row date.
- `closest` — picks the release whose coverage-window midpoint is nearest the row date. Useful for granular quarterly/monthly datasets.

**`temporal.out_of_range`:**

- `fail` (default) — abort if any row's date predates the earliest release of any touched dataset.
- `nearest` — clamp to the earliest available; one WARNING per affected row.

**`temporal.reference_edition`:**

- The ASGS edition all output `sa2_code` values get reported in. Default: latest known (currently 3).
- Pipeline does one lookup per row against the reference edition for the canonical `sa2_code`.
- Per-dataset value lookups happen against each release's source edition (which may differ from reference).

### 9.2 Per-dataset `temporal:` metadata block

Each registered dataset's spec markdown gains a temporal declaration:

```yaml
---
id: erp_by_sa2
namespace: ERP
fetcher: census_augment.datasets._erp:ErpDataSource

temporal:
  cadence: annual
  cover_basis: financial_year_ending      # how to compute window from release id
  release_id_format: "YYYY-YY"            # e.g. "2022-23"
  available_releases:                      # may be a function instead of static
    - "2018-19"
    - "2019-20"
    - "2020-21"
    - "2021-22"
    - "2022-23"
  asgs_edition_by_release:
    "2018-19": 2
    "2019-20": 2
    "2020-21": 2
    "2021-22": 3                          # transition
    "2022-23": 3
---
```

`cover_basis` enumerable values:

- `census_reference_date` — for per-Census datasets (GCP, SEIFA). Window: instant.
- `financial_year_ending` — window: 1 Jul prior year through 30 Jun release year.
- `calendar_year_ending` — window: 1 Jan through 31 Dec.
- `quarter_ending` — window: the 3 calendar months ending at the quarter date.

Datasets without a `temporal:` block fall back to their configured `release` for every row in temporal mode — with a WARNING log on first use.

### 9.3 Algorithm

```
1. Validate input.date_column exists; parse to datetime.
2. For each registered dataset in config.variables:
     Get its temporal metadata + resolution rule.
     For each row, resolve a release using the rule.
3. Bucket rows by the per-dataset release tuple.
4. For each bucket:
     a. Collect the set of ASGS editions referenced by the bucket's datasets.
     b. For each edition E in that set:
          Spatial-lookup all the bucket's rows against E's boundaries.
          Row gains an {edition: sa2_code_E} mapping.
     c. Also spatial-lookup all rows against the reference edition's
        boundaries → canonical `sa2_code`.
     d. For each (dataset, release) in the bucket:
          source_edition = release's edition
          source_sa2 = row's sa2_code in source_edition
          look up the enrichment value at that SA2 in that release
5. Concat per-bucket outputs; restore original row order; emit augmented df.
```

Critically: step 4d **respects the §2 invariant** — values come from the lookup at the right boundary edition for each release. Per-edition spatial lookups within a bucket are how this is mechanically achieved.

### 9.4 Pipeline API surface

`Pipeline.augment(df)` keeps its public signature. Internally it branches:

```python
def augment(self, df, *, address_column=_UNSET, ...):
    if self._config.input.date_column is None:
        return self._augment_cross_sectional(df, ...)
    return self._augment_temporal(df, ...)
```

`AugmentResult` gains optional fields:

```python
@dataclass
class AugmentResult:
    df: pd.DataFrame
    summary: RunSummary
    added_columns: list[str]
    is_fully_enriched: pd.Series
    geocoding_failed: pd.Series
    sa2_unmatched: pd.Series
    # NEW (None in cross-sectional mode):
    releases_used: dict[str, set[str]] | None = None
    out_of_range_rows: pd.Series | None = None
```

`releases_used` is the per-dataset set of releases actually touched: `{"erp_by_sa2": {"2021-22", "2022-23"}, ...}`.

### 9.5 Caching strategy

The existing per-release cache layout already supports multiple releases per dataset. The new requirement is **per-edition boundary caches**:

```
<data_dir>/
├── boundaries/
│   ├── 2016/
│   │   ├── SA2_2016_AUST_SHP_GDA94.zip
│   │   └── SA2_2016_AUST_SHP_GDA94/{shp,dbf,prj,shx,feather}
│   └── 2021/
│       ├── SA2_2021_AUST_SHP_GDA2020.zip
│       └── SA2_2021_AUST_SHP_GDA2020/{shp,dbf,prj,shx,feather}
├── census/
│   ├── 2016/
│   └── 2021/
├── mb/
│   ├── 2016/
│   └── 2021/
└── ...
```

This is a **breaking change** to cache layout. Per reviewer guidance, no auto-migration: users wipe and re-download (this is the only cache requiring re-download; everything else stays put).

### 9.6 What Level 2 alone produces

Per-row enrichment values come from the correct source-edition lookup. Each row's `sa2_code` is in `reference_edition`. Per-dataset `<dataset>_release` columns track which release was used.

Cross-row aggregation by `sa2_code` works for any rows whose datasets are all on the reference edition. For rows whose dataset releases span editions, the per-dataset enrichment values are *point-correct* but **not aggregation-comparable** at SA2 level without Level 3.

The reference-edition `sa2_code` is still a valid groupby key — it just means values from different editions are pooled into the same canonical SA2 bucket. For most analysis this is what users want.

---

## 10. Level 3 — Cross-edition aggregation (deferred)

**Deferred to a future PR.** Documented here as a sketch so the deferred work is captured.

Use case: a user with rows spanning ASGS 2016 and 2021 wants to do `df.groupby("sa2_code").agg(sum=...)` over enrichment values, and the SA2 boundary changes between editions matter.

Mechanism: use ABS correspondence tables (§7) to migrate enrichment values from source edition to reference edition, weighted by population shares. Adds a new dataset registration `asgs_correspondences` with its own fetcher; adds per-field `migration_strategy` metadata to existing dataset specs; adds correspondence-based value rewriting in the per-bucket Pipeline.

The §2 invariant is preserved — values are still computed at the right edition's boundary; migration is a post-processing step that re-keys them to the reference edition for aggregation.

---

## 11. Output schema

### Cross-sectional mode

**Unchanged from spec.md §8.** Bit-identical for v1.4.x configs.

### Temporal mode additions

Always present in temporal mode:

| Column | Type | Description |
|---|---|---|
| `<input.date_column>` | datetime | Echoed from input (no rename) |
| `<dataset_id>_release` | str | Release used for this row, per touched dataset. E.g. `seifa_release`, `gcp_release`, `erp_by_sa2_release`, etc. |
| `sa2_code_edition` | int | The reference ASGS edition the row's canonical `sa2_code` is in. Constant per run. |

Present when at least one dataset's release source edition differs from the reference edition:

| Column | Type | Description |
|---|---|---|
| `<dataset_id>_sa2_code_source` | str | Per-dataset SA2 code in the source edition. Equals the canonical `sa2_code` when source == reference. |

Order: per spec.md §8, new columns slot in *after* the existing reserved seven and *before* enrichment columns. So:

```
<input cols>, geo_lat, geo_lon, geo_source, geo_match_score,
sa2_code, sa2_name, sa2_resolution,
[sa2_code_edition,]
[temporal: <dataset>_release columns,]
[level-2-cross-edition: <dataset>_sa2_code_source columns,]
<enrichment cols>
```

---

## 12. G-NAF temporal handling

G-NAF publishes quarterly via gnaf-loader. The data source already supports `census_year` selection (2016 vs 2021 boundaries embedded in the parquet).

For temporal mode:

- `gnaf.release` becomes a per-row resolved value (closest at-or-before, same rule as datasets).
- The bucket-orchestrator picks the right gnaf-loader directory per row (e.g. `address_principal_census_2016_boundaries` for rows pre-mid-2022, `address_principal_census_2021_boundaries` for later rows).
- DuckDB connection per (release, census_year) bucket; closed when the bucket finishes.

Special cases:

- Address didn't exist yet in the row-date's release window → falls through to address-component / fuzzy / Nominatim tiers, as today.
- Address retired after row-date but in a more recent release → not currently handled; tracked as deferred (see §17).

---

## 13. Caching strategy

Most caches are already per-release. The breaking changes in this work:

| Cache | Before | After | Migration |
|---|---|---|---|
| `boundaries/` | Flat, single edition | `<edition-year>/` subdirs | Wipe + redownload |
| `census/` | Flat, single edition | `<edition-year>/` subdirs | Wipe + redownload |
| `mb/` | Flat, single edition | `<edition-year>/` subdirs | Wipe + redownload |

Per reviewer guidance, no auto-migration. Documented as a breaking change in CHANGELOG; users clear their cache and run `census-augment fetch ...` to repopulate.

Dataset-specific caches (`seifa/`, `erp_by_sa2/`, `dss_payments/`, `abs_personal_income/`) already use per-release filenames and don't need restructuring. They just gain more files when historical releases are registered.

---

## 14. Error cases

### 14.1 Bad date column data

- `input.date_column` references a missing column → loud `ValueError` listing columns.
- Some dates unparseable → fail the run with the first 3 bad row indices.
- All-null column → fail loudly (user opted into temporal; we shouldn't silently regress to cross-sectional).

### 14.2 Out-of-range dates

Per `temporal.out_of_range`:

- `fail` (default) — abort. List of affected row indices in the error.
- `nearest` — clamp to earliest available release; one WARNING per row, with a `RunSummary` counter of clamped rows.

### 14.3 Dataset has no temporal capability

A registered dataset without a `temporal:` block in its spec markdown uses its configured `release` for every row in a temporal-mode run. WARNING logged once per run. `releases_used` reflects the single release.

### 14.4 Bucketing volume

Bucket count = product of per-dataset release counts that the input touches. With 5 datasets and a 7-year input span, worst case is ~7 × 5 = 35 quarters × 7 = 245 buckets — most quickly bounded by the slowest cadence (DSS quarterly). G-NAF resolution defaults to quarterly buckets too.

In practice: with annual cadence for most datasets and `closest_at_or_before` resolution, a 5-year input span produces ~5 unique buckets per dataset, ~25 buckets total. Each bucket is a single Pipeline run — no per-row overhead.

### 14.5 Cross-edition straddle

Per §6.9 — a single row's bucket can reference multiple ASGS editions. Handled by multi-edition spatial lookup within the bucket. No special user-visible error; the per-dataset `<dataset>_sa2_code_source` column reveals when source ≠ reference.

---

## 15. Real-data verification additions

Per CLAUDE.md Real Data First:

- **ASGS 2016 boundary fetch.** New probe in `verify_real_parsers.py`. Verifies the 2016 boundary file's CRS (should be GDA94 / EPSG:4283 vs 2021's GDA2020 / EPSG:7844) and SA2 code schema (should be `SA2_MAIN16` vs 2021's `SA2_CODE21`).
- **Historical SEIFA fetch.** Probe for SEIFA 2016 (and 2011 if added). Verifies the multi-sheet workbook structure matches the parser's assumptions per edition.
- **Per-release ABS PIA fetch.** Probe for ABS PIA 2018-19 (Edition 2 era) vs 2022-23 (Edition 3 era). Verifies column schema is consistent across the edition transition.
- **Per-release ERP fetch.** Same: probe for ERP 2017-18 vs 2022-23.
- **G-NAF quarterly variant.** Probe for `address_principal_census_2016_boundaries` directory existence in the gnaf-loader bucket.

`tools/fetch_real_data.py` gains `--edition` flags for boundary / census downloads (`--edition 2`, `--edition 3`, default 3).

---

## 16. Backward compatibility

**Strong commitment:** zero behavioural change for v1.4.x cross-sectional configs.

- A config without `input.date_column` runs cross-sectionally with the exact same code path.
- Output schema for cross-sectional runs is unchanged.
- Library `Pipeline.augment(df)` signature unchanged; new `AugmentResult` fields are `None` in cross-sectional mode.

**Breaking changes documented:**

- Boundary / census / mb cache layouts move to `<edition>/` subdirs. Users wipe cache and re-fetch.
- `ato_personal_income` dataset renames to `abs_personal_income`; `ATO` namespace renames to `ABS_PIA`. Users with `ATO.foo` variable references update to `ABS_PIA.foo`. See §20.

---

## 17. Out of scope

- **Multi-snapshot side-by-side output.** "Give me 2011 + 2016 + 2021 income for the same row in three columns" is a different operation (pivot, not asof-join). Out of scope.
- **G-NAF retirement-aware lookup.** "Address X existed in 2018 but was retired in 2022; row is dated 2020; use the latest release that still had X." Tracked as a deferred refinement; today addresses missing from the resolved release fall through to fuzzy / Nominatim.
- **Custom user correspondence tables.** A user with school-catchment → SA2 mapping could in principle reuse the Level 3 infrastructure. Future work.
- **Level 3 cross-edition value migration** (correspondence-based). Deferred — see §7, §10.
- **Sub-cadence interpolation.** ERP is annual; we don't interpolate to make it monthly.
- **ASGS Edition 4 (2026).** ABS Edition 4 ships from Jul 2026. We'll register support when actual artefacts ship.
- **Historical pre-ASGS geographies.** SEIFA 2001/2006 use CCD/SLA; out of scope.

---

## 18. Resolved decisions

All open questions from the v1 draft are resolved (2026-05-13):

### Q1 — Default resolution rule: `closest_at_or_before`

Both `closest_at_or_before` and `closest` supported. `closest_at_or_before` is default — causally correct for as-of analysis.

### Q2 — Out-of-range default: `fail`

Both `fail` and `nearest` supported. `fail` is default — loud is better than silently using a wildly inappropriate release.

### Q3 — Historical depth: open horizon

No upper / lower bound. Each dataset declares its own time range via `available_releases` in its spec metadata. Per-dataset cadence (annual / quarterly / per-Census) flows naturally from the metadata.

The §2 invariant — boundary edition matches the release's compiled edition — guarantees that historical datasets get spatially looked up correctly regardless of when they were published.

Initial historical scope (in this work):

- **SEIFA 2011** (Edition 1, shipped Phase F.6) — `.xls` via python-calamine, same parser as 2016.
- **SEIFA 2016** (Edition 2, shipped Phase F.3)
- **GCP 2016** (Edition 2, shipped Phase F.4)
- **ERP** back to 2016 (Edition 2 series)
- **DSS** back to 2015 (the earliest SA2-coded quarter)
- **ABS PIA** back to 2010-11
- **ASGS Edition 1 boundary** (shipped alongside Phase F.6 — used by SEIFA 2011 temporal-mode lookups).

Pre-2011 SEIFA releases (2001, 2006) remain out of scope — they use CCD/SLA pre-ASGS geography.

**GCP 2011 / BCP 2011** is out of scope at the data-source layer: ABS gates the 2011 DataPack behind a login at `https://www.censusdata.abs.gov.au/datapacks` with no public direct URL. Verified 2026-05-29 by probing multiple URL patterns + the live datapacks home page. A future "user-supplied ZIP" fallback on `DataPacksDataSource` could unblock 2011 GCP for power users who download manually; tracked in BACKLOG.

### Q4 — Migration of averages/medians: preserve source value

**Resolved (implementer's call):** When Level 3 (cross-edition migration) is implemented, average / median / rank-type values will be **preserved at the source-edition SA2** with a WARNING. Mathematical reason: the population-weighted *average of averages* is not an average — we'd need the underlying distribution to do better. Documenting this is honest; doing it anyway and producing subtly-wrong numbers is not.

For Level 2 alone (this PR), the question doesn't arise — values come from the source-edition lookup and aren't migrated.

### Q5 — Level 2-only output with mixed editions: emit with per-dataset source-SA2 column

**Resolved (implementer's call):** Emit the row with both the canonical reference-edition `sa2_code` AND a per-dataset `<dataset>_sa2_code_source` column when source ≠ reference. WARNING on the run summary listing affected datasets / row counts.

Reasoning: the row's enrichment values *are* point-correct (looked up at the right boundary edition). The mixed-edition concern is only about aggregation, and surfacing the source SA2 lets downstream consumers do their own per-dataset groupby if they need to.

Refusing-to-run would be more restrictive than the data correctness requires.

### Q6 — `ato_personal_income` rename: yes, breaking

**Resolved:** rename to `abs_personal_income`; namespace `ATO` → `ABS_PIA`. Breaking change. Documented in CHANGELOG.

### Q7 — Cache directory restructure: no auto-migration

**Resolved:** wipe cache; users re-fetch. Documented in CHANGELOG.

---

## 19. Implementation roadmap

| Phase | Scope | Lands as |
|---|---|---|
| **A** | Refined spec (this document) + `docs/temporal-data.md` (Level 1) | One PR |
| **B** | Per-dataset `temporal:` metadata blocks in all 4 spec markdown files; Pydantic schema for the metadata; no pipeline behaviour change yet | One PR |
| **C** | `ato_personal_income` → `abs_personal_income` rename (breaking) | One PR |
| **D** | Boundary / census / mb cache restructure to per-edition subdirs; `BoundariesDataSource` parameterised on edition | One PR |
| **E** | `input.date_column` + `temporal` config block + Pydantic validation; pipeline branches in `augment()`; bucketing orchestrator; per-edition spatial lookups; output schema additions; `AugmentResult` additions | One PR (this is the big one — ~3 days of work) |
| **F** | Historical dataset registrations: SEIFA 2016, GCP 2016, ERP 2016+, DSS 2015+, ABS PIA 2010+. Each lands with real-data verification | Multiple PRs (one per dataset) |
| **G** | G-NAF release-per-bucket; quarterly resolution | One PR |
| **H** | Examples + docs polish: `examples/temporal_augmentation.py`, `examples/historical_lookup.py`, updates to `docs/usage-*.md` | One PR |

Level 3 (cross-edition value migration via correspondences) lives in its own follow-up roadmap once Level 2 is in users' hands.

---

## 20. Side-quest: `ato_personal_income` → `abs_personal_income`

What we call `ato_personal_income` is actually **ABS catalogue 6524.0.55.002 "Personal Income in Australia"** — a LEED-derived ABS product. Not ATO Taxation Statistics. The misnomer dates from v1.3 when the dataset was registered.

Per reviewer guidance, breaking change is acceptable:

- Dataset id: `ato_personal_income` → `abs_personal_income`
- Namespace: `ATO` → `ABS_PIA`
- Module: `src/census_augment/datasets/_ato.py` → `src/census_augment/datasets/_abs_pia.py`
- Spec file: `datasets/ato_personal_income.md` → `datasets/abs_personal_income.md`
- Cache dir: `<data_dir>/ato_personal_income/` → `<data_dir>/abs_personal_income/`
- All test references updated.

Configs with `ATO.foo` variable references must update to `ABS_PIA.foo`. CHANGELOG entry explicit. No alias / deprecation period.

---

## End of spec

Implementation begins from Phase A.
