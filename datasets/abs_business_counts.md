---
id: abs_business_counts
name: ABS Counts of Australian Businesses (catalogue 8165.0) by SA2
status: proposed
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
fetch_size_compressed: ~8 MB (single national XLSX data cube, DC8)
tags: [economy, business, employment, industry]
namespace: ABS_CAB
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY (reference year, businesses operating as at 30 June)"
  available_releases:
    - "2023"
    - "2024"
    - "2025"
  asgs_edition_by_release:
    "2023": 3
    "2024": 3
    "2025": 3
---

# ABS Counts of Australian Businesses (catalogue 8165.0) by SA2

Annual ABS publication (also called **CABEE** — Counts of Australian
Businesses, including Entries and Exits) of the number of actively
trading businesses in the Australian economy, broken down to **SA2**
level by **employment-size band**. Counts are businesses operating as at
**30 June** of the reference year.

A direct measure of the local **economic base** — business density and
firm-size structure — at small-area granularity. Complements the
demographic / housing / welfare datasets with the "what economic
activity is here" dimension, and pairs naturally with ERP for a
businesses-per-capita density signal.

## Source

Single national XLSX data cube **DC8** ("Businesses by Industry Division
by Statistical Area Level 2 by Annualised Employment Size Ranges"):

- `https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/jul2021-jun2025/8165DC08.xlsx`

The single workbook carries **three reference years** (2023, 2024, 2025)
in three sheets; the release id selects which year the parser surfaces.
The URL is constructed deterministically (no HTML scrape, per spec §4);
a new annual workbook (e.g. `jul2022-jun2026`, due ~Aug 2026) needs a
new entry added to `_ABS_CAB_RELEASES` in the fetcher.

## Update cadence

Annual (released each December for the financial year ending the prior
June). Each workbook carries the latest three reference years.

## Granularity

SA2 native — no downscale. The cube is published in **long** format
(one row per industry division × SA2, 20 ANZSIC divisions A–S plus an
`X` "Currently Unknown" division). There is **no per-SA2 total row**, so
the augmentor sums the 20 industry-division rows per SA2 to produce the
per-SA2 figure. National "Total All Industries" rows (blank SA2 code) and
footnote rows are excluded by a strict 9-digit SA2-code filter.

## Schema (variables exposed by the augmentor)

Per-SA2 business counts by **annualised employment-size band** (summed
across all industry divisions). The per-industry-division breakdown is
available in the source but not yet surfaced (see wish list).

| Variable | Type | Description |
|---|---|---|
| `ABS_CAB.business_count_non_employing` | int | Non-employing businesses (sole traders / no employees) in the SA2 |
| `ABS_CAB.business_count_1_4_employees` | int | Businesses with 1–4 employees |
| `ABS_CAB.business_count_5_19_employees` | int | Businesses with 5–19 employees |
| `ABS_CAB.business_count_20_199_employees` | int | Businesses with 20–199 employees |
| `ABS_CAB.business_count_200_plus_employees` | int | Businesses with 200+ employees |
| `ABS_CAB.business_count_total` | int | Total actively trading businesses in the SA2 (all industries, all sizes) |
| `ABS_CAB.reference_period` | str | Reference year (e.g. "2025" — businesses operating as at 30 June 2025) |

### Wish list — spec'd here, not yet implemented

- **Per-industry-division counts** (19 ANZSIC divisions × total) — the
  local *industry mix* / economic-composition signal. The source carries
  it directly; deferred to keep v1 focused on the firm-size structure.
- **Turnover-size bands** — the companion DC9 cube breaks businesses by
  annualised turnover range at SA2 (same structure, different size
  dimension).

## Fetch notes (live-probed 2026-06-10)

- One workbook holds three years: `Table 1` = June 2025, `Table 2` =
  June 2024, `Table 3 ` (trailing space) = June 2023. The parser
  validates the sheet's banner names the expected year before parsing.
- A **2-row header band** (size-band labels over a `Code`/`Label`/`no.`
  row); data starts two rows below the band-label row.
- Columns: Industry Code, Industry Label, SA2 Code (9-digit), SA2 Label,
  then the 5 employment-size bands and a Total.

## Suppression / privacy notes

- ABS **perturbs** small cell counts to protect confidentiality
  (footnote (b) of the cube). Division / state / employment-size /
  Australia totals are *not* perturbed. Because the augmentor sums
  perturbed industry-division rows, the summed size bands may not add
  exactly to the summed Total — both are surfaced as published; treat
  small SA2 counts as accurate to within perturbation.
- No SA2-level suppression to null in this cube (zeros are published
  explicitly).

## Suggested derived features

- `businesses_per_1000_residents` —
  `ABS_CAB.business_count_total / ERP.population_total × 1000` (local
  business density relative to resident population).
- `pct_businesses_non_employing` —
  `ABS_CAB.business_count_non_employing / ABS_CAB.business_count_total ×
  100` (share of the business base that is sole-trader / non-employing —
  a small-business / gig-economy structural signal).

## Sources / citations

- ABS 8165.0 Counts of Australian Businesses, including Entries and
  Exits: https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
- Licence: CC-BY-4.0
