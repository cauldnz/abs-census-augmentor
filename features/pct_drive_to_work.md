---
id: pct_drive_to_work
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp_2021
default: false
tags: [transport, employment, fuel-demand]
numerator:
  expression: sum
  fields:
    - G62.One_method_Car_as_driver_P
    - G62.One_method_Car_as_passenger_P
    - G62.One_method_Truck_P
    - G62.One_method_Motorbike_scootr_P
denominator:
  expression: field
  field: G62.Tot_P
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/census-dictionary/2021/variables-topic/transport/method-travel-work-mtwp
    note: MTWP variable applicability — employed persons aged 15+
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: Confirms G62 is the 2021 GCP table for Method of travel to work by sex
---

# pct_drive_to_work

Share of employed persons aged 15+ who travelled to work primarily by private
motor vehicle on Census day (Tuesday 10 August 2021).

## Why this denominator

Total population includes children, retirees, and the unemployed who are
*never* in the numerator. For an SA2 with 60% non-working-age (e.g. retirement
coast, regional areas with high non-participation), using `G01.Tot_P_P`
instead of `G62.Tot_P` will under-state by roughly the labour-force
participation rate × employment rate — which varies from ~45% in retirement
SA2s to ~70% in inner-city SA2s. Order-of-magnitude wrong, sometimes by 30+
percentage points across the cross-section.

## Why not `G62.Tot_OneMethod_P`

`G62.Tot_P` (the table total) includes "two methods", "three methods", "did
not go to work", "worked at home", and "method not stated". A person who
drove + caught the train ends up in "two methods", not in any "one method"
cell. Excluding the multi-mode commuters from the denominator inflates the
ratio — and the inflation is biased toward inner-city SA2s where multi-modal
commutes are more common, so it correlates with the wrong things.

## Edge cases

- **Zero denominator** → null. Industrial / national-park / port SA2s with
  no resident workforce produce `Tot_P = 0`.
- **Suppressed source counts** → ABS perturbation can make sub-totals not
  sum to `Tot_P` exactly. Absorb the discrepancy in the calculation; do not
  assert equality.
- **Census Day during COVID-19 lockdowns** — "Worked at home" and "Did not go
  to work" are unusually inflated for 2021. The numerator is unaffected
  (people who drove still drove), but the *interpretation* of the ratio
  shifts: it's "share of employed who drove **on lockdown Census Day**", not
  "share who normally drive". Document this in the PRESET docstring.

## Notes / config knobs

If "drive" is intended to mean *driving themselves only* (excluding car
passengers, truck and motorbike), narrow the numerator to
`G62.One_method_Car_as_driver_P` only. Worth surfacing as a PRESET parameter
(`drive_definition: "all_private_motor" | "self_driver_only"`).

## Bounds (typical, not theoretical)

National average ~56%. SA2-level distribution sits in the 30-80% range in
practice; outliers > 90% usually indicate the SA2 has fewer than ~50 employed
respondents (small-area noise, treat with caution).

## Sources

- ABS Census Dictionary, MTWP variable definition and applicability:
  https://www.abs.gov.au/census/guide-census-data/census-dictionary/2021/variables-topic/transport/method-travel-work-mtwp
- 2021 Census product release guide (table inventory):
  https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
- Real-data schema check: `tests/fixtures/gcp-schemas/G62.txt`
