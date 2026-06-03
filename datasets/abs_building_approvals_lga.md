---
id: abs_building_approvals_lga
name: ABS Building Approvals (catalogue 8731.0) at LGA, downscaled to SA2
status: proposed
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: monthly
geography_level: LGA
geography_edition: LGA_2025
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
fetch_size_compressed: ~1 MB (8 per-state XLSX cubes, ~125 KB each)
tags: [housing, construction, building, urban-development, lga, downscale]
namespace: ABS_BA_LGA
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2024-25"
    - "2025-26"
  asgs_edition_by_release:
    "2024-25": 3
    "2025-26": 3
---

# ABS Building Approvals (catalogue 8731.0) at LGA, downscaled to SA2

LGA-keyed sibling to the SA2-native `abs_building_approvals` dataset
(also catalogue 8731.0). ABS publishes the data at both granularities;
this dataset reads the LGA cubes and **downscales** the LGA values
to SA2 via an area-weighted spatial correspondence (see `spec.md`
§20.7 Strategy 2). The first dataset in the augmentor to exercise
`census_augment.correspondence.LgaSa2Correspondence` against real
production code.

**Why two datasets for the same source?** LGAs and SA2s overlap —
they're not nested geographies. An SA2 can span multiple LGAs, and an
LGA can span multiple SA2s. The two datasets carry slightly different
information:

- `abs_building_approvals` uses ABS's SA2-native publication. Counts
  and values come from ABS's own allocation of approvals to SA2s.
- `abs_building_approvals_lga` uses ABS's LGA-native publication and
  area-weight downscales to SA2. Counts and values reflect the LGA-
  level aggregate distributed proportionally by area share.

The two won't generally match at SA2 level — they're modelling slightly
different things. Pick whichever matches your downstream question. If
you're studying LGA-level planning policy effects, the LGA-source
values may be more honest. For most other analyses, the SA2-native
publication is the more direct choice.

Both datasets can coexist in a single config under different namespaces
(`ABS_BA` vs `ABS_BA_LGA`).

## Source

Landing page: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia

Same source as `abs_building_approvals`, different per-state product
files. The LGA SA2 series uses product codes offset by 2 from the SA2
series (state-pair × 4 + 4 vs state-pair × 4 + 2):

| State | LGA complete FY | LGA YTD |
|---|---|---|
| NSW | `87310do004_<YYYYMM>.xlsx` | `87310do005_<YYYYMM>.xlsx` |
| VIC | `87310do008_<YYYYMM>.xlsx` | `87310do009_<YYYYMM>.xlsx` |
| QLD | `87310do012_<YYYYMM>.xlsx` | `87310do013_<YYYYMM>.xlsx` |
| SA | `87310do016_<YYYYMM>.xlsx` | `87310do017_<YYYYMM>.xlsx` |
| WA | `87310do020_<YYYYMM>.xlsx` | `87310do021_<YYYYMM>.xlsx` |
| TAS | `87310do024_<YYYYMM>.xlsx` | `87310do025_<YYYYMM>.xlsx` |
| NT | `87310do028_<YYYYMM>.xlsx` | `87310do029_<YYYYMM>.xlsx` |
| ACT | `87310do032_<YYYYMM>.xlsx` | `87310do033_<YYYYMM>.xlsx` |

The fetcher discovers the latest reference month from the landing page
(same scrape as `abs_building_approvals`) and selects the right series
based on the requested release.

## Update cadence

Monthly. Each release ships both the previous complete financial year
(`do004`/`do008`/... series) and the current FYTD (`do005`/`do009`/...
series). Augmentor treats this as "annual" for caching purposes.

## Granularity

LGA native (5-digit LGA codes, e.g. `10050` Albury, matching the
`LGA_CODE25` attribute in the boundary file). Downscaled to SA2 via
the `LgaSa2Correspondence` area-weighted intersection. The honest
contract: count metrics use `downscale_counts()` (per-LGA sum
invariant — total approvals across SA2s in an LGA equal the LGA
total); value metrics use the same since they're additive too.

