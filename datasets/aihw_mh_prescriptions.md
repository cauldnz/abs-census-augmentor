---
id: aihw_mh_prescriptions
name: AIHW Mental Health-related Prescriptions (NMHSPF) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare (NMHSPF support data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
fetch_size_compressed: ~880 KB (single ZIP with multiple CSVs)
tags: [mental-health, prescriptions, pharmaceutical, healthcare, downscale]
namespace: AIHW_MHP
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2024-25"
  asgs_edition_by_release:
    "2024-25": 3
---

# AIHW Mental Health-related Prescriptions (NMHSPF) by SA4

Annual AIHW dataset published as part of the **National Mental Health
Service Planning Framework (NMHSPF)** support material. Captures the
number of patients and prescriptions for mental health-related
medicines (antidepressants, antipsychotics, anxiolytics, hypnotics,
psychostimulants) under the Pharmaceutical Benefits Scheme (PBS) and
Repatriation PBS (RPBS), broken down to **SA4** (Statistical Area
Level 4) — 89 SA4s nationally.

This is a **cross-level / non-native-geography** dataset for the
augmentor. AIHW publishes the static download at SA4 only; finer
geographies (SA3 / SA2) live behind their interactive Regional
Profiles dashboard with no public direct URL. The augmentor joins
SA4 values onto SA2 rows via the boundary's `SA4_CODE21` attribute
(see `spec.md` §20.7 Strategy 1). Every SA2 inside SA4 X inherits
SA4 X's value unchanged — this is the honest contract: the source
publishes no within-SA4 variation, so the augmentor doesn't fabricate
any.

## Source

ZIP download from AIHW NMHSPF support material:

- Latest (2024-25): `https://www.aihw.gov.au/getmedia/464b35c8-9573-4a02-a508-0757c66feeb4/Mental-health-related-prescriptions-2024-25.zip`

The ZIP contains the long-format CSV
`Mental health-related prescriptions PHN and SA4 2024-25 (N).csv`
(~4.2 MB) plus a metadata workbook and a demographic-quarter CSV the
augmentor doesn't use. AIHW's getmedia URLs use opaque UUIDs that are
stable per release; new releases ship under new UUIDs and need to be
added to `available_releases` + the URL constant in the fetcher
(`_AIHW_RX_URLS_BY_RELEASE`).

## Update cadence

Annual. AIHW publishes the NMHSPF supports each financial year,
typically several months after FY end.

## Granularity

SA4 native — 89 SA4 codes nationally. The CSV file's
`GeographicAreaCode` column carries the SA4 code with an `SA4` prefix
(e.g. `SA4101`); the augmentor strips the prefix to join against the
boundary's `SA4_CODE21` (bare 3-digit). The CSV also includes PHN
rows; those are filtered out.

`FinancialYear` values use a Unicode en-dash (`2024–25`); the fetcher
normalises to ASCII (`2024-25`) for matching against `release`
identifiers.

## Schema (variables exposed by the augmentor)

The CSV is in **long** format with 4 measures × 3 demographics ×
several demographic categories per (FY, SA4). The fetcher filters to
`Demographic = "Total"` and `DemographicCategory = "Total"` to surface
the headline totals; age/sex breakdowns are a wish-list extension.

| Variable | Type | Description |
|---|---|---|
| `AIHW_MHP.mh_patients_count` | int | Total patients receiving at least one mental-health-related prescription in SA4 over the FY |
| `AIHW_MHP.mh_patient_rate_per_1000` | float | Patients per 1,000 estimated resident population in the SA4 |
| `AIHW_MHP.mh_prescriptions_count` | int | Total mental-health-related prescriptions dispensed in SA4 over the FY |
| `AIHW_MHP.mh_prescription_rate_per_1000` | float | Prescriptions per 1,000 ERP in the SA4 |
| `AIHW_MHP.reference_financial_year` | str | Reference period (e.g. "2024-25") |

### Wish list — spec'd here, not yet implemented

- Age-group breakdown columns (`Age group / 0–17 years`, `18–24 years`, ...,
  `65 years and over`). Each age band × 4 measures = up to 28 extra
  columns; useful for age-specific mental-health prevalence proxies.
- Sex breakdown columns (`Sex / Male`, `Sex / Female`). 2 × 4 = 8 extra
  columns; useful for sex-specific mental-health prevalence proxies.

## Fetch notes

- The ZIP contains CSVs in **Windows-1252 (cp1252)** encoding, not UTF-8.
  Source uses en-dash characters in age ranges (e.g. `0–17 years`) and
  in FY labels (`2024–25`); these mojibake under UTF-8 decode. The
  fetcher specifies `encoding="cp1252"` on `read_csv`.
- The CSV mixes SA4 and PHN rows in the same file; filter to
  `GeographicAreaType == "SA4"`.
- `GeographicAreaCode` has an `SA4` prefix (e.g. `SA4101`). Strip the
  prefix before joining to the boundary's `SA4_CODE21`.
- The 4 metric rows per (FY, SA4) are pivoted wide on `Measure` to
  produce one row per SA4 with 4 metric columns.

## Cross-level downscale

The dataset is SA4-native; the augmentor's output is SA2-native. To
join, the fetcher needs a SA2 → SA4 mapping derived from the SA2
boundary file's `SA4_CODE21` attribute. Pipeline construction wires
this in via `AihwMhPrescriptionsDataSource.attach_sa2_to_sa4_mapping(
{sa2_code: sa4_code, ...})` (analogous to how ERP gets areas via
`attach_sa2_areas`). For library use against a custom boundary,
callers can derive the mapping themselves via
`census_augment.spatial.compute_sa2_parent_codes(boundaries)["SA4"]`.

Without an attached mapping, `load()` raises a clear `RuntimeError`
explaining how to attach one. This is intentional: SA4-keyed output
would not be useful in the rest of the pipeline (which is SA2-native),
so making the dependency required at load-time is cleaner than
silently emitting wrong-shape data.

## Suppression / privacy notes

- AIHW perturbs small counts and applies suppression for cells with
  fewer than the publication threshold. Suppressed cells appear as
  blank or `-`; parsed to null.
- SA4 is the smallest geography AIHW publishes statically for this
  series — coarser than SA2 (89 SA4s vs 2,473 SA2s nationally). The
  honest contract: "patients per 1,000 in SA4 X" has identical value
  for every SA2 inside SA4 X.

## Suggested derived features

- `mh_prescriptions_per_resident` —
  `AIHW_MHP.mh_prescriptions_count / ERP.population_total` — direct
  per-resident prescription intensity. (The `_rate_per_1000` column
  is the ABS-published version; this PRESET would be the augmentor's
  computed version against the same ERP it uses elsewhere.)
- `mh_high_prevalence_indicator` —
  `(AIHW_MHP.mh_patient_rate_per_1000 > <state-median threshold>)` —
  binary indicator for above-median SA4 patient rate; useful for
  regression-discontinuity-style analyses.

## Sources / citations

- NMHSPF Regional activity data: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- AIHW Mental Health portal: https://www.aihw.gov.au/mental-health
- Licence: CC-BY-4.0
