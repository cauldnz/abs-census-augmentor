---
id: dss_payments
name: DSS Payment Demographic Data
status: proposed
custodian: Department of Social Services
licence: CC-BY-4.0
update_cadence: quarterly
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://data.gov.au/data/dataset/dss-payment-demographic-data
fetch_size_compressed: ~5 MB per quarter
tags: [welfare, social-services, demographics]
namespace: DSS
temporal:
  cadence: quarterly
  cover_basis: quarter_ending
  release_id_format: "YYYY-Qn (quarter ending)"
  available_releases:
    - "2022-Q4"
    - "2023-Q1"
    - "2023-Q2"
    - "2023-Q3"
    - "2023-Q4"
    - "2024-Q1"
    - "2024-Q2"
    - "2024-Q3"
    - "2024-Q4"
    - "2025-Q1"
    - "2025-Q2"
    - "2025-Q3"
  asgs_edition_by_release:
    "2022-Q4": 2
    "2023-Q1": 2
    "2023-Q2": 3
    "2023-Q3": 3
    "2023-Q4": 3
    "2024-Q1": 3
    "2024-Q2": 3
    "2024-Q3": 3
    "2024-Q4": 3
    "2025-Q1": 3
    "2025-Q2": 3
    "2025-Q3": 3
---

# DSS Payment Demographic Data

Quarterly counts of recipients of Australian Government income-support and family
payments — Age Pension, JobSeeker, Disability Support Pension, Parenting Payment,
Carer Payment and others — broken down by SA2 of residence and basic demographics
(age band, sex). Published by the Department of Social Services on data.gov.au.

This is the natural complement to ABS Census demographic data: Census tells you
who lives in an SA2; DSS tells you how many of them are receiving which payments.

## Source

Landing page: https://data.gov.au/data/dataset/dss-payment-demographic-data

The dataset is published as a series of quarterly CSV files, one per release date.
The augmentor should:

1. Discover the latest available release via the data.gov.au CKAN API
   (`https://data.gov.au/data/api/3/action/package_show?id=dss-payment-demographic-data`)
2. Filter to the SA2-resolution resources (the same data is also published at
   Postcode and LGA level — exclude those for the SA2-native pipeline).
3. Cache one parquet per quarter under `<cache>/dss_payments/<YYYY-Q>.parquet`.

## Update cadence

Quarterly. Approximately a 6-month lag from reference quarter to publication.

## Granularity

SA2 native. Counts are perturbed by small-cell suppression: cells with fewer
than ~20 recipients are reported as `<20` or null. The augmentor surfaces these
as null, not zero (zero is meaningful — five recipients is also meaningful;
suppression hides which).

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `DSS.age_pension_recipients` | int | Recipients of Age Pension |
| `DSS.jobseeker_payment_recipients` | int | Recipients of JobSeeker Payment |
| `DSS.disability_support_pension_recipients` | int | Recipients of Disability Support Pension |
| `DSS.parenting_payment_single_recipients` | int | Recipients of Parenting Payment Single |
| `DSS.parenting_payment_partnered_recipients` | int | Recipients of Parenting Payment Partnered |
| `DSS.carer_payment_recipients` | int | Recipients of Carer Payment |
| `DSS.youth_allowance_other_recipients` | int | Recipients of Youth Allowance (Other) |
| `DSS.youth_allowance_student_recipients` | int | Recipients of Youth Allowance (Student/Apprentice) |
| `DSS.commonwealth_rent_assistance_recipients` | int | Recipients of CRA |
| `DSS.release_quarter` | str | The release period these counts apply to (e.g. "2024-Q4") |

The augmentor pulls the latest quarter by default; users can pin via
`Pipeline.create(..., dss_release="2024-Q4")` if they want a stable release.

## Fetch notes

- Schema has changed across releases (some payment categories were renamed when
  Newstart became JobSeeker in 2020). The fetcher should NOT enforce a schema;
  the parser maps known column names to canonical variable names via a small
  alias table maintained per release era.
- Some early releases (pre-2018) used SA2 2011 boundaries; those releases
  should be filtered out by the SA2 edition declared in the front-matter, or
  passed through an ASGS 2011→2021 concordance with appropriate caveats.
- The CKAN response includes a `last_modified` per resource — use that for
  cache-invalidation rather than parsing release IDs.

## Suppression / privacy notes

- Cells with fewer than ~20 recipients are suppressed (rendered as null or
  `<20` depending on release format).
- Suppression is applied independently per payment type, so an SA2 can have
  visible Age Pension counts but suppressed JobSeeker counts.
- Surface suppressed cells as null. Downstream rate calculations should
  propagate the null rather than substituting a midpoint.

## Suggested derived features

- `pct_age_pension_recipients` — DSS.age_pension_recipients / ERP.population_65_plus.
  Pairs naturally with `pct_aged_65_plus`. (Cross-dataset feature.)
- `pct_jobseeker_recipients` — DSS.jobseeker_payment_recipients / ERP.population_15_64.
- `welfare_density_index` — sum of all DSS recipient counts / ERP.total_population.

## Sources / citations

- Dataset landing: https://data.gov.au/data/dataset/dss-payment-demographic-data
- Custodian: https://www.dss.gov.au/about-the-department/publications-articles/research-publications
- Licence: CC-BY-4.0 per data.gov.au metadata
