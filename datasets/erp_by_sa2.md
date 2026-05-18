---
id: erp_by_sa2
name: ABS Estimated Resident Population by SA2
status: proposed
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
fetch_size_compressed: ~3 MB
tags: [population, demographics, denominators]
namespace: ERP
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY (year ending 30 Jun)"
  available_releases:
    - "2016"
    - "2017"
    - "2018"
    - "2019"
    - "2020"
    - "2021"
    - "2022"
    - "2023"
    - "2024"
  asgs_edition_by_release:
    "2016": 2
    "2017": 2
    "2018": 2
    "2019": 2
    "2020": 2
    "2021": 2
    "2022": 3
    "2023": 3
    "2024": 3
---

# ABS Estimated Resident Population by SA2

Annual mid-year ABS population estimates at SA2 level. Updated each March
with provisional estimates for the prior June. Critical denominator for any
rate calculation that should reflect current population rather than the
five-yearly Census snapshot — at the end of an inter-Census period, Census
counts can be 5+ years stale and have drifted by 10-15% in growth corridors.

## Source

Landing page: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release

ABS publishes ERP as Excel workbooks under "Data downloads" on the latest
release page. The relevant workbook is "Population Estimates by Statistical Area
Level 2 (ASGS 2021), 2001 onwards".

The augmentor should:

1. Resolve the latest release URL by parsing the landing page (no clean API).
2. Download the SA2 workbook.
3. Parse the relevant sheet (typically named "Table 1" or by the SA2 schema) into a
   long-format `(sa2_code, year, population, ...)` parquet.

## Update cadence

Annual. Provisional estimates released ~9 months after reference period;
revised final estimates released alongside the next year's provisional.

## Granularity

SA2 native, on the current ASGS edition. ABS rebases to the new ASGS when
edition changes (most recently 2016 → 2021); historical estimates are
re-issued on the new boundaries via concordance, so a single time series
on 2021 boundaries goes back to ~2001.

## Schema (variables exposed by the augmentor)

The v1.5 fetcher exposes the latest-year summary plus the full per-year time
series. Age-band breakdowns, median age, gendered totals, and a derived
population-density column are wish-list items not yet wired up (see "Wish list"
below).

| Variable | Type | Description |
|---|---|---|
| `ERP.population_total` | int | Total estimated resident population for the latest reference year |
| `ERP.reference_year` | int | The mid-year reference year (e.g. 2024 = June 2024) |
| `ERP.state_abbreviation` | str | State/territory abbreviation (NSW, VIC, ...) |

In addition, the fetcher emits one int column per available year as
`ERP.population_history_YYYY` (e.g. `population_history_2001` through
`population_history_2024` for the 2024 release). These cover the same
2001-onwards history ABS publishes in the source workbook and let
downstream consumers compute multi-year growth without re-fetching.

The augmentor returns the latest available year by default. Users can pin
via `Pipeline.create(..., erp_year=2023)`.

### Wish list — spec'd in earlier drafts, not yet implemented

These rows were documented in the v1.4 draft of this spec but never landed
in the fetcher. They're real ABS-published series; wiring them up means
parsing additional sheets / computing density from the ASGS area lookup.
Tracked in BACKLOG.md.

- `ERP.population_male`, `ERP.population_female` — gendered totals
- `ERP.population_0_14`, `ERP.population_15_64`, `ERP.population_65_plus` — age bands
- `ERP.median_age` — median age (years)
- `ERP.population_density_per_km2` — derived from SA2 area

## Fetch notes

- The XLSX schema is stable but sheet names and column ordering have shifted
  between releases. Parse by header content, not by position.
- "No usual address" / "Migratory, offshore and shipping" pseudo-SA2s appear
  in some releases. Filter to substantive SA2s (codes starting with 1–8 for
  states/territories) per the ASGS specification.

## Suppression / privacy notes

- ERP is an estimate, not a count, so there is no formal small-cell suppression.
- Estimates for very small SA2s (< 100 people) carry larger relative error;
  ABS publishes RSE (relative standard error) bands for these in some releases.
  v1 ignores RSE; downstream users who need it can request the raw workbook.

## Suggested derived features

- `population_growth_5y` — (ERP.population_total - ERP.population_total[t-5]) / ERP.population_total[t-5]
- `dependency_ratio` — (ERP.population_0_14 + ERP.population_65_plus) / ERP.population_15_64
- `pct_population_change_since_census` — drift from 2021 Census `Tot_P_P` to current ERP

## Sources / citations

- Landing: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
- Methodology: https://www.abs.gov.au/methodologies/regional-population-methodology
- Licence: CC-BY-4.0
