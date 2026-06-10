---
id: pct_businesses_non_employing
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: abs_business_counts
default: false
tags: [economy, business, small-business, structure]
numerator:
  expression: field
  field: ABS_CAB.business_count_non_employing
denominator:
  expression: field
  field: ABS_CAB.business_count_total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
    note: ABS Counts of Australian Businesses (cat 8165.0) DC8 cube — non-employing businesses and total businesses per SA2, both summed across industry divisions.
---

# pct_businesses_non_employing

Share of an SA2's actively trading businesses that are **non-employing**
(sole traders / owner-operators with no employees) — a structural signal
about the *kind* of business base, not just its size.

A high share points to a local economy dominated by sole traders,
contractors, freelancers, and the gig / owner-operator end of the
spectrum; a low share points to a base weighted toward employing firms.
Two SA2s with identical `businesses_per_1000_residents` can have very
different economic character depending on this split.

## Why this numerator / denominator

`ABS_CAB.business_count_non_employing` over `ABS_CAB.business_count_total`
— both from the same ABS Counts of Australian Businesses cube, summed
across industry divisions, so the ratio is self-contained and needs no
external denominator. The remaining `business_count_*_employees` bands
make up the employing complement.

## Edge cases

- **Zero / null denominator** → null. An SA2 with no businesses (rare
  pseudo-SA2s) returns null rather than dividing by zero.
- **Perturbation** — ABS perturbs both counts independently, so the
  ratio can drift slightly in low-business-count SA2s; the summed Total
  is read from the source Total column rather than a recomputed band-sum
  (see `datasets/abs_business_counts.md`). The ratio is robust to this at
  the SA2 scale but treat small-count areas as ±a few points.
- **Out-of-bounds** — theoretically 0–100%; perturbation can occasionally
  nudge a summed numerator a hair above the summed Total in a tiny SA2
  (warn-only, not clipped).

## Bounds (typical, not theoretical)

National non-employing share sits around 60% of all businesses (the
sole-trader / owner-operator base is large). Professional-services and
tradie-heavy residential SA2s run higher; SA2s anchored by large
employers (industrial estates, hospital / university precincts) run
lower.

## Suggested companion

- `businesses_per_1000_residents` — pair the *structure* (this PRESET)
  with the *density* to characterise an SA2's economic base on two axes.

## Sources

- ABS Counts of Australian Businesses (8165.0) —
  https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
