---
id: pct_jobseeker_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, unemployment, working-age, cross-dataset]
numerator:
  expression: field
  field: DSS.jobseeker_payment_recipients
denominator:
  expression: field
  field: ERP.population_15_64
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; "JobSeeker Payment" column total.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — working-age (15-64) population share applied to total ERP.
---

# pct_jobseeker_recipients

Share of an SA2's working-age (15-64) residents who receive JobSeeker
Payment — the primary unemployment / underemployment support payment
since the 2020 consolidation of Newstart, Sickness Allowance, and
related payments. Higher values point to SA2s with labour-market
softness or structural unemployment.

## Why this denominator

`ERP.population_15_64` is the working-age population, which is the
roughly-eligible pool for JobSeeker. JobSeeker has an upper age cut-off
at the Age Pension qualifying age (67) and a lower bound at 22 (under
22 generally receive Youth Allowance instead) — so the 15-64 band is
slightly over-inclusive at both ends, but the broad-age-group bands
ABS publishes are what we have.

For comparative purposes (one SA2 versus another, or year-over-year
within the same SA2) the small over-inclusivity is uniform and doesn't
distort rankings.

## Why not `ERP.population_total`

Total population includes children and retirees, who are ineligible
for JobSeeker. Using all-of-population as the denominator suppresses
the metric to ~1-3% across most SA2s, hiding the variation in
working-age unemployment that's the actually interesting signal.

## Why not "labour force" from GCP

GCP labour-force columns (`G43.LF_15ov_*`) measure people who are
either employed or actively seeking work. JobSeeker recipients are
mostly inside this set — but the denominator that matches DSS's
publication framing best is the resident-population working-age
band, which is what governments themselves use when discussing
JobSeeker "incidence" at the regional level.

## Edge cases

- **Zero denominator**: an SA2 with no estimated 15-64 residents
  yields null. Rare; mostly the non-substantive pseudo-SA2s
  (industrial, no usual address, migratory, offshore).
- **Suppressed DSS counts**: SA2s with fewer than ~20 JobSeeker
  recipients have the source value suppressed to null. The PRESET
  propagates null.
- **Youth Allowance overlap**: People under 22 are usually on Youth
  Allowance, not JobSeeker. This PRESET specifically measures
  JobSeeker incidence — see `pct_youth_allowance_recipients` (not
  yet authored) for the under-22 analogue if you need that
  separation.
- **Perturbation**: DSS counts subject to ABS perturbation; treat as
  accurate to ±5%.

## Notes / config knobs

None — the calculation is fixed. To include Youth Allowance recipients
in the numerator (giving a broader "income-support among working-age"
ratio), sum the two source fields in your own config rather than
using this PRESET.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National rate sits around 3-5% of the
working-age population, with notable variation: 1% or less in
high-employment metro SA2s (Sydney CBD, Melbourne CBD, mining
boomtowns); 10-15% in disadvantaged outer-metro and regional SA2s
with weak labour markets. A 1% national shift typically moves the
median SA2 by 1-2 percentage points.

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
