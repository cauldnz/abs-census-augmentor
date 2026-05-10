---
id: motor_vehicles_per_dwelling
status: proposed
output_kind: ratio
bounds: [0, 10]
dataset: gcp_2021
default: false
tags: [transport, vehicle-ownership, fuel-demand]
numerator:
  expression: field
  field: G34.Total_motor_vehicles
denominator:
  expression: field
  field: G34.Total_dwellings
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G34 = Number of motor vehicles by dwellings
---

# motor_vehicles_per_dwelling

Mean motor vehicles per occupied dwelling — a useful proxy for
fuel-demand modelling and household-mobility analysis.

## Why this denominator

`G34.Total_dwellings` is the row total of the motor-vehicle table —
the population the count is drawn from. Using `G01.Tot_P_P` (total
persons) gives a different concept entirely (vehicles per person)
which under-states by ~2x because of multi-person households.

## Edge cases

- **Zero denominator** → null. Industrial / no-resident SA2s.
- **Perturbation** — sub-totals may not exactly sum to `Total_dwellings`.
- **Bounds** — values > 10 are almost certainly noise; values 4-6
  appear in low-density rural SA2s with multiple farm vehicles.

## Bounds (typical)

National average ~1.7 vehicles/dwelling. Inner-city low (~0.9),
outer-suburban ~2.0, rural can be 2.5+.

## Sources

- ABS Census Dictionary, NPRD / VEHRD variables
- 2021 Census product release guide