## Cross-level downscale

The fetcher requires a `LgaSa2Correspondence` to be attached before
`load()` (analogous to `aihw_mh_prescriptions.attach_sa2_to_sa4_mapping`).
`Pipeline.from_config` derives the correspondence automatically:

1. Fetches the LGA boundary via `LgaBoundariesDataSource(year="latest")`
2. Computes `compute_lga_sa2_correspondence(sa2=boundaries, lga=lga)`
3. Caches the result to a parquet sidecar so the ~30 s geometric
   intersection runs once per (SA2 release, LGA release) pair
4. Attaches the correspondence to the fetcher in
   `CensusEnricher._make_fetcher`

Without an attached correspondence, `load()` raises a clear
`RuntimeError` explaining how to attach one.

## Schema (variables exposed by the augmentor)

Counts are number of approvals; values are estimated cost in `$'000`
(thousands of dollars). All metrics are **additive** — downscaled via
`LgaSa2Correspondence.downscale_counts()` so the per-LGA sum is
preserved across the SA2s that overlap that LGA.

| Variable | Type | Description |
|---|---|---|
| `ABS_BA_LGA.new_houses_count` | float | Number of approvals for new houses (downscaled by SA2's area share of its overlapping LGAs) |
| `ABS_BA_LGA.new_other_residential_building_count` | float | New apartments / units / townhouses approvals, downscaled |
| `ABS_BA_LGA.total_dwellings_count` | float | Total new dwelling approvals (houses + other residential), downscaled |
| `ABS_BA_LGA.value_new_houses` | float | Estimated value of new house approvals, in `$'000`, downscaled |
| `ABS_BA_LGA.value_new_other_residential_building` | float | Estimated value, downscaled |
| `ABS_BA_LGA.value_alterations_additions_conversions` | float | Estimated value, downscaled |
| `ABS_BA_LGA.value_total_residential_building` | float | Estimated value, downscaled |
| `ABS_BA_LGA.value_non_residential_building` | float | Estimated value, downscaled |
| `ABS_BA_LGA.value_total_building` | float | Estimated value, downscaled |
| `ABS_BA_LGA.reference_financial_year` | str | Reference period (e.g. `"2024-25"`) |

Note: downscaled count columns are `float` (not `int`) because
area-weight redistribution produces fractional contributions per SA2.
The SA2-native `abs_building_approvals` keeps integer counts.

## Fetch notes (live-probed 2026-06-01)

- The LGA cube's data sheet is named **`Table 1`** (with space), not
  `Table_1` (with underscore) as in the SA2 cube. Real-Data-First
  payoff: the parser doesn't share the sheet-name string with the SA2
  parser.
- Otherwise the row layout matches the SA2 cube:
  - Row 4 (0-indexed) = column headers
  - Row 5 = units row (`no.` for counts, `$'000` for values)
  - Row 6 onwards = data
- Column A in data rows is the **5-digit LGA code** (vs the SA2
  cube's mixed 1-digit / 5-digit / 9-digit codes). NSW codes are
  10000-19999, VIC 20000-29999, etc. The parser filters strictly to
  5-digit numeric codes to drop the single state-aggregate row at the
  top.
- An LGA code in the cube that isn't in the boundary's
  `LGA_CODE25` set (e.g. very recent boundary change, "Unincorporated"
  pseudo-LGAs) gets warned about + dropped from the downscale — its
  values don't contribute to any SA2.

## Suppression / privacy notes

None at LGA level. Approval counts are public-record from local
government authority reporting; no perturbation is applied at LGA
granularity.

## Suggested derived features

- `housing_supply_rate_lga` —
  `ABS_BA_LGA.total_dwellings_count / ERP.population_total × 1000` —
  LGA-source equivalent of `housing_supply_rate` against the SA2-
  source dataset. Useful for comparing publication discrepancies.

## Sources / citations

- Landing: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
- Methodology: https://www.abs.gov.au/methodologies/building-approvals-australia-methodology
- Licence: CC-BY-4.0
