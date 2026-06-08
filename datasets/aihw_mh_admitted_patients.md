---
id: aihw_mh_admitted_patients
name: AIHW Mental Health Admitted Patient Care (NMHSPF) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare (NMHSPF support data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
fetch_size_compressed: ~460 KB (single ZIP with multiple CSVs)
tags: [mental-health, hospital, admitted-patients, healthcare, downscale]
namespace: AIHW_APC
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2023-24"
  asgs_edition_by_release:
    "2023-24": 3
---

# AIHW Mental Health Admitted Patient Care (NMHSPF) by SA4

Annual AIHW dataset from the **National Mental Health Service Planning
Framework (NMHSPF)** support material. Captures mental-health-related
**admitted patient care** activity — hospitalisations, patient days,
psychiatric care days, and procedures — broken down to **SA4**
(Statistical Area Level 4), 89 SA4s nationally.

Sibling to `aihw_mh_prescriptions` (same source family, same SA4 → SA2
downscale). This is a **cross-level / non-native-geography** dataset:
AIHW publishes the static download at SA4 (and PHN); the augmentor
joins SA4 values onto SA2 rows via the boundary's `SA4_CODE21`
attribute (see `spec.md` §20.7 Strategy 1). Every SA2 inside SA4 X
inherits SA4 X's value unchanged — the honest "no within-parent
variation" contract: the source publishes no finer detail, so the
augmentor fabricates none.

## Source

ZIP download from AIHW NMHSPF support material:

- Latest (2023-24): `https://www.aihw.gov.au/getmedia/1ed521e7-7ee2-4dc0-98a4-d4f0bd0b027d/Admitted-patient-care-state-and-territory-2023-24-data-files.zip`

The ZIP contains the long-format CSV
`Admitted patient care state and territory PHN_SA4 2023-24.csv` plus a
"Common Procedures" CSV and two metadata workbooks the augmentor
doesn't use. AIHW's getmedia URLs use opaque UUIDs that are stable per
release; new releases ship under new UUIDs and need to be added to
`available_releases` + the URL constant in the fetcher
(`_AIHW_APC_URLS_BY_RELEASE`).

## Update cadence

Annual. AIHW publishes the NMHSPF supports each financial year,
typically several months after FY end. The ZIP is single-year (no
`FinancialYear` column), so each release is one URL.

## Granularity

SA4 native — 89 SA4 codes nationally. The CSV's `GeographicAreaCode`
carries the SA4 code with an `SA4` prefix (e.g. `SA4101`); the
augmentor strips the prefix to join against the boundary's
`SA4_CODE21` (bare 3-digit). The CSV also includes PHN rows; those are
filtered out (PHN doesn't nest into the ASGS hierarchy).

## Schema (variables exposed by the augmentor)

The CSV is in **long** format with a `SeparationType` dimension
(`Same day` / `Overnight` / `Total`). The fetcher filters to
`SeparationType == "Total"` for the headline values. Four metrics, each
with a count and a `per 10,000 population` rate twin.

| Variable | Type | Description |
|---|---|---|
| `AIHW_APC.mh_hospitalisations_count` | int | Mental-health-related hospitalisations (separations) in the SA4 over the FY |
| `AIHW_APC.mh_patient_days_count` | int | Total patient days for MH-related admitted care in the SA4 |
| `AIHW_APC.mh_psychiatric_care_days_count` | int | Patient days specifically under specialised psychiatric care |
| `AIHW_APC.mh_procedures_count` | int | MH-related procedures performed |
| `AIHW_APC.mh_hospitalisations_per_10000` | float | Hospitalisations per 10,000 estimated resident population |
| `AIHW_APC.mh_patient_days_per_10000` | float | Patient days per 10,000 ERP |
| `AIHW_APC.mh_psychiatric_care_days_per_10000` | float | Psychiatric care days per 10,000 ERP |
| `AIHW_APC.mh_procedures_per_10000` | float | Procedures per 10,000 ERP |
| `AIHW_APC.reference_financial_year` | str | Reference period (e.g. "2023-24") |

### Wish list — spec'd here, not yet implemented

- `SeparationType` breakdown (`Same day` vs `Overnight`) — useful for
  distinguishing acute inpatient load from day-only care. Doubles the
  metric columns.

## Fetch notes

- The member CSV is **UTF-8** (real-data finding, live-probed
  2026-06-05) — NOT cp1252 like the `aihw_mh_prescriptions` sibling.
  Different files in the same AIHW source family use different
  encodings, so encoding is per-dataset.
- No `FinancialYear` column — single-year publication; the
  `reference_financial_year` output column is set to the release id.
- The ZIP mixes SA4 + PHN rows in the same file; filter to
  `GeographicAreaType == "SA4"`.
- `GeographicAreaCode` has an `SA4` prefix (e.g. `SA4101`); strip
  before joining to the boundary's bare `SA4_CODE21`.
- The PHN_SA4 member is matched by the `PHN_SA4` substring (the ZIP
  also has a "Common Procedures" CSV — don't match that one).

## Cross-level downscale

SA4-native source, SA2-native output. The fetcher requires a SA2 → SA4
mapping attached via `attach_sa2_to_sa4_mapping(mapping)` before
`load()`. `Pipeline.from_config` wires this automatically from the SA2
boundary via `compute_sa2_parent_codes(boundaries)["SA4"]`. Without an
attached mapping, `load()` raises a clear `RuntimeError`. SA2s mapped to
an SA4 AIHW didn't publish for get null values rather than being
dropped, keeping the join with other datasets well-formed.

## Suppression / privacy notes

- AIHW perturbs small counts and applies suppression for cells below
  the publication threshold; suppressed cells parse to null.
- SA4 is the smallest geography AIHW publishes statically for this
  series — coarser than SA2 (89 SA4s vs 2,473 SA2s nationally). The
  honest contract: "hospitalisations per 10,000 in SA4 X" has identical
  value for every SA2 inside SA4 X.

## Suggested derived features

- `mh_hospitalisation_intensity` —
  `AIHW_APC.mh_patient_days_count / AIHW_APC.mh_hospitalisations_count`
  (mean length of stay per MH hospitalisation in the SA4).

## Sources / citations

- NMHSPF Regional activity data: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- AIHW Mental Health portal: https://www.aihw.gov.au/mental-health
- Licence: CC-BY-4.0
