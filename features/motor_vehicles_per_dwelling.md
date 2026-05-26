---
id: motor_vehicles_per_dwelling
status: proposed
output_kind: ratio
bounds: [0, 10]
dataset: gcp
default: false
tags: [transport, vehicle-ownership, fuel-demand]
numerator:
  expression: weighted_sum
  fields:
    - G34.Num_MVs_per_dweling_0_MVs
    - G34.Num_MVs_per_dweling_1_MVs
    - G34.Num_MVs_per_dweling_2_MVs
    - G34.Num_MVs_per_dweling_3_MVs
    - G34.Num_MVs_per_dweling_4mo_MVs
  weights: [0, 1, 2, 3, 4]
denominator:
  expression: field
  field: G34.Num_MVs_per_dweling_Tot
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

## Why this numerator

`G34` reports occupied dwellings binned by vehicle count: 0 / 1 / 2 /
3 / 4-or-more / not-stated. There's no "total vehicles" column —
that's a derived quantity. We compute it as the weighted sum
`0 × dwellings_with_0_MVs + 1 × dwellings_with_1_MV + 2 × ... +
4 × dwellings_with_4_or_more_MVs`. The "4-or-more" bucket gets
weight 4, which under-counts very-high-vehicle households (mostly
rural / commercial / farm dwellings). This matches the ABS
publication convention; an alternative weight of 5 nudges the
national average up by ~1-2% and is reasonable for fuel-demand
modelling that emphasises long-tail mileage.

## Why this denominator

`G34.Num_MVs_per_dweling_Tot` is the count of dwellings with a known
number of motor vehicles (excluding `Num_MVs_NS`, which is the
"not stated" suppression bucket). The mean is over dwellings that
actually responded, not over the full enumeration. The alternative
`G34.Total_dwelings` (sic — the ABS column has one "l") includes
the not-stated bucket, which would conflate "dwelling with 0
vehicles" and "dwelling whose vehicle count is unknown".

## Edge cases

- **Zero denominator** → null. Industrial / no-resident SA2s.
- **Perturbation** — sub-totals may not exactly sum to
  `Num_MVs_per_dweling_Tot`.
- **Bounds** — values > 10 are almost certainly noise; values 4-6
  appear in low-density rural SA2s with multiple farm vehicles.

## Bounds (typical)

National average ~1.7 vehicles/dwelling. Inner-city low (~0.9),
outer-suburban ~2.0, rural can be 2.5+.

## Sources

- ABS Census Dictionary, NPRD / VEHRD variables
- 2021 Census product release guide
- Real-data schema check: `tests/fixtures/gcp-schemas/G34.txt`
