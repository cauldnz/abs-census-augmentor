---
id: pct_aged_65_plus
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp_2021
default: false
tags: [demographics, age]
numerator:
  expression: field
  field: G04.Age_65_yr_above_P
denominator:
  expression: field
  field: G01.Tot_P_P
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G04 = Age by sex; G01 = Selected person characteristics
---

# pct_aged_65_plus

Share of usual residents aged 65+. The headline "ageing" measure
for an SA2.

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
