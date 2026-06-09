---
id: aihw_mh_medicare
name: AIHW Medicare-subsidised Mental Health Services (NMHSPF) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare (NMHSPF support data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
fetch_size_compressed: ~730 KB (single ZIP with multiple CSVs)
tags: [mental-health, medicare, mbs, primary-care, downscale]
namespace: AIHW_MBS
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2024-25"
  asgs_edition_by_release:
    "2024-25": 3
---

# AIHW Medicare-subsidised Mental Health Services (NMHSPF) by SA4

Annual AIHW dataset from the **National Mental Health Service Planning
Framework (NMHSPF)** support material. Captures **Medicare-subsidised
mental-health-specific services** (MBS items) — patients and services —
at **SA4** level, downscaled to SA2.

Fourth dataset in the AIHW NMHSPF family (after prescriptions, admitted
patient care, and ED presentations), same SA4 → SA2 downscale via the
boundary's `SA4_CODE21` attribute (see `spec.md` §20.7 Strategy 1).
Every SA2 inside SA4 X inherits SA4 X's value — the honest "no
within-parent variation" contract.

## Source

ZIP download from AIHW NMHSPF support material:

- Latest (2024-25): `https://www.aihw.gov.au/getmedia/e733afb1-0cba-4998-be88-86fa9291e621/Medicare-mental-health-service-2024-25.zip`

The ZIP contains `Medicare mental health services PHN SA4 2024-25.csv`
plus a quarters/demographics CSV and two metadata workbooks the
augmentor doesn't use. New releases ship under new opaque getmedia
UUIDs — add to `available_releases` + the URL constant in the fetcher
(`_AIHW_MEDICARE_URLS_BY_RELEASE`).

## Update cadence

Annual. The ZIP carries a multi-year series; the release id selects the
financial year the parser surfaces.

## Granularity

SA4 native. The CSV's `GeographicAreaCode` carries the SA4 code in a
**hyphenated** form (`SA4-101`); the augmentor strips the `SA4-` prefix
to join against the boundary's bare 3-digit `SA4_CODE21`. PHN rows are
filtered out.

## Schema (variables exposed by the augmentor)

The CSV is in **long** format with a `ProviderType` dimension. The
fetcher filters to the `All providers` total (the file also splits by
Psychiatrists / GPs / Clinical psychologists / Other psychologists /
Other allied health) and the requested financial year. Four measures.

| Variable | Type | Description |
|---|---|---|
| `AIHW_MBS.mh_medicare_patients_count` | int | Patients who received a Medicare-subsidised MH service in the SA4 over the FY |
| `AIHW_MBS.mh_medicare_patient_rate_per_1000` | float | Patients per 1,000 estimated resident population |
| `AIHW_MBS.mh_medicare_services_count` | int | Medicare-subsidised MH services provided in the SA4 |
| `AIHW_MBS.mh_medicare_service_rate_per_1000` | float | Services per 1,000 ERP |
| `AIHW_MBS.reference_financial_year` | str | Reference period (e.g. "2024-25") |

### Wish list — spec'd here, not yet implemented

- Per-`ProviderType` breakdown (Psychiatrists / GPs / Clinical
  psychologists / etc.) — a strong access-equity signal (e.g.
  psychiatrist services per 1,000 by SA4). ~5 provider types × 4
  measures.

## Fetch notes (live-probed 2026-06-05)

- The CSV is **cp1252**.
- SA4 codes are **hyphenated** — `SA4-101`, not `SA4101` like the other
  AIHW datasets. The parser strips `^SA4-`.
- `ProviderType` values contain **non-breaking spaces** (U+00A0), e.g.
  `"All\xa0providers"`. The parser normalises NBSP → regular space
  before filtering to `"All providers"`.
- `FinancialYear` labels use a Unicode en-dash — normalised to ASCII
  hyphen for release matching.

## Cross-level downscale

SA4-native source, SA2-native output. Requires a SA2 → SA4 mapping
attached via `attach_sa2_to_sa4_mapping(mapping)` before `load()`.
`Pipeline.from_config` wires this automatically from the boundary; the
enricher attaches it to any fetcher exposing the method. SA2s mapped to
an SA4 AIHW didn't publish for get null values rather than being
dropped.

## Suppression / privacy notes

- AIHW perturbs small counts and suppresses cells below the publication
  threshold; suppressed cells parse to null.
- SA4 is the smallest geography AIHW publishes statically. The honest
  contract: "patients per 1,000 in SA4 X" is identical for every SA2
  inside SA4 X.

## Suggested derived features

- `mh_medicare_services_per_patient` —
  `AIHW_MBS.mh_medicare_services_count / AIHW_MBS.mh_medicare_patients_count`
  (mean MH-service contacts per patient per year in the SA4).

## Sources / citations

- NMHSPF Regional activity data: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- AIHW Mental Health portal: https://www.aihw.gov.au/mental-health
- Licence: CC-BY-4.0
