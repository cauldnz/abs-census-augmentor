---
id: pct_disability_support_pension_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, disability, working-age, cross-dataset]
numerator:
  expression: field
  field: DSS.disability_support_pension_recipients
denominator:
  expression: field
  field: ERP.population_15_64
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; "Disability Support Pension" column total.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — working-age (15-64) population share applied to total ERP.
---

# pct_disability_support_pension_recipients

Share of an SA2's working-age (15-64) residents who receive the
Disability Support Pension (DSP). The headline measure of permanent /
long-term disability prevalence in the workforce — recipients have been
assessed as having a permanent physical, intellectual, or psychiatric
condition limiting capacity to work ≥15 hours/week.

## Why this denominator

`ERP.population_15_64` is the working-age band, which is the
roughly-eligible pool for DSP. DSP has a lower bound of 16 and an
upper bound at the Age Pension qualifying age (67) — so 15-64 is
mildly under-inclusive at the upper end (recipients aged 65-66
exist but small in number) and over-inclusive at the lower end (the
15 year olds are excluded from DSP). The broad-age-group bands ABS
publishes don't give us a cleaner 16-66 cut without single-year ages.

The bias is small (~1-2 percentage points) and uniform — cross-SA2
comparisons stay valid.

## Why not `ERP.population_total`

Total population includes children and retirees, who are ineligible.
Using all-of-population suppresses the metric to roughly half its
true working-age incidence and hides the SA2-to-SA2 variation that's
the interesting signal.

## Why not "labour-force population"

Recipients of DSP are mostly *outside* the labour force by definition
(they've been assessed as unable to work ≥15 hours/week). A labour-
force denominator would systematically under-count the population that
DSP draws from. Working-age resident population is the right base.

## Edge cases

- **Zero denominator**: rare; mostly the non-substantive pseudo-SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 DSP recipients
  have the source value suppressed to null. The PRESET propagates
  null.
- **Perturbation**: DSS counts subject to ABS perturbation; treat as
  accurate to ±5%.
- **Concurrent eligibility**: people receiving Carer Payment (which
  has its own DSP-like medical-evidence requirement on the cared-for
  person) are not in this numerator. See `pct_carer_payment_recipients`
  (not yet authored) if relevant.

## Notes / config knobs

None — the calculation is fixed. To include DSP + Carer Payment
together (a broader "disability-related income support" composite),
sum the two source fields in your own config rather than using this
PRESET.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National DSP incidence among working-age
sits around 4-6%, with substantial regional variation: ~1-2% in
high-employment metro SA2s where the labour-market screens favour
better-health workers, ~10-15% in disadvantaged regional SA2s with
declining heavy industries (long-term occupational injuries are a
significant DSP-pathway).

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
