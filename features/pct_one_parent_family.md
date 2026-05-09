---
id: pct_one_parent_family
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp_2021
default: false
tags: [demographics, family]
numerator:
  expression: field
  field: G29.OneP_F_C_Tot
denominator:
  expression: field
  field: G29.Tot_F_C_Tot
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G29 = Family composition
---

# pct_one_parent_family

Share of families with children under 15 that are one-parent
families.

## Why this denominator

Families *with children* — couples without kids and other family
types are not the population this rate is well-defined over. Using
"all families" undercounts by including childless couples and
non-traditional households that can't be one-parent.

## Edge cases

- **Zero denominator** → null. Some industrial / national-park SA2s
  have no resident families.
- ABS perturbation can produce category sub-totals that don't sum
  exactly.

## Bounds (typical)

National average ~16%. Higher in low-income outer-suburban and
remote SA2s (25–40%); lower in inner-city couple-with-kids SA2s
(5–10%).

## Sources

- ABS Census Dictionary, FMCF / FMSF variables
- 2021 Census product release guide
