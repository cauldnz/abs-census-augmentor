---
id: abs_personal_income
name: ABS Personal Income in Australia by SA2
status: proposed
custodian: Australian Bureau of Statistics (sourced from ATO administrative data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/personal-income-australia
fetch_size_compressed: ~4 MB
tags: [income, employment, administrative-data]
namespace: ABS_PIA
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2015-16"
    - "2016-17"
    - "2017-18"
    - "2018-19"
    - "2019-20"
    - "2020-21"
    - "2021-22"
    - "2022-23"
  asgs_edition_by_release:
    "2015-16": 2
    "2016-17": 2
    "2017-18": 2
    "2018-19": 2
    "2019-20": 3
    "2020-21": 3
    "2021-22": 3
    "2022-23": 3
---

# ABS Personal Income in Australia by SA2

Annual SA2-level personal income statistics derived from ATO administrative
records, published by ABS. Distinguishes "employee income", "investment income",
"superannuation income", "own unincorporated business income", and "total income"
— giving a richer income decomposition than Census self-report covers.

Different bias profile to the Census income variable (`G02.Median_tot_hhd_inc_weekly`):

- ATO data captures only people who lodged tax returns. Excludes those below the
  tax-free threshold who didn't file, which biases coverage in low-income SA2s.
- Census self-report income tops out at the highest income band (currently
  "$3,000 or more weekly"); ATO data has no top-coding and so captures the
  high-income tail more accurately. This matters in high-income SA2s where
  the Census median is censored.

Both have value; this dataset complements rather than replaces the Census source.

## Source

Landing page: https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/personal-income-australia

ABS publishes the data as a series of XLSX workbooks under "Data downloads",
with separate files for SA2, LGA, GCCSA, and other geographies. The augmentor
fetches the SA2 workbook.

## Update cadence

Annual. Reference period is the previous-but-one financial year (e.g. the
release in early 2025 covers FY 2022-23) — data has roughly an 18-month lag
because of tax-return processing.

## Granularity

SA2 native, on the current ASGS edition. ABS rebases historical years to the
current ASGS via concordance.

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `ABS_PIA.median_total_income` | float | Median total income across persons in SA2 ($) |
| `ABS_PIA.mean_total_income` | float | Mean total income ($) |
| `ABS_PIA.median_employee_income` | float | Median employee income ($) |
| `ABS_PIA.median_investment_income` | float | Median investment income ($) — many people have $0 here |
| `ABS_PIA.median_super_income` | float | Median superannuation income ($) |
| `ABS_PIA.median_own_business_income` | float | Median income from own unincorporated business ($) |
| `ABS_PIA.gini_coefficient` | float | Gini coefficient of total income within the SA2 |
| `ABS_PIA.income_earners_count` | int | Number of income earners (people who lodged a return with non-zero income) |
| `ABS_PIA.reference_financial_year` | str | Reference period (e.g. "2022-23") |

## Fetch notes

- The XLSX has multiple sheets (one per income type). Parse all sheets and
  pivot into a long-format parquet keyed on `(sa2_code, financial_year)`.
- "Australia total" rows and intermediate aggregates are mixed into the SA2
  rows in some releases. Filter strictly on SA2 code length (= 9) to drop them.
- Median values for SA2s with very few earners are suppressed — surface as null.

## Suppression / privacy notes

- ABS applies suppression to SA2s with fewer than ~10 earners on a given
  income type. These appear as `np` (not published) in the source XLSX —
  parse to null.
- Median values are computed from microdata before perturbation; published
  values are not perturbed individually but the underlying microdata is.

## Suggested derived features

- `income_inequality_proxy` — ATO.gini_coefficient (already exposed; flagging
  as a feature for completeness).
- `pct_high_income_earners` — would need ATO income-band data, which is in
  a separate workbook; not in v1 scope.
- `census_vs_ato_income_ratio` — ABS_PIA.median_total_income / (G02.Median_tot_hhd_inc_weekly × 52).
  Useful diagnostic — if much greater than 1, the SA2 has substantial income
  invisible to Census self-report (high-income tail or significant non-employee
  income).

## Sources / citations

- Landing: https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/personal-income-australia
- Methodology: https://www.abs.gov.au/methodologies/personal-income-australia-methodology
- Licence: CC-BY-4.0
