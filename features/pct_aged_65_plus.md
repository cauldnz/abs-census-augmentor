---
id: pct_aged_65_plus
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp
default: false
tags: [demographics, age]
numerator:
  expression: sum
  fields:
    - G01.Age_65_74_yr_P
    - G01.Age_75_84_yr_P
    - G01.Age_85ov_P
denominator:
  expression: field
  field: G01.Tot_P_P
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G01 = Selected Person Characteristics by Sex; carries the published age-band columns
---

# pct_aged_65_plus

Share of usual residents aged 65+. The headline "ageing" measure
for an SA2.

## Why this numerator

G01 publishes age in 10-year bands above 25, with `Age_65_74_yr_P`,
`Age_75_84_yr_P`, and `Age_85ov_P` covering everyone 65 and over.
Summing the three gives the 65+ population for the SA2.

The previous version of this spec referenced a fictional
`G04.Age_65_yr_above_P` — `G04` doesn't exist in the 2021 GCP
DataPack at all (the table is split into `G04A` for males and
`G04B` for females, neither of which has a "65+" total). Even if
the published `G04A` / `G04B` were used, they bring nothing G01
doesn't already provide for this calculation.

## Why this denominator

`G01.Tot_P_P` is the SA2's total persons by place of usual residence
— exactly the population the numerator is drawn from. Census-night
counts (visitors included) would change the answer in tourist /
student SA2s.

## Edge cases

- **Zero denominator** → null (SA2s with `Tot_P_P = 0`).
- ABS perturbation can make age-band sub-totals not sum to
  `Tot_P_P` exactly; don't assert equality.

## Bounds (typical)

National average ~17%. Retirement-coast SA2s sit 25–35%;
working-age inner-city 5–10%.

## Sources

- ABS Census Dictionary, AGEP variable
- 2021 Census product release guide
- Real-data schema check: `tests/fixtures/gcp-schemas/G01.txt`
