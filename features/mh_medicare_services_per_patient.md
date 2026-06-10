---
id: mh_medicare_services_per_patient
status: proposed
output_kind: ratio
bounds: [0, 60]
dataset: aihw_mh_medicare
default: false
tags: [mental-health, medicare, mbs, treatment-intensity, aihw, downscale]
numerator:
  expression: field
  field: AIHW_MBS.mh_medicare_services_count
denominator:
  expression: field
  field: AIHW_MBS.mh_medicare_patients_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
    note: AIHW NMHSPF Medicare-subsidised MH services, SA4. Both fields from the same dataset.
---

# mh_medicare_services_per_patient

Mean number of Medicare-subsidised mental-health services received per
patient per year, at the SA4 level AIHW publishes — the
**treatment-intensity** companion to the dataset's population-rate
columns (`mh_medicare_patient_rate_per_1000`). This is the metric the
`aihw_mh_medicare` dataset spec flags as its suggested derived feature.

Where the patient rate measures *access breadth* (how much of the
population touches Medicare-funded MH care), this measures *depth of
contact* (how many MBS-item services each patient receives — e.g. a
handful of GP mental-health-plan reviews vs an extended course of
psychologist sessions).

## Why this numerator / denominator

`AIHW_MBS.mh_medicare_services_count` (MBS MH-specific services provided
in the SA4 over the financial year) divided by
`AIHW_MBS.mh_medicare_patients_count` (distinct patients who received at
least one). Both are the `All providers` headline counts from the same
AIHW Medicare CSV, so the ratio is self-contained and population-free.

One of the AIHW "intensity per patient" PRESET family alongside
`mh_prescriptions_per_patient`, `mh_community_contacts_per_patient`, and
`mh_admitted_avg_length_of_stay`.

## Cross-level note (SA4 → SA2)

Both inputs are SA4-native, downscaled to SA2 by inheritance, so the
ratio is identical for every SA2 within an SA4 — an SA4-level intensity
surfaced on SA2 rows, not an SA2-level estimate.

## Edge cases

- **Zero / null denominator** → null (SA4 with no published patients, or
  an SA2 mapping to an unpublished SA4).
- **Perturbation** — ratio of two perturbed counts; noisier in
  low-population SA4s.
- **Bounds** — `[0, 60]` is a warn-only sanity band. The realistic mean
  is well under 20 services per patient per year; higher values warrant
  a look at the underlying counts.

## Bounds (typical, not theoretical)

National mean is in the mid-single digits to low teens of MBS MH
services per patient per year, varying with provider mix (GP-led plans
vs psychologist/psychiatrist courses of care).

## Sources

- AIHW NMHSPF Regional activity data:
  https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- Both inputs documented in `datasets/aihw_mh_medicare.md`
  (namespace `AIHW_MBS`); this PRESET is its spec-suggested derived
  feature.
