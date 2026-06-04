---
id: mean_dwelling_approval_value
status: proposed
output_kind: scalar
bounds: null
scale: 1000
dataset: abs_building_approvals
default: false
tags: [housing, construction, value, affordability]
numerator:
  expression: sum
  fields:
    - ABS_BA.value_new_houses
    - ABS_BA.value_new_other_residential_building
denominator:
  expression: field
  field: ABS_BA.total_dwellings_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
    note: ABS Building Approvals (cat 8731.0) SA2 cube — estimated value of new residential building (houses + other residential), in $'000, divided by new dwelling count.
---

# mean_dwelling_approval_value

Mean estimated construction value of a new dwelling approved in the SA2
over the reference financial year, **in dollars**. A coarse signal of
the build cost / quality / size mix of new housing — high values flag
SA2s building large detached houses or premium apartments; low values
flag modest-cost housing.

The ABS value columns are published in `$'000` (thousands of dollars).
The `scale: 1000` front-matter converts the per-dwelling mean from
`$'000` back to dollars, so the output reads as a natural dollar amount
(e.g. `485000`) rather than thousands (`485`).

## What this is

`(value_new_houses + value_new_other_residential_building) /
total_dwellings_count`, scaled to dollars. The numerator is the total
estimated construction value of *new residential* building (houses plus
apartments / units / townhouses); the denominator is the count of new
dwellings. The result is the value-weighted mean cost to build one new
dwelling in the SA2.

## What this is NOT

- **Not a sale price or market value.** ABS "value" is the estimated
  *cost of construction* declared on the building approval, not what the
  finished dwelling sells for. Land cost is excluded. So this tracks
  build cost, not housing affordability or property prices.
- **Not a per-house figure.** The numerator mixes house and
  other-residential value, and the denominator mixes both dwelling
  types. In an SA2 building both detached houses (high per-unit value)
  and apartments (lower per-unit value), the mean blends them. Pair with
  `pct_apartment_approvals` to interpret: a low mean value + high
  apartment share is consistent with modest apartments; a high mean
  value + low apartment share is consistent with large detached houses.

## Why exclude alterations and non-residential

`value_alterations_additions_conversions` and
`value_non_residential_building` aren't new-dwelling construction, so
including them would corrupt the "mean cost per new dwelling" framing.
Only the two new-residential value streams (houses + other residential)
go into the numerator, matched to the `total_dwellings_count`
denominator.

## Edge cases

- **Zero denominator**: an SA2 with no new dwelling approvals yields null
  (no dwellings → no mean cost). Common for low-activity SA2s.
- **Value present, count zero**: shouldn't happen for new residential
  (value and count move together), but if a release has a value with a
  suppressed/zero count, the null-denominator rule yields null rather
  than an infinite mean.
- **Mixed-dwelling blending**: see "What this is NOT" — interpret
  alongside `pct_apartment_approvals`.
- **Perturbation**: ABS BA SA2 values are raw (no perturbation), so the
  mean is exact given the published value + count.

## Notes / config knobs

`scale: 1000` converts `$'000` → dollars. To keep the value in `$'000`
(matching ABS's native unit), author a sibling PRESET with `scale: 1.0`,
or reference the underlying `ABS_BA.value_*` and
`ABS_BA.total_dwellings_count` fields directly.

Uses the SA2-native `abs_building_approvals` dataset. LGA-source variant
via `ABS_BA_LGA.*` fields.

## Bounds (typical, not theoretical)

No fixed bounds. Empirically (2024-25-era build costs):

- Modest apartment-heavy SA2s: $250,000-$400,000
- Median Australian SA2: ~$400,000-$550,000
- Large-detached-house / premium SA2s: $600,000-$1,000,000+

A value below $150,000 or above $2,000,000 should prompt a sanity check
— usually a very-low-count SA2 where one atypical approval dominates.

## Sources

- ABS Building Approvals (8731.0) SA2 cube —
  https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
