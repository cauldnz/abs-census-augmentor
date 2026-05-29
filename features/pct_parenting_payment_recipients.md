---
id: pct_parenting_payment_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, families, working-age, cross-dataset]
numerator:
  expression: sum
  fields:
    - DSS.parenting_payment_single_recipients
    - DSS.parenting_payment_partnered_recipients
denominator:
  expression: field
  field: ERP.population_15_64
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; sums both Parenting Payment streams.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
    note: ABS 3235.0 DS0002 — working-age (15-64) population share applied to total ERP.
---

# pct_parenting_payment_recipients

Share of an SA2's working-age (15-64) residents on Parenting Payment
(single + partnered combined). Parenting Payment is the principal
income-support payment for the primary carer of a young child;
"single" recipients are sole carers (eligible until youngest child
turns 14), "partnered" recipients are in a couple (eligible until
youngest child turns 6).

Useful as a proxy for SA2-level concentration of low-income families
with young children — a denominator-aware companion to
`pct_one_parent_family` (which is GCP-derived and a different
question: family structure, not income-support uptake).

## Why this numerator (sum of single + partnered)

The two Parenting Payment streams answer different questions
individually (sole-parent welfare reliance vs couple-parent welfare
reliance), but for an SA2-level "parenting-payment incidence" headline
the sum captures both. Users wanting the breakdown should reference
the underlying DSS fields directly.

## Why this denominator

`ERP.population_15_64` is the working-age pool. Parenting Payment
eligibility has no explicit age cap — recipients aged 65+ exist (a
grandparent caring for a young grandchild) but are vanishingly rare;
the under-15 lower bound is implicit (you can't be the primary carer
of a child if you're still a child yourself).

## Why not "families with children" from GCP

GCP `G29` gives counts of families with dependent children, but
Parenting Payment is a *person-based* count — a couple with three
children counts as one Parenting Payment Partnered recipient (the
nominated primary carer), not three. Mixing the two denominators
would conflate "share of family units on Parenting Payment" with
"share of children whose parent gets Parenting Payment", which are
materially different.

The 15-64 resident-population denominator is the consistent base
across the welfare-incidence PRESETs in this family
(`pct_jobseeker_recipients`, `pct_disability_support_pension_recipients`).

## Edge cases

- **Zero denominator**: rare; mostly the non-substantive pseudo-SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 recipients of
  *either* stream have the source value suppressed to null. The sum
  propagates null when either is null. For low-population SA2s the
  partnered count is often below the suppression threshold even
  when the single count isn't.
- **Couples both eligible**: only the nominated primary carer is the
  Parenting Payment recipient. The other parent may be on a
  different payment (JobSeeker, Carer Payment, etc.) but isn't
  double-counted here.
- **Perturbation**: DSS counts subject to ABS perturbation;
  sums-of-two have roughly ±sqrt(2) × per-count error. Treat as
  accurate to ±5-7%.

## Notes / config knobs

None — the calculation is fixed. To get just the sole-parent
component, reference `DSS.parenting_payment_single_recipients`
directly with `ERP.population_15_64` denominator in your config.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National rate sits around 1-2% of
working-age. Substantial regional variation: lower-income outer-metro
SA2s often 3-5%; high-income inner-city SA2s well below 1%
(higher-income families are far less likely to qualify for income
support even when sole-carer).

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population by Age and Sex (3235.0) —
  https://www.abs.gov.au/statistics/people/population/regional-population-age-and-sex/latest-release
