---
id: businesses_per_1000_residents
status: proposed
output_kind: rate
bounds: null
scale: 1000
dataset: [abs_business_counts, erp_by_sa2]
default: false
tags: [economy, business, density, cross-dataset]
numerator:
  expression: field
  field: ABS_CAB.business_count_total
denominator:
  expression: field
  field: ERP.population_total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
    note: ABS Counts of Australian Businesses (cat 8165.0) DC8 cube — total actively trading businesses per SA2 (summed across industry divisions).
  - url: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
    note: ABS Regional Population (3218.0) — total resident population denominator (via the erp_by_sa2 dataset).
---

# businesses_per_1000_residents

Actively trading businesses per 1,000 residents — a **business-density**
indicator that normalises the raw business count by the resident
population, making SA2s comparable regardless of size.

It separates two very different kinds of place that a raw count conflates:
a populous outer-suburban SA2 with many businesses simply *because* many
people live there, versus a commercial / CBD SA2 with a high business
count relative to its (often small) residential population. A high value
flags an area whose economic footprint outweighs its residential one.

## Why this numerator / denominator

`ABS_CAB.business_count_total` (total actively trading businesses in the
SA2, summed across all industry divisions) over `ERP.population_total`
(total estimated resident population), scaled to a per-1,000 figure so
the headline number reads naturally. The same cross-dataset
count-over-population shape as `housing_supply_rate`.

The numerator counts businesses *located* in the SA2 (by their main
business address), while the denominator counts people who *live* there —
they are deliberately different populations. That mismatch is the whole
point: it's what makes the ratio reveal commercial vs residential
character.

## Why not businesses per employed resident / per worker

A "businesses per worker" framing would arguably be cleaner, but the
augmentor's worker counts come from 5-yearly Census journey-to-work data,
not an annual series — and the resident-population denominator keeps this
PRESET comparable with the other `*_per_1000_residents` style metrics.

## Edge cases

- **Zero / null denominator** → null. Non-residential / industrial SA2s
  with ~zero resident population would otherwise divide-by-zero; they
  return null rather than an infinite density.
- **Very high values are real, not errors.** CBD and commercial-core
  SA2s legitimately reach into the thousands per 1,000 (many businesses,
  few residents). `bounds` is therefore left `null` — clamping or warning
  would fire on genuine high-density commercial areas. Interpret extreme
  values as "commercial core", not data error.
- **Perturbation** — ABS perturbs small SA2 business counts; the density
  of a low-population SA2 is correspondingly noisier.
- **Vintage mismatch** — business counts are as at 30 June of their
  reference year; ERP is the matching annual estimate. Pair compatible
  release years for the cleanest read.

## Bounds (typical, not theoretical)

National average is on the order of ~100 businesses per 1,000 residents
(roughly one business per ten people, reflecting the large non-employing
/ sole-trader share). Residential SA2s sit below that; commercial and
CBD SA2s run far higher.

## Sources

- Numerator: ABS Counts of Australian Businesses (8165.0) —
  https://www.abs.gov.au/statistics/economy/business-indicators/counts-australian-businesses-including-entries-and-exits/latest-release
- Denominator: ABS Regional Population (3218.0) via the `erp_by_sa2`
  dataset.
