---
id: pct_youth_allowance_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, youth, working-age, cross-dataset]
numerator:
  expression: sum
  fields:
    - DSS.youth_allowance_other_recipients
    - DSS.youth_allowance_student_and_apprentice_recipients
denominator:
  expression: field
  field: ERP.population_15_64
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; sums both Youth Allowance streams.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — working-age (15-64) population share applied to total ERP.
---

# pct_youth_allowance_recipients

Share of an SA2's working-age residents on Youth Allowance (jobseekers
+ students/apprentices combined). Youth Allowance is the under-22
analogue of JobSeeker / Austudy — recipients are either
looking for work (Other) or enrolled in full-time study /
apprenticeship (Student/Apprentice).

Useful as a sister metric to `pct_jobseeker_recipients` — together
the two cover the principal income-support incidence among
working-age residents.

## Why this numerator (sum of both streams)

The two streams answer related questions about young Australians'
economic transition — one out-of-employment, one in
education/apprenticeship — but the combined number is the cleanest
"Youth Allowance incidence" headline. Users wanting the breakdown
should reference the underlying DSS fields directly.

## Why this denominator

`ERP.population_15_64` is the working-age pool. The Youth Allowance
eligibility band (15-21 inclusive, with extensions for
students/apprentices up to 24) sits entirely within 15-64 — so the
denominator is *significantly* over-inclusive (only ~10-12% of the
working-age band is in the YA eligibility window).

This gives a *very small* number (typically 0.5-2% across SA2s). For
internal SA2-to-SA2 comparison that's fine — the bias is uniform. For
absolute interpretation, you'd want a tighter 15-24 denominator,
which the broad-age-group bands ABS publishes don't give us without
single-year ages.

A future revision could narrow the denominator if/when single-year
age columns are wired up — tracked in BACKLOG.

## Why not `ERP.population_total`

Total population would make the metric numerically even smaller
without adding signal. Working-age aligns with how `pct_jobseeker_*`
and `pct_disability_*` are calculated, so the family of welfare-
incidence PRESETs is comparable.

## Edge cases

- **Zero denominator**: rare; mostly non-substantive pseudo-SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 recipients of
  *either* stream → that stream's value is null. The sum propagates
  null when either is null. For small-population SA2s, the Other
  stream is often below the suppression threshold even when the
  Student/Apprentice stream isn't.
- **JobSeeker overlap (age 22+)**: at age 22, recipients transition
  off Youth Allowance to JobSeeker (or Austudy/ABSTUDY for ongoing
  students). This PRESET is the under-22 analogue of
  `pct_jobseeker_recipients` — they're complementary, not
  overlapping.
- **Perturbation**: DSS counts subject to ABS perturbation;
  sums-of-two have roughly ±sqrt(2) × per-count error. Treat as
  accurate to ±5-7%.

## Notes / config knobs

None — the calculation is fixed. To get just the jobseeker
(`Other`) component (the "youth unemployment" cut), reference
`DSS.youth_allowance_other_recipients` directly with
`ERP.population_15_64` in your config.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National rate sits around 1-2% of
working-age (would be ~10% if denominator were tightened to 15-24).
Higher in university-town SA2s where students/apprentices congregate.

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
