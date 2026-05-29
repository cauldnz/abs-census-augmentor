---
id: pct_commonwealth_rent_assistance_recipients
status: proposed
output_kind: percentage
bounds: [0, 100]
dataset: [dss_payments, erp_by_sa2]
default: false
tags: [welfare, housing, rent, cross-dataset]
numerator:
  expression: field
  field: DSS.commonwealth_rent_assistance_recipients
denominator:
  expression: field
  field: ERP.population_total
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://data.gov.au/data/dataset/dss-payment-demographic-data
    note: Quarterly DSS recipient counts at SA2; "Commonwealth Rent Assistance" column total.
  - url: https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
    note: ABS 3218.0 DS0003 — total resident population denominator.
---

# pct_commonwealth_rent_assistance_recipients

Share of an SA2's resident population receiving Commonwealth Rent
Assistance (CRA). CRA is a supplementary payment paid to recipients
of a qualifying income-support payment (Age Pension, JobSeeker,
DSP, Parenting Payment, Family Tax Benefit, etc.) who pay rent above
a threshold in the private rental market.

Useful as a measure of welfare-supported private-rental concentration
in an SA2 — high values point to SA2s with both significant rental
populations *and* a high share on a qualifying primary payment.

## Why this denominator

`ERP.population_total` (all of population) is the unambiguous
denominator for a "share of residents" metric. CRA has no
fixed-age-band eligibility — recipients span from young adults on
Youth Allowance through retirees on the Age Pension — so any
restricted-age denominator would introduce arbitrary distortion.

A narrower "renters" denominator (e.g. GCP `G37.R_Tot_Total`) would
give a different and arguably more useful metric — "share of renters
on CRA" — but those two source datasets (DSS quarterly vs GCP
five-yearly Census) can be on different reference periods, with the
mismatch growing as the Census ages. Resident-population is the
robust choice.

## Why not "households renting" from GCP

Same reason — period-mismatch between DSS (quarterly, current) and
GCP Census (every 5 years) means a Census-derived denominator can be
several years stale, especially in growth-corridor SA2s where the
rental population has shifted materially since the last Census. A
future revision could compute a separate `pct_cra_among_renters`
PRESET using both GCP rentals + an ERP-current scaling — out of
scope for v1.

## Edge cases

- **Zero denominator**: rare; non-substantive pseudo-SA2s.
- **Suppressed DSS counts**: SA2s with fewer than ~20 CRA recipients
  have the source value suppressed to null.
- **Double-counting concern**: CRA is paid *in addition to* a
  qualifying primary payment. The same person is a recipient of
  (e.g.) JobSeeker AND CRA. So if you sum
  `pct_jobseeker_recipients + pct_cra_recipients` you're partially
  double-counting. The composite `welfare_density_index` includes
  CRA in its sum exactly for this reason — it's a density measure,
  not unique-headcount.
- **Public-housing tenants not included**: CRA only applies to
  *private*-rental payments. Public-housing tenants pay subsidised
  rent and don't qualify; they're absent from this numerator.
  This is meaningful in SA2s with significant public-housing
  concentration (parts of inner Sydney, Adelaide, Tasmania) where
  the absence understates total rental-welfare exposure.
- **Perturbation**: DSS counts subject to ABS perturbation; treat
  as accurate to ±5%.

## Notes / config knobs

None — the calculation is fixed. For a "share of renters" framing
rather than "share of population", combine
`DSS.commonwealth_rent_assistance_recipients` with
`G37.R_Tot_Total` directly in your config — accepting the period-
mismatch caveat above.

## Bounds (typical, not theoretical)

Theoretical range is 0-100%. National rate sits around 5-7% of
all residents. Higher in metro fringe SA2s with concentrated
income-support populations (10-15%); lower in high-income inner-
city SA2s (1-3%) and in remote / Indigenous-community SA2s where
public-housing dominates rental tenure.

## Sources

- Numerator: DSS Payment Demographic Data quarterly release —
  https://data.gov.au/data/dataset/dss-payment-demographic-data
- Denominator: ABS Regional Population (3218.0 DS0003) —
  https://www.abs.gov.au/statistics/people/population/regional-population/latest-release
