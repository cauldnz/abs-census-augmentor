---
id: mh_prescriptions_per_patient
status: proposed
output_kind: ratio
bounds: [0, 60]
dataset: aihw_mh_prescriptions
default: false
tags: [mental-health, prescriptions, treatment-intensity, aihw, downscale]
numerator:
  expression: field
  field: AIHW_MHP.mh_prescriptions_count
denominator:
  expression: field
  field: AIHW_MHP.mh_patients_count
edge_cases:
  zero_denominator: null
  perturbation_tolerance: warn_only
  out_of_bounds_behaviour: warn
sources:
  - url: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
    note: AIHW NMHSPF mental-health-related prescriptions, SA4. Both fields from the same dataset.
---

# mh_prescriptions_per_patient

Mean number of mental-health-related prescriptions dispensed per
patient per year, at the SA4 level the AIHW publishes — a
**treatment-intensity** signal that complements the population-rate
columns the dataset already exposes (`mh_patient_rate_per_1000`).

Where `mh_patient_rate_per_1000` answers "how *many* people in this area
are being treated", this answers "how *intensively* is each treated
person being medicated" — distinguishing an area where many people each
get one or two scripts from one where fewer people are on heavy repeat
regimens.

## Why this numerator / denominator

`AIHW_MHP.mh_prescriptions_count` (total MH-related scripts dispensed in
the SA4 over the financial year) divided by
`AIHW_MHP.mh_patients_count` (distinct patients who received at least
one). Both are headline counts from the same AIHW NMHSPF prescriptions
CSV, so the ratio needs no external denominator and is unaffected by
population estimates.

This is one of a family of AIHW "intensity per patient" PRESETs —
`mh_medicare_services_per_patient`, `mh_community_contacts_per_patient`,
and `mh_admitted_avg_length_of_stay` — each dividing an activity count
by its patient/episode count within one AIHW service setting.

## Cross-level note (SA4 → SA2)

Both inputs are SA4-native values the augmentor downscales to SA2 by
inheritance (every SA2 inside SA4 X carries SA4 X's value). The ratio is
therefore identical for every SA2 within an SA4 — the honest "no
within-parent variation" contract the underlying dataset already makes.
It is an SA4-level metric surfaced on SA2 rows, not an SA2-level
estimate.

## Edge cases

- **Zero / null denominator** → null. An SA2 whose SA4 had no patients
  published (suppressed or genuinely zero), or which maps to an SA4
  AIHW didn't publish, yields null rather than a divide-by-zero.
- **Perturbation** — AIHW perturbs small counts; the ratio of two
  perturbed counts is noisier in low-population SA4s. Treat as accurate
  to a few percent.
- **Bounds** — `[0, 60]` is a sanity band (warn, not clip). Repeat
  prescribing (e.g. monthly PBS repeats) puts the realistic mean around
  8–15 scripts per patient; values above ~60 almost certainly signal a
  data anomaly worth inspecting.

## Bounds (typical, not theoretical)

National mean sits in the high single digits to low teens of scripts
per patient per year. Regional variation reflects prescribing patterns
and the chronicity of the treated cohort more than population mix.

## Sources

- AIHW NMHSPF Regional activity data:
  https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- Both inputs documented in `datasets/aihw_mh_prescriptions.md`
  (namespace `AIHW_MHP`).
