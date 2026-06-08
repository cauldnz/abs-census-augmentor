---
id: aihw_mh_ed_presentations
name: AIHW Mental Health Emergency Department Presentations (NMHSPF) by SA4
status: proposed
custodian: Australian Institute of Health and Welfare (NMHSPF support data)
licence: CC-BY-4.0
update_cadence: annual
geography_level: SA4
geography_edition: 2021_ASGS_Edition_3
geography_native: false
join_key: sa2_code_2021
landing_page: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
fetch_size_compressed: ~410 KB (single ZIP with multiple CSVs)
tags: [mental-health, emergency-department, healthcare, downscale]
namespace: AIHW_ED
temporal:
  cadence: annual
  cover_basis: financial_year_ending
  release_id_format: "YYYY-YY (Australian financial year)"
  available_releases:
    - "2023-24"
  asgs_edition_by_release:
    "2023-24": 3
---

# AIHW Mental Health Emergency Department Presentations (NMHSPF) by SA4

Annual AIHW dataset from the **National Mental Health Service Planning
Framework (NMHSPF)** support material. Captures **mental-health-related
emergency department presentations** at **SA4** level (89 SA4s
nationally) — public-hospital ED presentations with a principal
diagnosis in the mental-and-behavioural-disorders range.

Third dataset in the AIHW NMHSPF family (after `aihw_mh_prescriptions`
and `aihw_mh_admitted_patients`), same SA4 → SA2 downscale. This is a
**cross-level / non-native-geography** dataset: AIHW publishes the
static download at SA4 (and PHN); the augmentor joins SA4 values onto
SA2 rows via the boundary's `SA4_CODE21` attribute (see `spec.md`
§20.7 Strategy 1). Every SA2 inside SA4 X inherits SA4 X's value —
the honest "no within-parent variation" contract.

## Source

ZIP download from AIHW NMHSPF support material:

- Latest (2023-24): `https://www.aihw.gov.au/getmedia/f9ac2b47-69b7-47f5-a1a2-7e5d1099195b/Mental-health-services-provided-in-emergency-departments-states-and-territories-2023-24.zip`

The ZIP contains, inside a subdirectory whose name carries a Unicode
en-dash (`Data tables_ED states and territories 2023–24/`), the
long-format CSV `ED_PHN_SA4_2324.csv` plus several other CSVs and
metadata workbooks the augmentor doesn't use. AIHW's getmedia URLs use
opaque UUIDs that are stable per release; new releases need to be added
to `available_releases` + the URL constant in the fetcher
(`_AIHW_ED_URLS_BY_RELEASE`).

## Update cadence

Annual. The ZIP carries a multi-year series (2014-15 … 2023-24); the
release id selects which financial year's rows the parser surfaces.

## Granularity

SA4 native — 89 SA4 codes. The CSV's `GeographicAreaCode` carries the
SA4 code with an `SA4` prefix (e.g. `SA4101`); the augmentor strips the
prefix to join against the boundary's `SA4_CODE21` (bare 3-digit). PHN
rows are filtered out (PHN doesn't nest into the ASGS hierarchy).

## Schema (variables exposed by the augmentor)

The CSV is in **long** format. The fetcher filters to
`PresentationType == "Mental health-related presentations"` (the file
also carries an `All presentations` denominator series) and the
requested financial year. Two measures.

| Variable | Type | Description |
|---|---|---|
| `AIHW_ED.mh_ed_presentations_count` | int | Mental-health-related ED presentations in the SA4 over the FY |
| `AIHW_ED.mh_ed_presentations_per_10000` | float | MH-related ED presentations per 10,000 estimated resident population |
| `AIHW_ED.reference_financial_year` | str | Reference period (e.g. "2023-24") |

### Wish list — spec'd here, not yet implemented

- `All presentations` denominator series, to compute the MH share of
  all ED presentations per SA4 (a "what fraction of ED load is
  mental-health-related" metric).

## Fetch notes (live-probed 2026-06-05)

- The PHN_SA4 member is inside a subdirectory whose name contains a
  **literal Unicode en-dash** (`…2023–24/ED_PHN_SA4_2324.csv`) — the
  parser matches the member by the `PHN_SA4` substring, NOT an exact
  path.
- The CSV is **cp1252** (like `aihw_mh_prescriptions`; unlike
  `aihw_mh_admitted_patients` which is UTF-8) — encoding is per-dataset
  across the AIHW family.
- `FinancialYear` labels use a Unicode en-dash (`2023–24`); normalised
  to ASCII hyphen for matching against the release id.
- Filter to `PresentationType == "Mental health-related presentations"`
  and `GeographicAreaType == "SA4"`.

## Cross-level downscale

SA4-native source, SA2-native output. The fetcher requires a SA2 → SA4
mapping attached via `attach_sa2_to_sa4_mapping(mapping)` before
`load()`. `Pipeline.from_config` wires this automatically from the SA2
boundary via `compute_sa2_parent_codes(boundaries)["SA4"]`; the
enricher attaches it to any fetcher exposing the method, so no
per-dataset pipeline change is needed. Without an attached mapping,
`load()` raises a clear `RuntimeError`. SA2s mapped to an SA4 AIHW
didn't publish for get null values rather than being dropped.

## Suppression / privacy notes

- AIHW perturbs small counts and suppresses cells below the publication
  threshold; suppressed cells parse to null.
- SA4 is the smallest geography AIHW publishes statically for this
  series. The honest contract: "MH-related ED presentations per 10,000
  in SA4 X" has identical value for every SA2 inside SA4 X.

## Suggested derived features

- `mh_ed_presentation_rate` — `AIHW_ED.mh_ed_presentations_per_10000`
  is already a rate; pair with `aihw_mh_admitted_patients` to study the
  ED-to-admission funnel per SA4.

## Sources / citations

- NMHSPF Regional activity data: https://www.aihw.gov.au/nmhspf/support-material/regional-activity-data
- AIHW Mental Health portal: https://www.aihw.gov.au/mental-health
- Licence: CC-BY-4.0
