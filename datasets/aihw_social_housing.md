---
id: aihw_social_housing
name: AIHW Social Housing Dwellings (Housing Assistance) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare
licence: CC-BY-3.0-AU
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/reports/housing-assistance/housing-assistance-in-australia/data
fetch_size_compressed: ~260 KB (single XLSX workbook)
tags: [housing, social-housing, tenure, downscale]
namespace: AIHW_SH
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY (reference year, dwellings at 30 June)"
  available_releases:
    - "2023"
  asgs_edition_by_release:
    "2023": 3
---

# AIHW Social Housing Dwellings (Housing Assistance) by SA4

Annual AIHW dataset from **Housing Assistance in Australia**. Captures
the count of **social-housing dwellings** by program — public housing,
State Owned and Managed Indigenous Housing (SOMIH), and community
housing — at **SA4** level, downscaled to SA2. Dwelling counts are as at
**30 June** of the reference year.

A **tenure / built-environment** signal not derivable from the standard
Census GCP: where Census tenure tells you how many households *rent*,
this tells you how much of the dwelling stock is publicly / community
provided social housing. Slots into the same SA4 → SA2 inheritance the
AIHW mental-health datasets use.

## Source

Single XLSX workbook ("Social housing dwellings" data tables); the SA4
table is sheet **DWELLINGS.4**:

- 2023: `https://www.aihw.gov.au/getmedia/47ce0fe9-8706-4991-9fa9-1b0770971ef8/AIHW-337-Data-tables-Social-housing-dwellings.xlsx`

The AIHW `getmedia` UUID **and** the series number (e.g. `337`) change
each annual release — the URL is hardcoded per release (no HTML scrape);
a new release needs a new entry in `_AIHW_SH_URLS_BY_RELEASE` in the
fetcher.

## Update cadence

Annual (dwellings as at 30 June). The SA4 table (DWELLINGS.4) carries a
single reference year per workbook.

## Granularity

SA4 native (the workbook's `Region Code` is the bare 3-digit SA4 code).
Downscaled to SA2 by inheritance — every SA2 inside SA4 X carries SA4
X's value (the honest "no within-parent variation" contract). Note AIHW
itself assigned dwellings to SA4s via a postcode→SA2 correspondence
(workbook footnote), so the SA4 figures are already a modelled
allocation; the augmentor's SA2 inheritance adds no further precision.

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `AIHW_SH.social_housing_public_count` | int | Public-housing dwellings in the SA4 at 30 June |
| `AIHW_SH.social_housing_somih_count` | int | State Owned and Managed Indigenous Housing (SOMIH) dwellings — null for states without a SOMIH program (Vic / WA / ACT) |
| `AIHW_SH.social_housing_community_count` | int | Community-housing dwellings in the SA4 |
| `AIHW_SH.social_housing_total_count` | int | Total social-housing dwellings (public + SOMIH + community) |
| `AIHW_SH.reference_period` | str | Reference year (e.g. "2023" — dwellings at 30 June 2023) |

### Wish list — spec'd here, not yet implemented

- **Occupancy rates** (DWELLINGS.6/.7) and **dwelling structure**
  (bedrooms, dwelling type — DWELLINGS.9/.10) are in the same workbook
  but published at state / remoteness level, not SA4, so they aren't
  SA4-downscalable; deferred.
- **LGA-level totals** (DWELLINGS.5) — a `total housing` figure by LGA,
  downscalable via the existing LGA→SA2 correspondence as an alternative
  geography.

## Fetch notes (live-probed 2026-06-10)

- The SA4 sheet is `DWELLINGS.4`; banner rows 1-2, header at row 4, data
  from row 5. Columns: `State/territory, Region Code, Region Name,
  Public housing, SOMIH(a), Community housing, Total`.
- The `SOMIH` column uses the suppression sentinel `". ."` for states
  without a SOMIH program — parsed to null.
- Footnote rows after the data carry a blank `Region Code`, dropped by a
  strict 3-digit SA4-code filter. 88 SA4 rows.

## Suppression / privacy notes

- `". ."` denotes "not applicable" (no SOMIH program in that state) and
  parses to null. AIHW rounds counts; the sum of SA4s may not exactly
  match national totals in companion tables (workbook footnote).

## Suggested derived features

- `pct_social_housing_community` —
  `AIHW_SH.social_housing_community_count / AIHW_SH.social_housing_total_count
  × 100` (community-housing share of the social-housing stock — a
  provider-mix signal).
- A social-housing-per-dwelling density would need a total-dwellings
  denominator at SA4; the augmentor exposes dwellings at SA2 via Census
  GCP, so this is better computed at SA2 after enrichment.

## Sources / citations

- AIHW Housing Assistance in Australia — data:
  https://www.aihw.gov.au/reports/housing-assistance/housing-assistance-in-australia/data
- Licence: CC-BY-3.0-AU
