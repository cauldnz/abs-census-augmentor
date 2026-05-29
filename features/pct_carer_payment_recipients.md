---
id: pct_carer_payment_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, carer, working-age, cross-dataset]
numerator:
  expression: field
  field: DSS.carer_payment_recipients
denominator:
  expression: field
  field: ERP.population_15_64
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; "Carer Payment" column total.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — working-age (15-64) population share applied to total ERP.
---

# pct_carer_payment_recipients

Share of an SA2's working-age (15-64) residents on Carer Payment.
Carer Payment is an income-support payment for people providing
constant care to someone with a permanent severe disability or
medical condition. Recipients are themselves working-age and out of
the workforce because of full-time caring responsibilities — so the
incidence in an SA2 reflects both the prevalence of high-care-need
people *and* the household-level decision about who provides that
care.

## Why this denominator

`ERP.population_15_64` is the working-age population — the pool from
which Carer Payment recipients are drawn (recipients themselves are
the carers, not the cared-for). The age cap on Carer Payment is the
Age Pension qualifying age (67) at which point the carer transitions
to the Age Pension (with or without Carer Allowance, a separate
supplementary payment). The lower bound is implicit at ~16+.

The 15-64 band is slightly over-inclusive at both ends but the bias
is small (~1-2 percentage points) and uniform across SA2s, so
SA2-to-SA2 comparisons stay valid. This is the same denominator
`pct_jobseeker_recipients` / `pct_disability_support_pension_recipients`
use — the cross-PRESET comparability is the point.

## Why not population-of-cared-for-people

That framing — "share of high-care-need residents whose caring is
provided by a Carer Payment recipient rather than residential care /
informal unpaid arrangements" — would be a more interesting metric,
but it needs a denominator the augmentor doesn't have. AIHW publishes
disability-prevalence data but not at SA2 level on the open portal.
ABS GCP `G18` ("Core activity need for assistance") at SA2 is the
closest proxy but mixes age groups and isn't currently exposed as a
PRESET source.

Working-age-resident denominator is what we have; the spec is explicit
about the framing limitation.

## Why not `pct_disability_support_pension_recipients` as a sister

DSP is paid to people who *have* a disability; Carer Payment is paid
to people who *care for* someone with a disability. The two
populations are largely disjoint at the individual level (a few
edge cases — e.g. a DSP recipient who also cares for a partner with
a different disability — exist but are rare). They measure related
but distinct welfare-incidence stories.

Comparing the two PRESETs across SA2s can reveal interesting
patterns: SA2s with high DSP + low Carer Payment likely have high
residential / institutional care take-up; SA2s with high Carer
Payment + comparable DSP have more in-home family-provided care.

## Edge cases

- **Zero denominator**: rare; non-substantive pseudo-SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 Carer Payment
  recipients have the source value suppressed to null. Carer Payment
  is a smaller cohort than JobSeeker or DSP, so suppression is more
  common at the SA2 level — particularly in low-population SA2s.
  Expect ~10-20% of small SA2s to return null on this metric.
- **Concurrent payments**: Carer Payment recipients sometimes also
  receive Carer Allowance (a supplementary, not in this numerator)
  or Commonwealth Rent Assistance. The composite
  `welfare_density_index` includes Carer Payment in its sum.
- **Perturbation**: DSS counts subject to ABS perturbation; treat
  as accurate to ±5% — the relative error is larger here than for
  JobSeeker / Age Pension because Carer Payment counts per SA2 are
  smaller.

## Notes / config knobs

None — the calculation is fixed. For a "all-carers" framing that
includes Carer Allowance (a more inclusive non-income-tested payment
for carers in lower-intensity caring roles), additional source
columns would need wiring up — Carer Allowance isn't currently in
the DSS dataset spec. Could be added if there's demand.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National rate sits around 1-2% of
working-age. Substantial regional variation: lower-income outer-
metro and regional SA2s often above 3%; high-income metro SA2s
typically below 0.5% (these populations more often access paid
in-home / residential care rather than informal family caring).

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
