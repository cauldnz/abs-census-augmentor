---
id: welfare_density_index
status: proposed
output_kind: ratio
bounds: null
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, composite, cross-dataset]
numerator:
  expression: sum
  fields:
    - DSS.age_pension_recipients
    - DSS.jobseeker_payment_recipients
    - DSS.disability_support_pension_recipients
    - DSS.parenting_payment_single_recipients
    - DSS.parenting_payment_partnered_recipients
    - DSS.carer_payment_recipients
    - DSS.youth_allowance_other_recipients
    - DSS.youth_allowance_student_and_apprentice_recipients
    - DSS.commonwealth_rent_assistance_recipients
denominator:
  expression: field
  field: ERP.population_total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; nine principal payment types summed.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
    note: ABS 3218.0 DS0003 — total resident population denominator.
---

# welfare_density_index

A composite "income-support intensity" measure: the ratio of total
payment-recipient counts (summed across nine principal DSS payment
types) to the SA2's total resident population.

**This is a recipient-density index, not a unique-headcount measure.**
Recipients of multiple payments (e.g. an Age Pensioner also receiving
Commonwealth Rent Assistance) are counted once per payment. The
resulting ratio can exceed 1.0 in SA2s with concentrated welfare
reliance, but most SA2s land between 0.05 and 0.30.

Useful as a single-number proxy for an SA2's overall reliance on
income-support transfers — pairs well with SEIFA disadvantage indexes
for visualisations / quick filters.

## Why this denominator

`ERP.population_total` is the unambiguous all-of-population denominator
for a "density" metric. Working-age or eligible-age would give cleaner
per-payment ratios but the index's role is a coarse top-line, so the
broadest possible denominator is the right call.

## Why not a unique-headcount denominator

DSS publishes recipient counts per payment type, not per person. To
get unique-headcount welfare incidence would require a person-level
data linkage (PLIDA, MADIP) which isn't accessible at SA2 level on
the open ABS portal. The sum-of-recipients framing is what's
available; the spec's name (`*_density_index`, not
`pct_population_on_welfare`) signals this.

## Why these nine payments

They're the principal income-support and family payments DSS
publishes consistently at SA2 level across the v1.5+ release range:

1. Age Pension
2. JobSeeker Payment
3. Disability Support Pension
4. Parenting Payment (Single)
5. Parenting Payment (Partnered)
6. Carer Payment
7. Youth Allowance (Other)
8. Youth Allowance (Student / Apprentice)
9. Commonwealth Rent Assistance

DSS publishes additional payment-related counts (Pensioner Concession
Card holders, Health Care Card holders, Family Tax Benefit, etc.) but
those are *concession / supplementary* rather than direct income
support, and double-count even more aggressively with the nine above
(an Age Pensioner usually has a Pensioner Concession Card). Including
them would inflate the index without adding signal.

## Edge cases

- **Zero denominator**: yields null. Same boundary-edge SA2s as the
  other PRESETs.
- **Suppressed DSS counts**: SA2s with any single payment type
  suppressed (count <20 → null) have that contribution missing from
  the sum. The PRESET propagates null for the entire SA2 in this
  case (any null in the sum makes the sum null). This is a
  meaningful limitation for low-population SA2s where ~3-5 of the
  nine payment types might be suppressed routinely; the index
  works best for SA2s with a few hundred residents and up.
- **Negative outliers don't exist**: counts are zero-bounded. A zero
  result means *no* recipients on any of the nine payments — almost
  unheard of in substantive SA2s.
- **Perturbation**: each underlying count is subject to ABS
  perturbation. The sum's perturbation accumulates roughly as
  ±sqrt(9) × per-count error, so treat values to two decimal
  places at most.

## Notes / config knobs

None. To customise (drop a payment type, narrow to working-age
recipients, etc.) reference the individual `DSS.*_recipients` and
`ERP.population_total` fields in your config rather than this PRESET.

## Bounds (typical, not theoretical)

Theoretical range is 0 upwards (no fixed upper bound — recipients
of multiple payments can sum past population). Empirically:

- Inner-metro high-income SA2s: ~0.05-0.10
- Median Australian SA2: ~0.15-0.25
- Disadvantaged outer-metro / remote SA2s: ~0.30-0.50
- Extreme cases (very high welfare reliance): 0.5+

A value above 1.0 should prompt a sanity check — usually indicates
a very small / atypical SA2 where one or two recipient cohorts
dominate the resident population.

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population (3218.0 DS0003) —
  https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
