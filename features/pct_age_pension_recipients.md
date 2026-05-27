---
id: pct_age_pension_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, ageing, demographics, cross-dataset]
numerator:
  expression: field
  field: DSS.age_pension_recipients
denominator:
  expression: field
  field: ERP.population_65_plus
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; "Age Pension" column total.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — broad-age-group share applied to total ERP to recover 65+ count.
---

# pct_age_pension_recipients

Share of an SA2's 65-and-over residents who receive the Age Pension. The
canonical "retirement reliance" metric — high values point to SA2s where
older residents lean heavily on the public pension rather than
superannuation, private income, or workforce participation past 65.

## Why this denominator

`ERP.population_65_plus` is the eligible-age population for the Age
Pension in the same reference year as the DSS numerator. Using it
gives the share of *eligible-age* residents on the pension, which is
the cleanest "uptake" measure.

The Age Pension qualifying age is 67 (fully phased in since
July 2023), so the 65+ denominator is mildly over-inclusive: an SA2
with a high share of 65-66 year-olds will appear to have a lower
uptake than it truly does for the eligible-age cohort. ABS doesn't
publish a 67+ broad band, so 65+ is the best available denominator
without dropping down to single-year ages (a much heavier parse).

The downward bias is small at the SA2 level (~5 percentage points in
the extreme) and uniform across SA2s, so cross-SA2 comparisons stay
valid.

## Why not `ERP.population_total`

Total population includes everyone — including the under-65 majority
who can't receive Age Pension. The headline number would be small and
mostly track the SA2's age structure rather than pension uptake itself.

## Why not GCP `Age_65_yr_above_P`

The 2021 Census reference date was 2021-08-10. The Age Pension
recipient count moves quarterly; pinning the denominator to a Census
snapshot from 4+ years ago would produce stale uptake rates in any
SA2 with non-trivial age-cohort drift. The ERP-derived 65+ count is
re-baselined every release, so this PRESET stays current.

## Edge cases

- **Zero denominator**: an SA2 with no estimated 65+ residents yields
  null (not a divide-by-zero error). Real-world examples: a handful
  of industrial / minimal-residence SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 recipients have
  the source value suppressed to null. The PRESET propagates null.
- **Perturbation**: DSS counts are subject to ABS perturbation for
  small-cell privacy. Values are nominal; treat as accurate to ±5%.
- **Boundary-edge SA2s**: "No usual address", "Migratory, offshore
  and shipping" pseudo-SA2s aren't included in either source — they
  drop out of the result naturally.

## Notes / config knobs

None — the calculation is fixed. If you want a different denominator
(e.g. all-of-population, GCP-derived 65+, or true 67+ from single-year
age bands when those land), reference the numerator field directly
in your config rather than using this PRESET.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. The national average sits around
60-70% — most retirement-age Australians do receive the Age Pension,
though increasingly drawing partial rates because of accumulated
super. Outliers above 90% point to high-disadvantage retirement
SA2s; below 30% point to SA2s with high private-income retirees
(Sydney's eastern suburbs, Mosman, Toorak).

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
