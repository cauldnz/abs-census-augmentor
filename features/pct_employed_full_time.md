---
id: pct_employed_full_time
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp_2021
default: false
tags: [employment, labour-force]
numerator:
  expression: sum
  fields:
    - G43.E_FT_15ov_M
    - G43.E_FT_15ov_F
denominator:
  expression: sum
  fields:
    - G43.LF_15ov_M
    - G43.LF_15ov_F
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G43 = Selected labour force, education and migration characteristics by sex
---

# pct_employed_full_time

Share of the labour force aged 15+ that is employed full-time.

## Why this denominator

The labour force (employed + unemployed actively looking) is the
population the employment-rate question is well-defined over. Adding
"not in the labour force" (retirees, students, carers) to the
denominator under-counts the share by 30+ percentage points in SA2s
with high non-participation.

## Why not employed-total

Excluding the unemployed from the denominator inflates the rate when
unemployment is high (the SA2's unemployed are still part of the
"labour force aged 15+", and their absence from the numerator
*should* reduce the percentage).

## Edge cases

- **Zero denominator** → null. SA2s with `LF_15ov_M + LF_15ov_F = 0`
  (industrial / no-resident / national-park SA2s).
- ABS perturbation may produce sub-totals that don't sum exactly.

## Bounds (typical)

National average ~58%. Inner-city working-age SA2s 65–75%;
retirement-coast 35–45%.

## Sources

- ABS Census Dictionary, LFSP variable
- 2021 Census product release guide
