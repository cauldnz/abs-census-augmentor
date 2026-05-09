---
id: pct_renters
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp_2021
default: false
tags: [housing, tenure, renters]
numerator:
  expression: field
  field: G37.R_Tot
denominator:
  expression: field
  field: G37.OPDs_Total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: Confirms G37 is the 2021 GCP table for Tenure and landlord type by dwelling structure
---

# pct_renters

Share of occupied private dwellings (OPDs) that are rented (any
landlord type) on Census night.

## Why this denominator

`G37.OPDs_Total` is the count of *occupied* private dwellings — the
right base for tenure analysis, since unoccupied dwellings have no
tenure to report.

## Why not `G37.Total_dwellings`

The full G37 row total includes unoccupied dwellings, vacant
dwellings, and visitor-occupied stock. Including those in the
denominator under-counts the rental share by 5–10% in coastal /
holiday-home SA2s where unoccupied stock is a meaningful slice of
total dwellings.

## Edge cases

- **Zero denominator** → null. Industrial / national-park SA2s with
  no occupied private dwellings.
- **Suppressed source counts** → ABS perturbation can make
  category-totals not sum to `OPDs_Total` exactly. Don't assert
  equality.

## Bounds (typical, not theoretical)

National average ~31%. Inner-city SA2s sit 50–70%; outer-suburban
owner-occupied SA2s 10–25%. Values > 90% are rare and usually
indicate small-area noise.

## Sources

- ABS Census Dictionary, TEND variable: tenure type
- 2021 Census product release guide
