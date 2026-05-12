# Temporal-Spatial Capability — Design Specification

> **Status:** Draft for review (2026-05-12). Not yet implemented.
>
> **Relationship to main spec:** This document supplements [`spec.md`](spec.md) — it does not replace any section. The main spec describes the cross-sectional baseline (v1.0 → v1.4.2); this document describes the additions that turn the tool into a *temporal-spatial* augmentor. Once approved and partially implemented (Level 1 / Level 2), the relevant decisions will be merged into the main spec's §14 (Resolved Decisions) log.
>
> **Companion artefacts:**
> - [`BACKLOG.md`](BACKLOG.md) "Temporal + spatial augmentation" entry — the higher-level framing this document expands.
> - PR #53 — the original backlog entry's PR. This document supersedes that entry's design sketch.

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [The gap today](#2-the-gap-today)
3. [Use cases](#3-use-cases)
4. [Design principles](#4-design-principles)
5. [Definitions](#5-definitions)
6. [Per-dataset temporal semantics](#6-per-dataset-temporal-semantics)
7. [ABS correspondence tables (boundary edition migration)](#7-abs-correspondence-tables-boundary-edition-migration)
8. [Level 1 — Document the limitation](#8-level-1--document-the-limitation)
9. [Level 2 — `input.date_column` + per-bucket runs](#9-level-2--inputdate_column--per-bucket-runs)
10. [Level 3 — Correspondence-based boundary migration](#10-level-3--correspondence-based-boundary-migration)
11. [Output schema additions](#11-output-schema-additions)
12. [G-NAF temporal handling](#12-g-naf-temporal-handling)
13. [Caching strategy](#13-caching-strategy)
14. [Error cases and edge conditions](#14-error-cases-and-edge-conditions)
15. [Real-data verification additions](#15-real-data-verification-additions)
16. [Backward compatibility](#16-backward-compatibility)
17. [Out of scope](#17-out-of-scope)
18. [Open questions for review](#18-open-questions-for-review)
19. [Implementation roadmap](#19-implementation-roadmap)
20. [Side-quest: `ato_personal_income` is misnamed](#20-side-quest-ato_personal_income-is-misnamed)

---

## 1. Purpose

Add **per-row temporal selection** to `census-augment` so that each input row picks the dataset snapshot appropriate for *its* timestamp, rather than the entire input receiving one global snapshot determined by config.

Three motivating workflows:

- **Longitudinal analysis** — "How did SA2-level income change between 2011 / 2016 / 2021 at this location?"
- **Historical augmentation** — "I have 5 years of transaction logs; augment each row with the demographic snapshot closest to its date."
- **Boundary-stable comparisons** — "ERP 2018 vs ERP 2023, joined on SA2." SA2 codes don't match 1:1 across ASGS editions; correspondence tables fix that.

Today none of these workflows are supported in-tool. Users have to bucket data externally, run the pipeline N times with N configs, and join the results — a meaningful friction point flagged as "somewhat urgent" by the project owner.

---

## 2. The gap today

The tool is cross-sectional: **one run = one snapshot of every dataset for every row**. Spec.md §3's architecture diagram has no notion of time; spec.md §8's output schema doesn't track which release a value came from.

| Dimension | Locked to |
|---|---|
| ASGS boundary edition | `CensusConfig.year: Literal[2021]`, `CensusConfig.asgs_edition: Literal[3]` |
| Census GCP DataPack | 2021 |
| SEIFA | One edition per run (`seifa.release: 2021`) |
| ERP | One yearly release per run (`erp.release: "latest"` or `"2024"`) |
| DSS Payments | One quarterly release per run |
| ABS Personal Income | One financial-year release per run |
| Input rows | No date column. Same lat/lon at different timestamps → identical augmentation |
| Boundary-edition migration | Not handled. We don't read ABS's correspondence tables |
| G-NAF release | One per run (`gnaf.release: "latest"` or `"202506"`) |

The G-NAF address-lifecycle problem is a related concern: a 2010 row's address might not exist in the 2025 G-NAF release. Today we'd silently fail to geocode it; with temporal capability we should use the release closest to the row's date.

---

## 3. Use cases

### UC-1 — Longitudinal augmentation (same row, multiple snapshots side-by-side)

> "Show me median household income for this address in 2011, 2016, and 2021."

**Today:** Three Pipeline runs with three different `census.year` configs; manual join.

**Target:** One run, output columns `sa2_median_income_2011`, `sa2_median_income_2016`, `sa2_median_income_2021`.

**Out of scope for Level 2.** This needs explicit multi-snapshot output, not per-row temporal selection. See §17.

### UC-2 — Per-row temporal augmentation

> "For each transaction row with a date, give me the snapshot closest to its timestamp."

**Today:** Hand-bucket input by year, run N times, concat.

**Target:** Set `input.date_column: transaction_date`; each row is augmented against the closest-available snapshot per registered dataset. The output gains `<dataset>_release` columns naming which snapshot was used.

**The headline use case for Level 2.**

### UC-3 — Boundary-stable comparisons

> "Compare ERP 2018 vs ERP 2023 at the SA2 level."

**Today:** Not supported in a way that produces correct values. ERP 2018 uses ASGS 2016 SA2 codes; ERP 2023 uses ASGS 2021 codes. They don't align 1:1. ~8% of SA2s changed between editions.

**Target:** Configure a reference ASGS edition. The pipeline reads the ABS correspondence tables and migrates each row's enrichment values to the reference edition's SA2 code using population-weighted ratios. Output is on a single consistent geography even when the source rows spanned editions.

**The headline use case for Level 3.**

### UC-4 — Within-year granularity

> "I have monthly transaction data. The DSS dataset is quarterly. Pick the nearest DSS release per row."

Same Level 2 mechanic — DSS has its own per-release cadence and the resolver respects it.

---

## 4. Design principles

1. **Cross-sectional default unchanged.** When `input.date_column` is unset (today's behaviour), every code path produces bit-identical output to v1.4.x. No silent migration of existing configs.

2. **Opt-in via a single config field.** Users add `input.date_column: <colname>` to turn temporal mode on. Per-dataset release resolution rules are derived from sensible defaults that can be overridden if needed.

3. **Levels ship independently.** Level 1 (docs) lands first. Level 2 (`date_column` + per-bucket fan-out) lands second. Level 3 (correspondence-based boundary migration) lands later — and crucially, doesn't block Level 2 from being useful. Level 2 users with rows that span ASGS editions get a warning + per-row code-edition tagging, not a silent miscompare.

4. **Per-row resolution, per-bucket execution.** The pipeline conceptually resolves a release per row, but actually fans out *per bucket* (rows with the same release-tuple) for fetcher / cache efficiency. With most workloads bucketing aggressively to a few releases, this avoids loading every release for every row.

5. **Datasets opt in.** A registered dataset declares its temporal capability via its spec markdown. Datasets that haven't declared it fall back to a single "latest" release for the whole run (same as today). User-added third-party datasets keep working without modification.

6. **Real Data First (CLAUDE.md).** Every URL / filename / column-name claim in this document is verified against a live ABS / data.gov.au fetch before any parsing code lands. The Section 15 verification additions enforce this.

7. **Output schema additions are additive.** New columns (`<dataset>_release`, etc.) appear only in temporal mode. Cross-sectional output is unchanged. This preserves spec.md §8 as the single source of truth for cross-sectional users.

---

## 5. Definitions

The temporal vocabulary used throughout this document. Some terms are ABS-canonical; others are project-specific.

- **Release.** A specific published instance of a dataset. SEIFA 2021. ERP 2023-24. DSS Payments June 2024. ATO PIA 2022-23. The dataset module's existing `resolved_release` attribute names it.
- **Cadence.** How frequently a dataset publishes new releases. Per-Census (SEIFA, GCP), annual (ERP, ATO PIA), quarterly (DSS), continuous (G-NAF quarterly snapshots).
- **Snapshot.** Equivalent to a release. We use "snapshot" when emphasising the time-frozen aspect ("the snapshot for this row's date") and "release" when emphasising the publication aspect ("the 2022-23 release").
- **Release window.** The time period a release "covers" or applies to. ERP 2023 covers calendar year 2023. ATO PIA 2022-23 covers financial year 2022-23. DSS June 2024 quarter covers Apr-Jun 2024.
- **ASGS edition.** A Statistical Geography Standard issue: Edition 3 (Jul 2021 – Jun 2026), Edition 4 (from Jul 2026). Each edition has its own SA2 boundaries; codes change between editions.
- **Correspondence (or correspondence table).** ABS's canonical term for the table mapping one ASGS edition's regions to another's. Confirmed terminology — ABS does *not* use "concordance" despite some third-party docs doing so. CSV format with `RATIO_FROM_TO` weighted columns.
- **Reference edition (or reference frame).** The user-chosen ASGS edition that all output SA2 codes get migrated to. Configurable; default = the latest edition the tool knows about (currently 2021).
- **Release resolution rule.** How the pipeline picks the right release for a given input-row date. Default: `closest_at_or_before` (the most recent release whose window starts no later than the row's date).
- **Bucket.** A group of input rows that share the same release-per-dataset tuple. The pipeline fans out per bucket for fetcher / cache efficiency.
- **Temporal mode.** A pipeline run with `input.date_column` set. Contrast with **cross-sectional mode** (no `date_column`; today's behaviour).

---

## 6. Per-dataset temporal semantics

What each currently-registered dataset publishes, the time windows each release covers, and which ASGS edition each is on. Source-of-truth for the per-dataset metadata Level 2 will encode.

### 6.1 GCP DataPack (2021)

- **Cadence:** per-Census (5-yearly). 2011, 2016, 2021 published; 2026 due roughly Jun 2027.
- **Coverage:** the Census reference date. 2021 = 10 Aug 2021. 2016 = 9 Aug 2016. 2011 = 9 Aug 2011.
- **Earlier editions:** exist; on older geographies (Indigenous structure differs across editions; CCD / SLA pre-ASGS). Out of scope for v1.
- **ASGS edition:** GCP 2011 / 2016 / 2021 each coded against contemporaneous ASGS edition.

### 6.2 SEIFA

- **Cadence:** per-Census. **2001 / 2006 / 2011 / 2016 / 2021** all have IRSAD / IRSD / IEO / IER published.
- **Coverage:** the Census reference date.
- **ASGS edition:** each SEIFA release is on its contemporaneous ASGS edition (2021 on Edition 3, 2016 on Edition 2, etc.).
- **URLs:** 2021 has a predictable URL under `/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/2021/`. 2011 and 2016 live in the legacy `abs@.nsf/mf/2033.0.55.001` archive — URLs less predictable, will need a real-fetch + URL-lock-down per edition. **2001 / 2006 not recommended for Level 2 scope** (CCD-coded; geography-shift work bigger than the dataset's value).
- **Suggested registry split:** `seifa_2021` (current) + `seifa_2016` + `seifa_2011` as separate dataset specs sharing a common base. SEIFA-the-concept is what the user thinks about; the dataset-id is the implementation.

### 6.3 ERP (Regional Population, catalogue 3218.0)

- **Cadence:** annual; 30 June snapshot.
- **Coverage:** calendar year ending 30 June. ERP 2023-24 = 30 Jun 2024 snapshot, covers period 1 Jul 2023 – 30 Jun 2024.
- **Historical depth:** 2001 onwards at SA2 level. 12+ years on the live ABS site; older years via AURIN mirror.
- **ASGS edition:** **per-release.** The 2001–2021 series is published on ASGS 2016 boundaries. Releases from ~2021–22 onwards are on ASGS 2021. ABS back-restates one or two years on the new geography when transitioning. Encode as a per-release attribute, not a single global one.

### 6.4 DSS Payment Demographic Data

- **Cadence:** quarterly (Mar / Jun / Sep / Dec). Annual prior to mid-2014.
- **Coverage:** quarter-end date. Sep 2025 quarter covers calendar Q3 2025.
- **Historical depth:** SA2-coded data from **2015 onwards**. Earlier quarters have state / electorate / postcode / LGA only — not registerable at SA2 without losing the granularity.
- **ASGS edition:** SA2 2016 from 2015-Q3 through 2023-Q1. **SA2 2021 from 2023-Q2 onwards.** Real boundary transition mid-dataset — Level 3 territory.
- **URLs:** CKAN package `dss-payment-demographic-data` at data.gov.au; resources have UUIDs, resolve via `package_show`.

### 6.5 ABS Personal Income in Australia (catalogue 6524.0.55.002)

(See §20 — the `ato_personal_income` dataset-id is a misnomer.)

- **Cadence:** annual, by financial year (1 Jul – 30 Jun).
- **Coverage:** the financial year. 2022-23 release covers 1 Jul 2022 – 30 Jun 2023.
- **Historical depth:** 2010-11 through 2022-23 at SA2 on the live ABS site. Earlier via the predecessor catalogue 6524.0 ("Estimates of Personal Income for Small Areas"), 2001-02 onwards, on older geographies.
- **ASGS edition:** **per-release.** Latest release (2022-23) explicitly on SA2 2021. The 2010-11 to ~2015-16 vintage was on SA2 2016; transition to SA2 2021 around 2019-20 or 2020-21.

### 6.6 Cross-dataset temporal alignment table

For a row dated 2018-06-15, a `closest_at_or_before` resolver picks:

| Dataset | Resolved release | ASGS edition |
|---|---|---|
| GCP DataPack | 2016 | 2016 |
| SEIFA | 2016 | 2016 |
| ERP | 2017-18 (snapshot 30 Jun 2018) | 2016 |
| DSS | 2018-Q2 (June 2018 quarter) | 2016 |
| ABS PIA | 2017-18 | 2016 |
| G-NAF | ~202206 (the quarterly nearest before 2018-06-15) | 2016 |

For a row dated 2024-09-01:

| Dataset | Resolved release | ASGS edition |
|---|---|---|
| GCP DataPack | 2021 | 2021 |
| SEIFA | 2021 | 2021 |
| ERP | 2023-24 | 2021 |
| DSS | 2024-Q3 (Sep 2024 quarter) | 2021 |
| ABS PIA | 2022-23 | 2021 |
| G-NAF | latest available quarterly < 2024-09-01 | 2021 |

So a row dated 2018 lands entirely on ASGS 2016 codes; a row dated 2024 entirely on 2021. A row dated 2021-08-01 straddles the transition (some datasets on 2016, others on 2021) — this is the canonical Level 3 use case.

---

## 7. ABS correspondence tables (boundary edition migration)

ABS publishes per-level correspondence files on the ASGS Edition page.

### 7.1 Available correspondences (ASGS 2016 → 2021)

| Level | Filename | Size |
|---|---|---|
| Mesh Block | `CG_MB_2016_MB_2021.csv` | ~14 MB |
| SA1 | `CG_SA1_2016_SA1_2021.csv` | ~2.4 MB |
| SA2 | `CG_SA2_2016_SA2_2021.csv` | ~170 KB |
| SA3 | `CG_SA3_2016_SA3_2021.csv` | ~21 KB |
| SA4 | `CG_SA4_2016_SA4_2021.csv` | ~7 KB |
| GCCSA | `CG_GCCSA_2016_GCCSA_2021.csv` | ~3 KB |

Plus several non-ABS-structure correspondences (LGA, SED, CED, POA) that we do not need for v1.

URL base: `https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/correspondences/<FILENAME>`

Filename convention: `CG_<FROM_LEVEL>_<FROM_YEAR>_<TO_LEVEL>_<TO_YEAR>.csv`.

### 7.2 Schema

Confirmed columns for `CG_SA2_2016_SA2_2021.csv` (verify against live file before parser lands):

- `SA2_MAINCODE_2016` (9-digit string)
- `SA2_NAME_2016`
- `SA2_MAINCODE_2021`
- `SA2_NAME_2021`
- `RATIO_FROM_TO` (float in [0, 1])

For each FROM region, the rows naming it sum (in `RATIO_FROM_TO`) to approximately 1. This represents the share of the FROM region's *population* that lands in each TO region.

### 7.3 Edge cases

- **One-to-one passthrough:** ~92% of SA2 2016 codes appear in exactly one row with `RATIO_FROM_TO ≈ 1` and a different `SA2_MAINCODE_2021`. Most boundaries don't change.
- **Splits:** one SA2 2016 → N SA2 2021 codes. Each row's ratio < 1; sum ≈ 1.
- **Merges:** N SA2 2016 codes → one SA2 2021 code. Each row's ratio ≈ 1 against the same destination.
- **Brand-new SA2s:** SA2 2021 codes with no 2016 ancestor. These appear as the TO side of one or more rows where the FROM side is a partial donor — i.e. they're not "new" in the sense of being unjoinable, but in the sense of being a destination only.
- **Retired SA2s:** SA2 2016 codes that don't appear as a FROM at all (very rare; mostly happens at higher levels like LGA).
- **Encoding of "no concordance":** when one side is unmapped, ABS uses a placeholder code. Convention TBD — **verify against the actual file before parser lands** (CLAUDE.md Real Data First).

### 7.4 Weighting basis

Population-weighted. ABS docs: "As most ABS data relates to population, standard correspondences have a weighting calculated on the location of the population." No area-weighted alternative is published. For value migration we just multiply: `migrated_value = sum over donor rows of (donor_value * RATIO_FROM_TO)`.

This is correct for **count / sum / average** statistics. For **rank / decile / percentile** statistics (SEIFA scores, AusRank, etc.) the population-weighted migration produces meaningful but not strictly correct values — a percentile is a function of the whole population's distribution, not of individual SA2 values. Level 3 should warn about this; the alternative ("just don't migrate; preserve the source edition's code") is exposed via the reference-edition config.

### 7.5 SA2 2021 → 2026 (ASGS Edition 4)

ASGS Edition 4 lands progressively from Jul 2026 — Main Structure (including SA2) in Jul 2026, ready for the 2026 Census. Correspondence files will follow the same `CG_*` filename convention at the Edition 4 equivalent URL. **Not actionable yet** — register at the Edition 4 page when ABS publishes it; the only architectural change needed is parameterising the FROM/TO years in the URL/filename.

---

## 8. Level 1 — Document the limitation

**Scope:** Add `docs/temporal-data.md` (~150 lines) covering:

- The current limitation in plain English ("the tool is cross-sectional").
- The current workaround pattern (bucket by date → N runs with N configs → concat).
- Forward reference to this spec for the planned escape hatches.

**Effort:** ~30 minutes. **Worth doing now** regardless of whether Level 2 / 3 land — so users hitting the tool with time-series data don't file the gap as a bug.

**Deliverable:** one markdown file, cross-linked from `docs/index.md` and `docs/usage-library.md`.

**No code changes.**

---

## 9. Level 2 — `input.date_column` + per-bucket runs

The headline temporal capability. Per-row release selection; per-bucket execution; output gains release-tracking columns. **No** boundary migration — that's Level 3.

### 9.1 Config schema additions

```yaml
input:
  path: transactions.csv
  date_column: transaction_date     # NEW. Optional. ISO 8601 dates.

temporal:                            # NEW. Optional block.
  resolution: closest_at_or_before   # closest_at_or_before | closest | strict
  out_of_range: fail                 # fail | nearest | drop
  per_dataset:                        # optional per-dataset overrides
    dss_payments:
      resolution: closest             # DSS is quarterly; default to nearest
```

**`input.date_column` semantics:**

- Must reference a column in the input DataFrame.
- Parsed via `pd.to_datetime(..., errors="raise")`. Bad rows fail the run with a clear "row N has invalid date" error.
- Timezone-naive dates assumed UTC. Timezone-aware dates honoured.
- Absent → cross-sectional mode (today's behaviour).

**`temporal.resolution` values:**

- `closest_at_or_before` (default) — picks the most recent release whose coverage window starts ≤ the row's date. Sensible for cause-and-effect analysis ("what was the SA2 demographic at the time of this transaction?").
- `closest` — picks the release whose midpoint is nearest to the row's date. Sensible for granular datasets like DSS where Q1 2024 covers Jan-Mar but the row might be at Apr 1 — `closest` picks Q1, not Q2.
- `strict` — fail the row if no release window exactly contains the date.

**`temporal.out_of_range` values:**

- `fail` (default) — abort the run on the first row whose date predates the earliest release of any dataset that gets touched.
- `nearest` — fall back to the earliest release, with a WARNING log per affected row.
- `drop` — silently drop the row from output, with a count in the summary.

### 9.2 Algorithm

```
1. Validate input.date_column exists; parse to datetime.
2. For each registered dataset in config.variables:
     determine the set of releases needed across all rows.
     (most workloads bucket to 1-3 releases per dataset)
3. Bucket rows by the *tuple* of per-dataset releases.
   Most rows fall into a small number of buckets even
   for wide date ranges.
4. For each bucket:
     a. Build a per-bucket Pipeline with the bucket's release tuple.
     b. Run augment() on the bucket's rows.
5. Concat the per-bucket outputs.
6. Restore the original row order (the input's index).
7. Append per-dataset release columns (see §11).
```

**Example.** Input: 1000 rows spanning Jan 2018 – Dec 2024. Variables: `seifa_irsd`, `erp_population`, `gcp_median_age`.

- SEIFA: rows pre-2021 use 2016; rows post-2021 use 2021. 2 buckets.
- GCP: same split. 2 buckets.
- ERP: yearly releases. Up to 7 buckets (2018, 2019, …, 2024).

Bucket tuple per row = `(seifa_release, erp_release, gcp_release)`. Total distinct buckets ≤ 7. Each bucket gets one Pipeline instance, one set of fetcher calls, one cache-load round-trip.

Critically: **per-bucket Pipeline reuses the existing `Pipeline.augment(df)` machinery unchanged.** Bucketing is a higher-level orchestrator. This keeps the core pipeline simple and avoids invasive changes to spec.md §3's architecture.

### 9.3 Per-dataset capability declaration

Each registered dataset's spec markdown gains a `temporal:` block (default = absent → cross-sectional, "latest" release):

```yaml
---
id: erp_by_sa2
namespace: ERP
status: active
fetcher: census_augment.datasets._erp:ErpDataSource

# NEW
temporal:
  cadence: annual
  cover_basis: financial_year_end   # how to compute coverage window
  asgs_edition_by_release:
    "2001": 2     # ASGS Edition 2 (older era; pre-2016)
    "2016": 2     # Edition 2
    "2021": 3     # Edition 3
    "2022-23": 3
    "2023-24": 3
```

- `cadence` is purely informational (drives default `resolution`).
- `cover_basis` tells the pipeline how to compute each release's coverage window from its release identifier.
- `asgs_edition_by_release` tells Level 3 which edition a value comes from. Optional for Level 2; **required** for Level 3.

Datasets without a `temporal:` block stay cross-sectional. A temporal-mode run touching such a dataset uses its single configured release for every row — same as today.

### 9.4 New library API

`Pipeline.augment(df)` already exists. **No new public method** is needed — the existing one delegates to the new bucketing orchestrator internally:

```python
def augment(self, df, *, ...):
    if self._config.input.date_column is None:
        return self._augment_cross_sectional(df, ...)
    return self._augment_temporal(df, ...)
```

The `AugmentResult` dataclass gains optional fields:

```python
@dataclass
class AugmentResult:
    df: pd.DataFrame
    summary: RunSummary
    added_columns: list[str]
    is_fully_enriched: pd.Series
    geocoding_failed: pd.Series
    sa2_unmatched: pd.Series
    # NEW
    releases_used: dict[str, set[str]] | None = None  # {"erp_by_sa2": {"2022-23", "2023-24"}, ...}
    out_of_range_rows: pd.Series | None = None        # bool per row, only set in temporal mode
```

### 9.5 New CLI behaviour

`census-augment run --config config.yaml` runs in temporal mode automatically when `input.date_column` is set. The human-readable summary gains a "Per-dataset releases used" section.

A new `census-augment validate --config config.yaml --temporal` check exercises the temporal config without doing a real run (parses dates, computes bucket count, summarises).

### 9.6 Caching strategy at Level 2

The existing per-release cache layout (`<data_dir>/<dataset_id>/<release>/...`) already supports multiple releases coexisting — the v1.3 dataset modules just don't load multiple at once. Level 2 changes the *loading* logic, not the *layout*.

Sidecar caches (`<metadata>.parsed.pkl`, `<shp>.feather` from PR #49) work unchanged — each release has its own.

### 9.7 What Level 2 doesn't solve

Crucially: a Level 2 run whose rows span ASGS editions produces output where **`sa2_code` for some rows is on 2016 codes and for others on 2021 codes**. These codes don't join 1:1. Downstream consumers doing `df.groupby("sa2_code")` get nonsense for the cross-edition slice.

The Level 2 output schema flags this clearly:

- New column `sa2_code_edition` per row (`"2016"` or `"2021"`).
- WARNING log if any temporal run produces rows in multiple editions.
- The summary names how many rows are in each edition.

Users who need cross-edition joins must opt into Level 3 (or pin all their work to a single edition by filtering the input).

---

## 10. Level 3 — Correspondence-based boundary migration

Level 2 picks the right snapshot per row. Level 3 puts every row's SA2 code into a single chosen **reference edition**, migrating values via correspondence-table ratios.

### 10.1 Config schema additions (on top of Level 2)

```yaml
temporal:
  reference_edition: 2021            # NEW. ASGS edition all SA2 codes get migrated to.
  migration_warn_on_rank: true       # NEW. WARN when migrating rank-type values (see §7.4).
```

`reference_edition` defaults to the latest known edition (2021 today; 2026 when Edition 4 lands).

### 10.2 New dataset: `asgs_correspondences`

ABS correspondence tables are registered as a first-class dataset in the registry (spec.md §20), with its own fetcher pulling from the URL pattern in §7.1.

- **Dataset id:** `asgs_correspondences`
- **Namespace:** `ASGS` (but not directly exposed in `variables:`; loaded only by the migration orchestrator)
- **Fetcher:** `CorrespondencesDataSource` — pulls the per-level CSV per edition pair (e.g. `CG_SA2_2016_SA2_2021.csv`) into a parquet sidecar
- **Schema:** {from_code, to_code, ratio} indexed by from_code

### 10.3 Migration algorithm

```
For each per-bucket Pipeline result:
    if bucket's edition == reference_edition:
        skip — values already on reference codes.
    else:
        for each enrichment column:
            join bucket_df.sa2_code → correspondence (FROM)
            multiply value by RATIO_FROM_TO
            group_by(TO code), sum
        replace bucket_df.sa2_code with TO code
        annotate row with sa2_resolution_method = "correspondence_migrated"
```

For one-to-one passthrough rows (RATIO_FROM_TO = 1), this is a no-op except for the code renaming.

For splits / merges, this is the real work. The migration is *value-preserving* in aggregate — a count of 100 persons in a 2016 SA2 that splits 60/40 across two 2021 SA2s produces values of 60 and 40 in the output, totalling 100.

### 10.4 What gets migrated, what doesn't

| Value type | Migration | Why |
|---|---|---|
| Counts (population, dwellings) | Population-weighted sum | Correct |
| Sums (total income, total dwellings) | Population-weighted sum | Correct under the assumption that the variable is proportional to population |
| Averages (median age) | **Not migrated** — surface the source-edition value with a warning | Average of averages is not an average; needs the underlying distribution |
| Ratios (% renters) | Migrate numerator + denominator separately, then divide | PRESET features handle this naturally — they're already num/denom internally |
| Ranks / deciles / percentiles | Migrate the source variable (the underlying score) but **warn** that the migrated rank isn't strictly meaningful | See §7.4 |

The dataset spec markdown gains a per-field `migration_strategy` annotation:

```yaml
schema:
  population_total:
    type: integer
    migration_strategy: weighted_sum
  median_age:
    type: float
    migration_strategy: preserve_source_edition
```

### 10.5 Output schema additions at Level 3

- `sa2_code` is the reference-edition code.
- `sa2_code_source` is the source-edition code (the one before migration).
- `sa2_code_edition` is the source edition (`"2016"`, `"2021"`).
- `sa2_resolution_method` extended: existing values plus `"correspondence_migrated"`.

The fully-enriched-on-reference-edition output joins cleanly on `sa2_code` across rows from any edition.

### 10.6 PRESETs and migration

PRESETs (`PRESET.pct_renters`, etc.) are computed *after* migration. Their numerator and denominator source columns get migrated separately; the ratio is computed on the migrated values. This is the right behaviour for percentages — fine to inherit from how PRESETs already separate num/denom in their spec.

### 10.7 G-NAF at Level 3

A G-NAF lookup returns an MB code. If the MB is from an older ASGS edition, the mesh-block correspondence table (`CG_MB_2016_MB_2021.csv`) migrates it to the reference edition's MB code, then the existing MB→SA2 fast path proceeds.

For point-in-polygon (lat/lon inputs), the spatial index is loaded from the reference edition's SA2 boundaries — so the resolution is naturally in the reference edition.

---

## 11. Output schema additions

Cross-sectional output (today's) is unchanged. Temporal-mode output adds these columns:

### Always present in temporal mode

| Column | Type | Description |
|---|---|---|
| `<input.date_column>` | datetime | Echoed from input (already there; no rename) |
| `seifa_release` | str | The SEIFA release used for this row, if any SEIFA variable was requested |
| `erp_by_sa2_release` | str | The ERP release |
| `dss_payments_release` | str | The DSS release |
| `ato_personal_income_release` | str | The PIA release (despite the misnomer; see §20) |
| `gcp_2021_release` | str | The GCP DataPack release |
| `gnaf_release` | str | The G-NAF release the address was looked up against |

Datasets not referenced in `variables:` don't get a release column.

### Level 3 additions

| Column | Type | Description |
|---|---|---|
| `sa2_code_source` | str | The pre-migration SA2 code |
| `sa2_code_edition` | str | The source ASGS edition (`"2016"`, `"2021"`) |
| `sa2_resolution_method` | str | Extended enum; gains `"correspondence_migrated"` |

### Order

Per spec.md §8 column order, the new columns slot in after the existing reserved seven:

```
<input cols>, geo_lat, geo_lon, geo_source, geo_match_score,
sa2_code, sa2_name, sa2_resolution,
[Level 3: sa2_code_source, sa2_code_edition,]
[temporal: <dataset>_release columns, ordered alphabetically by dataset id,]
<enrichment cols>
```

---

## 12. G-NAF temporal handling

G-NAF publishes quarterly via gnaf-loader. Addresses are *created* and *retired* between releases:

- New subdivisions appear as new gnaf_pids in the next release.
- Demolished or renumbered addresses are retired (still in some prior releases; not in the latest).

A 2010 transaction at an address that was created in the 2015 G-NAF release is unmatchable with the 2010 G-NAF release. A 2024 transaction at an address that was demolished in 2020 is unmatchable with the 2024 G-NAF release.

**Resolution rule for `gnaf.release`:**

- If `temporal.resolution = closest_at_or_before`: use the G-NAF quarterly nearest before the row's date.
- If `temporal.resolution = closest`: same as `closest_at_or_before` (G-NAF doesn't have a meaningful "after" — addresses don't go back in time).
- **Special case:** if a row's date is before the earliest G-NAF release available (2014 for gnaf-loader, earlier for the official ABS PSV), the resolver picks the earliest available and logs a WARNING.

This needs:

- The G-NAF data source's `available_releases()` to return the list of quarterly releases discoverable in the S3 bucket.
- A per-quarter cache key (already in place; the cache layout uses `<data_dir>/gnaf/{YYYYMM}/`).

**Per-bucket DuckDB:** running with N G-NAF releases means N DuckDB connections, each loading its release's parquet. With cache mode (~10 GB per release) this is disk-expensive; with remote mode (httpfs streaming) it's bandwidth-expensive. Mitigate with `closest` resolution + bucket-aggressive defaults (e.g. quarterly buckets, not per-row).

---

## 13. Caching strategy

The existing per-release cache layout already supports multiple releases coexisting:

```
<data_dir>/
├── boundaries/                    # NEW: per-edition subdirs
│   ├── 2016/
│   │   └── SA2_2016_AUST_SHP_GDA2020.zip (+ extracted)
│   └── 2021/
│       └── SA2_2021_AUST_SHP_GDA2020.zip (+ extracted)
├── census/                        # NEW: per-edition subdirs (mirrors boundaries)
│   ├── 2016/
│   └── 2021/
├── correspondences/               # NEW
│   ├── CG_SA2_2016_SA2_2021.csv
│   ├── CG_MB_2016_MB_2021.csv
│   └── ...
├── erp_by_sa2/                    # already per-release
│   ├── erp-sa2-2017-18.xlsx (+ parquet sidecar)
│   ├── erp-sa2-2022-23.xlsx
│   └── ...
├── seifa_2021/                    # already per-release
├── seifa_2016/                    # NEW (registered as separate dataset)
├── dss_payments/                  # already per-release
│   ├── dss-2018-Q2.xlsx
│   ├── dss-2024-Q3.xlsx
│   └── ...
├── ato_personal_income/           # already per-release
├── gnaf/{YYYYMM}/                 # already per-release
└── ...
```

**Boundary cache restructure** is the only invasive change. Today `boundaries/` is flat — adding edition subdirs requires migrating existing caches. Approach: on first temporal-mode run that needs multiple editions, the boundary fetcher detects the flat legacy cache + migrates it into the `2021/` subdir, leaving a `.migrated` marker so the migration runs once. Documented in CHANGELOG; transparent to the user.

**`asgs_correspondences/` subdir** is new. ~25 MB total for all six edition-pair correspondence files; small enough to fetch eagerly on first temporal-mode run.

---

## 14. Error cases and edge conditions

### 14.1 Bad date column data

- Column referenced by `input.date_column` doesn't exist in input → loud `ValueError` with the column name and the available columns.
- Some rows have unparseable dates (e.g. "N/A", "TBD", or wrong format) → fail the run with a per-row error listing the first 3 bad rows + their row indices. Don't silently coerce to NaT.
- Column is all-null → fall back to cross-sectional mode with an INFO log? Or fail loudly? **Recommend fail loudly** — user explicitly asked for temporal mode, give them a clear error.

### 14.2 Out-of-range dates

Per `temporal.out_of_range`:

- `fail` (default) — abort. The first row whose date predates *any* dataset's earliest release fails the whole run.
- `nearest` — clamp to the earliest available release; WARN per affected row.
- `drop` — silently drop the row; count in `RunSummary.out_of_range_dropped`.

### 14.3 Dataset has no temporal capability

A registered dataset without a `temporal:` block in its spec markdown uses its configured `release` for every row in a temporal-mode run. This is "graceful degradation" — a custom user-added dataset doesn't break the temporal-mode run, it just produces the same value for every row (the configured release).

The summary names datasets that fell back to non-temporal mode. The user can opt in by adding `temporal:` to their dataset spec.

### 14.4 Bucketing overflow

A pathologically date-diverse input (e.g. 5 years of DSS quarters × 5 years of ERP × all SEIFA / GCP editions touched by date span) could produce a large number of buckets. With four datasets and 5 years of data, bucket count is bounded by ~5 × 20 = 100, which is fine. With G-NAF added it could be 100 × 20 = 2000 — still manageable.

But: **per-bucket Pipeline instances** mean per-bucket fetcher loads. With cache mode warm this is just disk reads; with remote mode it's network. Defaults should bias toward "fewer buckets":

- DSS quarterly with monthly-input data: default `closest` (not `closest_at_or_before`) so January's row picks Q1, not Q4-of-previous-year.
- G-NAF: default resolution is per-year, not per-quarter. The cost of a slightly stale G-NAF release for a 6-month-old transaction is dwarfed by the cost of loading a different DuckDB per quarter.

### 14.5 Migration produces fractional values

For weighted-sum migration, a 2016 SA2 with `population_total = 100` that splits 0.6 / 0.4 produces values 60 and 40. Fine — these are still integer-like.

But: `population_aged_65_plus = 13` that splits 0.6 / 0.4 produces 7.8 and 5.2 — fractional. The spec.md §8 schema doesn't say integer counts must be integer; we round to nearest integer at migration time with a one-line note in the output schema.

### 14.6 Reference edition not yet supported

If the user sets `temporal.reference_edition: 4` (ASGS Edition 4) before Edition 4 correspondences ship: clear `ValueError` listing the supported reference editions.

---

## 15. Real-data verification additions

Per CLAUDE.md Real Data First, every new external artefact gets a real-fetch + parser-verify step.

`tools/verify_real_parsers.py` gains:

- Fetch each of the six SA2-level / SA1 / MB correspondence CSVs (~20 MB total across all SA-level files).
- Parse each; verify column names match §7.2.
- Spot-check: ratios sum to ~1 per FROM region (within 0.01 tolerance for rounding).
- Spot-check: at least one known split (e.g. a specific SA2 2016 code we know was split).

For the new historical SEIFA / ERP / DSS / ATO PIA releases registered:

- Fetch the earliest registered release (e.g. SEIFA 2011) + the latest (e.g. SEIFA 2021).
- Verify the schema is consistent enough for the same parser to read both. If not, the historical-release support is per-edition (separate parser per edition).

`tools/fetch_real_data.py` gains a `--correspondences` flag (defaults on) and per-historical-release flags (`--seifa-year 2016`, etc.).

The new `.github/workflows/real-data-check.yml` (Phase 3, PR #57) auto-picks up the new probes via `verify_real_parsers.py`.

---

## 16. Backward compatibility

**Strong commitment: zero behavioural change for v1.4.x configs.**

- A config without `input.date_column` runs cross-sectionally with the exact same code path it does today.
- The `release` field on each dataset's config block still does what it does today (pin a specific release for a cross-sectional run).
- Output schema for cross-sectional runs is unchanged — no new columns, no renamed columns.
- The library `Pipeline.augment(df)` signature is unchanged in cross-sectional mode; the new fields on `AugmentResult` are `None`-valued.

The first temporal-mode run with a 1.4.x config will fail validation at config-parse time if temporal options are mis-specified, not at runtime. This is consistent with the existing Pydantic-validates-up-front pattern.

---

## 17. Out of scope

For this design, deliberately:

- **Multi-snapshot side-by-side output (UC-1).** "Show me 2011, 2016, 2021 income for this address simultaneously" is a different operation — it's `pivot` over time, not `join_asof`. Users wanting this run the pipeline three times with three different `census.year` configs, or build their own multi-call wrapper. Could be a future `Pipeline.augment_multi_release(df, years=[2011, 2016, 2021])` API; not now.
- **Sub-annual ABS PIA / ERP.** These are annual datasets at the source. Within-year interpolation is interpolation, not augmentation.
- **2026 Census preview.** ASGS Edition 4 ships from Jul 2026; the 2026 Census release lands in mid-2027. We won't support either until the actual artefacts ship.
- **Custom user correspondence tables.** A user with their own region-to-SA2 mapping (e.g. school catchments → SA2) could in principle reuse the migration infrastructure. Out of scope for v1.

---

## 18. Open questions for review

Decisions worth your call before any code lands.

### Q1. Default resolution rule

**Proposed:** `closest_at_or_before`. Causally correct ("what was the demographic at the time of this transaction"). Matches financial-grade "as-of" semantics.

**Alternative:** `closest`. Picks the release whose midpoint is nearest. Better for "what's the most representative snapshot" semantics but causally weaker.

**Mixed proposal:** dataset-specific defaults — `closest_at_or_before` for annual + per-Census datasets; `closest` for DSS (quarterly, so the "wrong side" of the resolution rule misses by at most ~45 days).

### Q2. Out-of-range default

**Proposed:** `fail`. Loud + actionable. User has to make an explicit decision.

**Alternative:** `nearest`. More forgiving; risks silently using a wildly inappropriate release (e.g. using ERP 2001 for a 1990 transaction).

### Q3. Historical SEIFA / ERP / PIA depth

**Proposed:** Register **2016 onwards** for SEIFA / ERP / ATO PIA. Older releases (2011, 2006, 2001) live on different ASGS editions and the URLs are less predictable.

**Alternative:** Go back further — 2011 specifically — because Level 3's correspondence support makes the geography migration tractable. Trade-off: more dataset specs to maintain.

**Note:** GCP DataPack registration extends naturally too — `gcp_2016` and `gcp_2011` as separate datasets sharing a common base.

### Q4. Migration semantics for averages / medians

**Proposed:** Preserve source-edition value; warn. Population-weighted average of medians is wrong; we don't have the underlying distribution to do better.

**Alternative:** Just do the weighted average anyway and let the user accept that. Faster to implement; more often subtly wrong.

### Q5. Level 2-only output without ASGS migration

What happens when a Level 2 (no L3) run produces rows in mixed ASGS editions?

**Proposed:** Emit the row with its source-edition SA2 code; add `sa2_code_edition` column; WARN once per run. Downstream consumers see correct codes but must handle the edition split themselves.

**Alternative:** Refuse to run; require Level 3 to be opted in. More restrictive, fewer subtle bugs.

### Q6. `ato_personal_income` rename

The dataset is misnamed (see §20). Renaming is a breaking change for users with configs using `ATO.foo`. Options:

a) **Leave alone, document** as a known misnomer.
b) **Rename to `abs_personal_income`**, alias the old name in the registry for backward compatibility.
c) **Rename in temporal-spec PR**, batch the disruption with the other config changes.

**Proposed:** (b). Costs ~5 lines of code; closes the bug; no user disruption.

### Q7. Cache directory restructure for boundaries

**Proposed:** Auto-migrate flat `boundaries/SA2_2021_AUST_SHP_GDA2020.zip` into `boundaries/2021/SA2_2021_AUST_SHP_GDA2020.zip` on first temporal-mode run; leave a `.migrated` marker. Transparent.

**Alternative:** Require user to run `census-augment clear-cache` first. Cleaner but disruptive.

---

## 19. Implementation roadmap

| Phase | Scope | Effort | Dependencies |
|---|---|---|---|
| **L1** | `docs/temporal-data.md` documenting the current limitation + workaround. | ~30 min | None — ship now |
| **L2.0** | Per-dataset `temporal:` blocks in all existing dataset spec markdown files; no pipeline change. | ~1 day | None |
| **L2.1** | Bucketing orchestrator (`Pipeline._augment_temporal`); `input.date_column` config; per-bucket fan-out. Cross-sectional output identical. | ~3 days | L2.0 |
| **L2.2** | Per-dataset release columns in temporal output; `AugmentResult` additions. | ~1 day | L2.1 |
| **L2.3** | G-NAF release-per-bucket; G-NAF `available_releases()` helper. | ~2 days | L2.1 |
| **L2.4** | Historical SEIFA / ERP / PIA / GCP registration; real-data verification. | ~3 days | L2.0 |
| **L3.0** | `asgs_correspondences` dataset + parser + parquet cache; real-data verification against the live `CG_*` CSVs. | ~2 days | L2.0 |
| **L3.1** | Migration orchestrator (the join + weighted-sum + groupby); per-field `migration_strategy` annotations. | ~3 days | L3.0 |
| **L3.2** | Reference-edition config; SA2 code rewriting in output; `sa2_code_source` / `sa2_code_edition` columns. | ~1 day | L3.1 |
| **L3.3** | Cache layout restructure (edition-subdirs for boundaries); migration helper. | ~1 day | L3.1 |
| **L3.4** | PRESET interaction with migration; per-PRESET migration tests. | ~1 day | L3.2 |

Total: **~3 weeks** for full L1 + L2 + L3. L2 alone is ~10 days and ships independently useful. L1 ships immediately.

Each level lands as its own PR cluster; L2 / L3 are themselves multi-PR sequences (~5 PRs each) so review stays tractable.

---

## 20. Side-quest: `ato_personal_income` is misnamed

While researching dataset cadences, the agent verified that what we call `ato_personal_income` (`src/census_augment/datasets/_ato.py`, namespace `ATO`) actually fetches **ABS catalogue 6524.0.55.002 "Personal Income in Australia"**, not ATO Taxation Statistics.

ABS PIA is a LEED-derived ABS product (using ATO data as one input). ATO publishes its own "Individuals" tables annually but at SA4 / postcode granularity, not SA2 — so the current implementation is correct, the **name is misleading**.

Recommended action (Q6 in §18 above):

- Add a new dataset registration `abs_personal_income` (namespace `ABS_PIA` or similar — avoiding the over-broad `ABS`).
- Keep `ato_personal_income` / `ATO` namespace aliased for backward compatibility (`v2.0` breaking-change candidate).
- Document in CHANGELOG + the dataset's spec markdown.

Not strictly part of the temporal spec, but worth fixing in the same PR cluster since the dataset's `temporal:` block needs writing anyway.

---

## End of spec

Awaiting review. Send specific feedback on §18's open questions, the level scope (L1 / L2 / L3 split), or anything else worth pushing back on.
