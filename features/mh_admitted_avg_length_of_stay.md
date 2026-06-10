---
id: mh_admitted_avg_length_of_stay
status: proposed
output_kind: ratio
bounds: [0, 365]
dataset: aihw_mh_admitted_patients
default: false
tags: [mental-health, hospital, length-of-stay, treatment-intensity, aihw, downscale]
numerator:
  expression: field
  field: AIHW_APC.mh_patient_days_count
denominator:
  expression: field
  field: AIHW_APC.mh_hospitalisations_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
    note: AIHW NMHSPF admitted patient care, SA4. Average length of stay (days) = patient days / hospitalisations.
---

# mh_admitted_avg_length_of_stay

Mean **length of stay**, in days, for mental-health-related admitted
patient care at the SA4 level AIHW publishes — total patient days
divided by hospitalisations (separations). This is the metric the
`aihw_mh_admitted_patients` dataset spec flags as its suggested derived
feature, and the standard hospital "average length of stay" (ALOS).

It is the hospital-side **treatment-intensity** signal: where the
per-10,000 columns measure how *often* an area's residents are admitted
for MH care, this measures how *long* each admission lasts — a short
mean (a few days) suggests acute stabilisation and rapid discharge,
while a long mean points to more sub-acute / extended inpatient care.

## Why this numerator / denominator

`AIHW_APC.mh_patient_days_count` (total bed-days for MH-related admitted
care in the SA4 over the financial year) divided by
`AIHW_APC.mh_hospitalisations_count` (separations). Both are headline
counts from the same AIHW admitted-patient-care CSV, so ALOS is
self-contained and population-free — the textbook definition.

One of the AIHW "intensity per patient/episode" PRESET family alongside
`mh_prescriptions_per_patient`, `mh_medicare_services_per_patient`, and
`mh_community_contacts_per_patient`. (This one is per *episode*, not per
patient — a single patient may have multiple separations in a year.)

## Cross-level note (SA4 → SA2)

Both inputs are SA4-native, downscaled to SA2 by inheritance, so the
ratio is identical for every SA2 within an SA4 — an SA4-level ALOS
surfaced on SA2 rows, not an SA2-level estimate.

## Edge cases

- **Zero / null denominator** → null (SA4 with no published
  hospitalisations, or an SA2 mapping to an unpublished SA4).
- **Perturbation** — ratio of two perturbed counts; noisier in
  low-population SA4s with few separations.
- **Bounds** — `[0, 365]` is a warn-only sanity band. Psychiatric ALOS
  realistically runs ~10–25 days (longer than general acute care);
  a mean above a year almost certainly signals a data anomaly. Note
  the AIHW figure counts MH-related bed-days, which can include long
  stays in specialised psychiatric units.

## Bounds (typical, not theoretical)

National MH ALOS sits in the low tens of days, materially longer than
general-acute ALOS. SA4s served by extended-care psychiatric facilities
sit higher.

## Sources

- AIHW NMHSPF Regional activity data:
  https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- Both inputs documented in `datasets/aihw_mh_admitted_patients.md`
  (namespace `AIHW_APC`); this PRESET is its spec-suggested derived
  feature.
