---
id: abs_building_approvals
name: ABS Building Approvals (catalogue 8731.0) by SA2
status: proposed
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: monthly
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
fetch_size_compressed: ~1.5 MB (8 per-state XLSX cubes, ~180 KB each)
tags: [housing, construction, building, urban-development]
namespace: ABS_BA
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

# ABS Building Approvals (catalogue 8731.0) by SA2

Monthly ABS publication of building approval counts and values, broken down to
SA2 level. The "approvals" headline indicator covers all building work that
requires a permit from a local government authority — new dwellings (houses
plus other residential — apartments, units, townhouses), alterations &
additions, and non-residential building. Counts are number of approvals;
values are estimated cost of the work in $'000s.

A leading indicator of housing supply and construction-sector activity at
small-area granularity — useful for housing-market analysis, planning, and
demographic-change correlation.

## Source

Landing page: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia

ABS publishes building approvals **monthly**, ~6 weeks after the reference
month. Each release contains both the previous complete financial year (e.g.
2024-25, file pattern `do002`/`do006`/`do010`/etc) and the current
financial-year-to-date (e.g. 2025-26 FYTD, file pattern `do003`/`do007`/etc).

Files are split per state/territory:

| State | SA2 complete FY | SA2 YTD |
|---|---|---|
| NSW | `87310do002_<YYYYMM>.xlsx` | `87310do003_<YYYYMM>.xlsx` |
| VIC | `87310do006_<YYYYMM>.xlsx` | `87310do007_<YYYYMM>.xlsx` |
| QLD | `87310do010_<YYYYMM>.xlsx` | `87310do011_<YYYYMM>.xlsx` |
| SA | `87310do014_<YYYYMM>.xlsx` | `87310do015_<YYYYMM>.xlsx` |
| WA | `87310do018_<YYYYMM>.xlsx` | `87310do019_<YYYYMM>.xlsx` |
| TAS | `87310do022_<YYYYMM>.xlsx` | `87310do023_<YYYYMM>.xlsx` |
| NT | `87310do026_<YYYYMM>.xlsx` | `87310do027_<YYYYMM>.xlsx` |
| ACT | `87310do030_<YYYYMM>.xlsx` | `87310do031_<YYYYMM>.xlsx` |

`<YYYYMM>` is the reference month (e.g. `202603` = March 2026 release).
The fetcher discovers the latest reference month from the landing page,
then downloads all 8 per-state SA2 cubes and concatenates the SA2 rows.

There's also a parallel set of LGA cubes (`do004` / `do005` etc), which
the augmentor doesn't use — joining via SA2 is direct (no cross-walk).

## Update cadence

Monthly. Reference month is ~6 weeks earlier than the release. Augmentor
treats this as "annual" for caching purposes — each FY is one snapshot,
even though the underlying ABS release happens 12 times per year. Re-running
with `refresh=True` picks up the latest monthly snapshot of the current FY.

## Granularity

SA2 native, on the current ASGS edition (Edition 3 / 2021 boundaries).
Each XLSX cube's `Table_1` sheet has 9-digit SA2 codes in column A mixed
with parent-level aggregates (state code = 1 digit, GCC code = alphanumeric
like `1GSYD`, SA4 = 3-digit, SA3 = 5-digit). The parser filters strictly to
9-digit numeric codes to drop aggregates.

## Schema (variables exposed by the augmentor)

Counts are number of approvals; values are estimated cost in `$'000` (thousands of dollars).

| Variable | Type | Description |
|---|---|---|
| `ABS_BA.new_houses_count` | int | Number of approvals for new houses (free-standing dwellings) |
| `ABS_BA.new_other_residential_building_count` | int | Number of approvals for new other residential building (apartments, units, townhouses) |
| `ABS_BA.total_dwellings_count` | int | Total new dwelling approvals (houses + other residential) |
| `ABS_BA.value_new_houses` | float | Estimated value of new house approvals, in `$'000` |
| `ABS_BA.value_new_other_residential_building` | float | Estimated value of new other residential building approvals, in `$'000` |
| `ABS_BA.value_alterations_additions_conversions` | float | Estimated value of alterations, additions, and conversion approvals, in `$'000` |
| `ABS_BA.value_total_residential_building` | float | Estimated value of all residential building approvals (new + alterations), in `$'000` |
| `ABS_BA.value_non_residential_building` | float | Estimated value of non-residential building approvals (offices, retail, etc.), in `$'000` |
| `ABS_BA.value_total_building` | float | Estimated value of all building approvals (residential + non-residential), in `$'000` |
| `ABS_BA.reference_financial_year` | str | Reference period (e.g. "2024-25") |

## Fetch notes

- The XLSX is openpyxl-readable (no `.xls` legacy format quirks).
- `Table_1` sheet structure (live-probed 2026-06-01 on March 2026 release):
  - Row 4 (0-indexed) = column headers (mixed metric names)
  - Row 5 = units row (`no.` for counts, `$'000` for values)
  - Row 6 onwards = data, with column A holding mixed-level codes
- Some SA2s have zero approvals in a given period — that's a real `0`, not a
  missing value. The parser preserves zero.
- The data is unperturbed at SA2 level; ABS publishes raw counts because
  approvals aren't person-identifying.

## Suppression / privacy notes

None at SA2 level. Approval counts are public-record from local government
authority reporting; no perturbation is applied. Confidentialised tax-style
suppression doesn't apply.

## Suggested derived features

- `housing_supply_rate` — `ABS_BA.total_dwellings_count` / `ERP.population_total` × 1000
  (new approvals per 1,000 residents per year).
- `pct_apartment_approvals` — `ABS_BA.new_other_residential_building_count` /
  `ABS_BA.total_dwellings_count` (share of dwelling approvals that are apartments
  rather than free-standing houses; rough urban-density proxy).
- `mean_dwelling_approval_value` — (`ABS_BA.value_new_houses` +
  `ABS_BA.value_new_other_residential_building`) × 1000 /
  `ABS_BA.total_dwellings_count` (mean approval value, $).

## Sources / citations

- Landing: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
- Methodology: https://www.abs.gov.au/methodologies/building-approvals-australia-methodology
- Licence: CC-BY-4.0
