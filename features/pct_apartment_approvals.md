---
id: pct_apartment_approvals
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: abs_building_approvals
default: false
tags: [housing, construction, urban-form, density]
numerator:
  expression: field
  field: ABS_BA.new_other_residential_building_count
denominator:
  expression: field
  field: ABS_BA.total_dwellings_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
    note: ABS Building Approvals (cat 8731.0) SA2 cube — new other residential building (apartments / units / townhouses) as a share of total new dwelling approvals.
---

# pct_apartment_approvals

Share of an SA2's new dwelling approvals that are **other residential
building** (apartments, units, townhouses) rather than free-standing
houses. A rough urban-form / densification proxy — high values flag SA2s
where new housing supply is overwhelmingly higher-density (inner-city,
transit-oriented development corridors); low values flag detached-house
suburbia and rural areas.

## Why this denominator

`ABS_BA.total_dwellings_count` is the sum of new house approvals and new
other-residential approvals — i.e. all new dwellings. Using it gives the
clean share-of-new-dwellings split between detached and higher-density
forms. The complement (`100 − pct_apartment_approvals`) is the share that
are free-standing houses.

## Why not population or land area

This is a *composition* metric, not a density or rate. The question is
"of the new homes being approved here, what fraction are apartments?" —
which only makes sense normalised by total new dwellings, not by
population or area. For a supply-per-capita measure use
`housing_supply_rate`; for residents-per-km² use
`ERP.population_density_per_km2`.

## ABS "other residential building" scope

ABS's "other residential building" category covers all non-house
residential: apartments, flats, units, townhouses, semi-detached,
terraces, and dwellings attached to non-residential buildings. It's
*not* a pure "apartment" count — semi-detached and townhouses are
included. So this PRESET slightly over-states pure high-rise apartment
share. The name `pct_apartment_approvals` is the readable shorthand;
read it as "share of new dwellings that aren't free-standing houses".

## Edge cases

- **Zero denominator**: an SA2 with no new dwelling approvals over the FY
  yields null. Very common for established inner SA2s and rural SA2s with
  near-zero building activity in any given year — those SA2s have no
  meaningful apartment-share to report.
- **Boundary at 0 and 100**: an SA2 approving only houses reads `0`; an
  SA2 approving only apartments reads `100`. Both are real and common
  (a greenfield house estate vs an inner-city apartment-tower SA2).
- **Low-count noise**: an SA2 with only 2-3 total dwelling approvals in
  the FY produces a coarse, noisy share (0 / 33 / 50 / 67 / 100). The
  metric is most meaningful for SA2s with a few dozen approvals and up.
- **Perturbation**: ABS BA counts at SA2 are raw (no perturbation), so
  the share is exact given the published counts.

## Notes / config knobs

None. Uses the SA2-native `abs_building_approvals` dataset. For the
LGA-downscaled variant, author a sibling PRESET referencing
`ABS_BA_LGA.new_other_residential_building_count` /
`ABS_BA_LGA.total_dwellings_count`.

## Bounds (typical, not theoretical)

Theoretical 0-100%. Empirically:

- Detached-house growth corridors / rural SA2s: 0-15%
- Median Australian SA2: ~20-40%
- Inner-metro / transit-oriented SA2s: 70-95%
- Apartment-tower SA2s (Sydney / Melbourne CBD fringe): 95-100%

## Sources

- ABS Building Approvals (8731.0) SA2 cube —
  https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
