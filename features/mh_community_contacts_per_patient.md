---
id: mh_community_contacts_per_patient
status: proposed
output_kind: ratio
bounds: [0, 200]
dataset: aihw_mh_community
default: false
tags: [mental-health, community-health, treatment-intensity, aihw, downscale]
numerator:
  expression: field
  field: AIHW_CMH.mh_community_contacts_count
denominator:
  expression: field
  field: AIHW_CMH.mh_community_patients_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
    note: AIHW NMHSPF community mental health care, SA4. Both fields from the same dataset.
---

# mh_community_contacts_per_patient

Mean number of community (ambulatory) mental-health service contacts per
patient per year, at the SA4 level AIHW publishes — the
**treatment-intensity** companion to the dataset's population-rate
columns (`mh_community_patients_per_10000`). This is the metric the
`aihw_mh_community` dataset spec flags as its suggested derived feature.

Community MH care is where the most intensive ongoing treatment happens:
people with serious, persistent mental illness can have dozens of
contacts a year (case management, medication review, crisis support).
So this ratio carries more signal than its hospital-side siblings — a
high value points to a cohort with high-acuity, high-touch needs, not
just broad access.

## Why this numerator / denominator

`AIHW_CMH.mh_community_contacts_count` (community MH service contacts
provided in the SA4 over the financial year) divided by
`AIHW_CMH.mh_community_patients_count` (distinct patients seen). Both are
the `Total`/`Total` headline counts from the same AIHW community MH CSV,
so the ratio is self-contained and population-free.

One of the AIHW "intensity per patient" PRESET family alongside
`mh_prescriptions_per_patient`, `mh_medicare_services_per_patient`, and
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
- **Bounds** — `[0, 200]` is a deliberately wide warn-only band:
  community MH contacts per patient run far higher than the other
  settings (frequent ongoing contact for serious mental illness),
  realistically in the low-to-mid tens. The wide upper bound avoids
  spurious warnings for genuinely high-acuity SA4s while still catching
  obvious data anomalies.

## Bounds (typical, not theoretical)

National mean is in the mid-teens of contacts per patient per year, with
substantial variation — SA4s serving populations with more severe,
persistent illness sit well above areas dominated by lower-acuity care.

## Sources

- AIHW NMHSPF Regional activity data:
  https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- Both inputs documented in `datasets/aihw_mh_community.md`
  (namespace `AIHW_CMH`); this PRESET is its spec-suggested derived
  feature.
