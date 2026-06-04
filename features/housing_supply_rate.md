---
id: housing_supply_rate
status: proposed
output_kind: rate
bounds: null
scale: 1000
dataset: [abs_building_approvals, erp_by_sa2]
default: false
tags: [housing, construction, supply, cross-dataset]
numerator:
  expression: field
  field: ABS_BA.total_dwellings_count
denominator:
  expression: field
  field: ERP.population_total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
    note: ABS Building Approvals (cat 8731.0) SA2 cube — total new dwelling approvals (houses + other residential) over the reference financial year.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
    note: ABS Regional Population (3218.0 DS0003) — total resident population denominator.
---

# housing_supply_rate

New dwelling approvals per 1,000 residents over the reference financial
year. The headline "is this SA2 building enough housing for its
population?" metric — high values flag SA2s adding housing stock fast
relative to their population; low values flag stagnant or
supply-constrained areas.

The `scale: 1000` front-matter multiplies the raw
`total_dwellings_count / population_total` ratio by 1,000 so the output
reads as a natural per-1,000-residents rate (e.g. `5.2`) rather than a
tiny raw ratio (`0.0052`).

## Why this numerator

`ABS_BA.total_dwellings_count` is the sum of new house approvals and new
other-residential (apartments / units / townhouses) approvals — i.e.
all *new dwelling* approvals. It excludes alterations / additions and
non-residential building, which aren't net additions to dwelling stock.
That's the right numerator for a "housing supply" rate: it counts new
homes, not renovations or commercial floor space.

## Why this denominator

`ERP.population_total` gives the per-capita framing every housing-supply
indicator uses. Dwelling approvals scale with population, so normalising
by resident population makes cross-SA2 comparison meaningful — a remote
SA2 with 200 approvals and 2,000 residents is building far faster
(100 per 1,000) than a metro SA2 with 200 approvals and 40,000 residents
(5 per 1,000).

## Why approvals, not completions

ABS publishes building *approvals* monthly with ~6-week lag; *completions*
lag 12-24 months and are harder to attribute to small areas. Approvals
are the leading indicator — they tell you what's about to be built. For a
forward-looking supply signal, approvals are the right series. (A future
PRESET could pair this with a completions series if one is wired up.)

## Edge cases

- **Zero denominator**: an SA2 with no estimated residents yields null
  (not divide-by-zero). Same minimal-residence / pseudo-SA2s as the
  other PRESETs.
- **Zero numerator**: an SA2 with no dwelling approvals over the FY
  yields `0.0` — a real and common value (most established inner SA2s
  approve very few new dwellings in any given year). Distinguish from
  null (no population data).
- **Reference-period mismatch**: the ABS BA numerator is a financial-year
  total; the ERP denominator is a point-in-time estimate. They're aligned
  to the same FY by default but a one-period skew is possible if releases
  are out of step. The effect on the rate is sub-percent and uniform.
- **Bounds**: no fixed upper bound. Greenfield-development SA2s on metro
  fringes can exceed 100 per 1,000 in a peak building year.

## Notes / config knobs

`scale: 1000` is baked in. For a raw dwellings-per-resident ratio,
reference `ABS_BA.total_dwellings_count` and `ERP.population_total`
directly in your config and divide them yourself, or author a sibling
PRESET with `scale: 1.0`.

This PRESET uses the **SA2-native** `abs_building_approvals` dataset.
The LGA-source variant (`abs_building_approvals_lga`) downscales LGA
totals to SA2 by area share; for a housing-supply rate against that
source, swap the numerator to `ABS_BA_LGA.total_dwellings_count` in a
sibling PRESET.

## Bounds (typical, not theoretical)

- Established inner-metro SA2s: 1-5 per 1,000
- Median Australian SA2: ~5-10 per 1,000
- Growth-corridor / greenfield SA2s: 30-100+ per 1,000
- Extreme single-development-year SA2s: 100+ per 1,000

A value above 150 should prompt a sanity check — usually a very small
SA2 where a single large development dominates a low population base.

## Sources

- Numerator: ABS Building Approvals (8731.0) SA2 cube —
  https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia
- Denominator: ABS Regional Population (3218.0 DS0003) —
  https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
