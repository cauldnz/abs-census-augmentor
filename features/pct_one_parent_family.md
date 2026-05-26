---
id: pct_one_parent_family
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: gcp
default: false
tags: [demographics, family]
numerator:
  expression: field
  field: G29.OPF_ChU15_a_Total_F
denominator:
  expression: sum
  fields:
    - G29.CF_ChU15_a_Total_F
    - G29.OPF_ChU15_a_Total_F
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/census/guide-census-data/2021-census-product-release-guide
    note: G29 = Family Composition
---

# pct_one_parent_family

Share of families with children under 15 that are one-parent
families.

## Why this numerator

`G29.OPF_ChU15_a_Total_F` is the row total of "One Parent Family
with at least one child under 15", summing across the four
combinations of dependent-students and non-dependent children that
G29 breaks the OPF block into. That's the right population for the
"is this a one-parent family with kids?" question.

## Why this denominator

Families *with children under 15* — couples without kids and other
family types are not the population this rate is well-defined over.
We sum `CF_ChU15_a_Total_F` (couple families with children under 15)
and `OPF_ChU15_a_Total_F` (one-parent families with children under
15), so the denominator is exactly "all families that have at least
one child under 15".

`G29` also carries an `Other_family` row total, but it's not
broken down by child-presence, so we exclude it from the
denominator. (Other-family households without children-under-15
shouldn't be in the rate's population; with-children-under-15
they should — but G29 doesn't let us pull that distinction out.)

## Why not `G29.Total_F`

The grand-total `G29.Total_F` includes couple-no-children,
one-parent-no-children-under-15, and other-family households. Those
are *not* the population the rate is defined over — including them
deflates the share by ~50% nationally and makes the metric vary
mostly with childlessness rate, not with single-parenthood rate.

## Edge cases

- **Zero denominator** → null. Some industrial / national-park SA2s
  have no resident families with children under 15.
- ABS perturbation can produce category sub-totals that don't sum
  exactly.

## Bounds (typical)

National average ~16%. Higher in low-income outer-suburban and
remote SA2s (25–40%); lower in inner-city couple-with-kids SA2s
(5–10%).

## Sources

- ABS Census Dictionary, FMCF / FMSF variables
- 2021 Census product release guide
- Real-data schema check: `tests/fixtures/gcp-schemas/G29.txt`
