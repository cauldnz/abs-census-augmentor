---
id: aihw_mh_community
name: AIHW Community Mental Health Care (NMHSPF) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare (NMHSPF support data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
fetch_size_compressed: ~940 KB (single ZIP with multiple CSVs)
tags: [mental-health, community-health, ambulatory, downscale]
namespace: AIHW_CMH
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2023-24"
  asgs_edition_by_release:
    "2023-24": 3
---

# AIHW Community Mental Health Care (NMHSPF) by SA4

Annual AIHW dataset from the **National Mental Health Service Planning
Framework (NMHSPF)** support material. Captures **community (ambulatory)
mental-health care** activity from the National Community Mental Health
database (CMHC) — patients, service contacts, and treatment days — at
**SA4** level, downscaled to SA2.

Fifth dataset in the AIHW NMHSPF family (after prescriptions, admitted
patient care, ED presentations, and Medicare services), same SA4 → SA2
downscale via the boundary's `SA4_CODE21` attribute (see `spec.md`
§20.7 Strategy 1). Every SA2 inside SA4 X inherits SA4 X's value — the
honest "no within-parent variation" contract.

## Source

ZIP download from AIHW NMHSPF support material:

- Latest (2023-24): `https://www.aihw.gov.au/getmedia/f04af158-e8b1-4660-93cc-8fd85eea5a08/Community-mental-health-care-state-and-territory-tables-2023-24.zip`

The ZIP contains three long-format CSVs (`CMHC_MRF_DemogFocus`,
`CMHC_MRF_GeospatialFocus`, `CMHC_MRF_SessionFocus`) plus metadata
workbooks. The augmentor reads only the **GeospatialFocus** CSV. New
releases ship under new opaque getmedia UUIDs — add to
`available_releases` + the URL constant in the fetcher
(`_AIHW_CMH_URLS_BY_RELEASE`).

## Update cadence

Annual. The GeospatialFocus CSV carries a multi-year series (2013-14 …
2023-24); the release id selects the financial year the parser surfaces.

## Granularity

SA4 native. The GeospatialFocus CSV mixes geography types in one file
(`GeospatialType` ∈ {`GCSSA`, `PHN`, `SA4`}); the augmentor filters to
`SA4`. For SA4 rows the `GeospatialDivisionCode` column carries the bare
3-digit SA4 code (`101`), joining directly to the boundary's
`SA4_CODE21` — **no prefix strip** (unlike the hyphenated Medicare
codes). For `GCSSA`/`PHN` rows the same column holds a *name*, so the
`GeospatialType == "SA4"` filter is load-bearing.

## Schema (variables exposed by the augmentor)

The CSV is in **long** format with a `DemographicCategory` /
`DemographicVariable` dimension; the fetcher filters to the `Total` /
`Total` headline (the file also splits by age group and Indigenous
status) and the requested financial year. Seven measures.

| Variable | Type | Description |
|---|---|---|
| `AIHW_CMH.mh_community_patients_count` | int | Patients receiving community MH care in the SA4 over the FY |
| `AIHW_CMH.mh_community_patients_per_10000` | float | Patients per 10,000 estimated resident population |
| `AIHW_CMH.mh_community_contacts_count` | int | Community MH service contacts provided in the SA4 |
| `AIHW_CMH.mh_community_contacts_per_10000` | float | Service contacts per 10,000 ERP |
| `AIHW_CMH.mh_community_treatment_days_per_3mo` | float | Treatment days per three-month period |
| `AIHW_CMH.mh_community_avg_treatment_length_days` | float | Average length of a treatment episode (days) |
| `AIHW_CMH.mh_community_population` | int | SA4 estimated resident population AIHW used as the rate denominator |
| `AIHW_CMH.reference_financial_year` | str | Reference period (e.g. "2023-24") |

### Wish list — spec'd here, not yet implemented

- Per-age-group and per-Indigenous-status breakdowns (the
  `DemographicCategory` splits the headline Total collapses) — an equity
  signal, at the cost of ~3× the column count.

## Fetch notes (live-probed 2026-06-09)

- The relevant CSV is `CMHC_MRF_GeospatialFocus_2324.csv` (the only
  member with "geospatial" in its name); it is **cp1252**.
- `GeospatialDivisionCode` is **polymorphic** — a 3-digit code for `SA4`
  rows, a place *name* for `GCSSA`/`PHN` rows. Filter `GeospatialType ==
  "SA4"` first.
- SA4 codes are **bare 3-digit** — no prefix to strip.
- `FinancialYear` labels use a Unicode en-dash — normalised to ASCII
  hyphen for release matching.
- The SA4 + `Total`/`Total` + FY slice pivots cleanly (one row per
  division × measure).

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
  contract: "community MH patients per 10,000 in SA4 X" is identical for
  every SA2 inside SA4 X.

## Suggested derived features

- `mh_community_contacts_per_patient` —
  `AIHW_CMH.mh_community_contacts_count / AIHW_CMH.mh_community_patients_count`
  (mean community-MH contacts per patient per year in the SA4).

## Sources / citations

- NMHSPF Regional activity data: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- AIHW Mental Health portal: https://www.aihw.gov.au/mental-health
- Licence: CC-BY-4.0
