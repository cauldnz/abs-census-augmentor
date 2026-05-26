---
id: pct_renters
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp
default: false
tags: [housing, tenure, renters]
numerator:
  expression: field
  field: G37.R_Tot_Total
denominator:
  expression: field
  field: G37.Total_Total
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

`G37` is the GCP table "Tenure and Landlord Type by Dwelling
Structure". It is implicitly OPD-scoped: tenure is only meaningful
for occupied dwellings, so the published table only covers those.
`G37.Total_Total` is the row total across every tenure type
(owned-outright + owned-with-mortgage + the eight rental sub-types
+ other-tenure + tenure-not-stated) for every dwelling structure
(separate-house + semi-detached + flat/apartment + other +
not-stated). Using it as the denominator gives the OPD-scoped rate
the PRESET intends.

## Why this numerator

`G37.R_Tot_Total` is the row total for the "Rented" tenure block,
summing across all eight rental sub-types (real-estate-agent,
state-housing-authority, community-housing-provider,
person-not-in-same-household, other-landlord-type, landlord-type-
not-stated). That's the right population for "is the dwelling
rented?" without committing to a specific landlord type.

## Edge cases

- **Zero denominator** → null. Industrial / national-park SA2s with
  no occupied private dwellings.
- **Suppressed source counts** → ABS perturbation can make
  category-totals not sum to `Total_Total` exactly. Don't assert
  equality.

## Bounds (typical, not theoretical)

National average ~31%. Inner-city SA2s sit 50–70%; outer-suburban
owner-occupied SA2s 10–25%. Values > 90% are rare and usually
indicate small-area noise.

## Sources

- ABS Census Dictionary, TEND variable: tenure type
- 2021 Census product release guide
- Real-data schema check: `tests/fixtures/gcp-schemas/G37.txt`
