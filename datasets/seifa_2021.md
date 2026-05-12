---
id: seifa_2021
name: ABS Socio-Economic Indexes for Areas (SEIFA) 2021
status: active
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: one-shot
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/latest-release
fetch_size_compressed: ~150 KB
tags: [seifa, demographics, socio-economic, disadvantage, advantage]
namespace: SEIFA
temporal:
  cadence: per_census
  cover_basis: census_reference_date
  release_id_format: YYYY (Census year)
  available_releases:
    - "2021"
  asgs_edition_by_release:
    "2021": 3
---

# ABS Socio-Economic Indexes for Areas (SEIFA) 2021

ABS's headline socio-economic typology, published once per Census.
Four indexes, each published as score (~600–1200, mean=1000, sd=100),
rank, decile, and percentile — both Australia-wide and state-relative.

The four indexes mean different things and are not interchangeable;
the SEIFA technical paper recommends quantiles (decile / percentile)
over scores for most modelling work since the score's numerical
scale is arbitrary.

- **IRSD** — Index of Relative Socio-economic Disadvantage. Disadvantage-only continuum (low score = more disadvantaged).
- **IRSAD** — Index of Relative Socio-economic Advantage and Disadvantage. Two-direction continuum.
- **IER** — Index of Economic Resources. Income, assets, dwelling-related variables.
- **IEO** — Index of Education and Occupation. Education attainment, occupational skill.

## Source

ABS publishes the SA2 file as `Statistical Area Level 2, Indexes,
SEIFA 2021.xlsx` from the latest-release page. The fetcher downloads
that single file (~150 KB compressed; ~1 MB uncompressed) and parses
the relevant sheet into a SA2-keyed parquet.

## Update cadence

Once per Census (five-yearly). 2021 file is final; 2026 will land as
`seifa_2026` when ABS publishes it.

## Granularity

SA2 native on 2021_ASGS_Edition_3. ABS computes scores at SA1 first
then aggregates to higher levels; the SA2 file is the canonical
publication for SA2 modelling.

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `SEIFA.irsd_score` | float | IRSD score (~600–1200, mean=1000) |
| `SEIFA.irsd_aus_rank` | int | IRSD rank within Australia |
| `SEIFA.irsd_aus_decile` | int | IRSD decile within Australia (1=most disadvantaged, 10=least) |
| `SEIFA.irsd_aus_percentile` | int | IRSD percentile within Australia |
| `SEIFA.irsd_state_rank` | int | IRSD rank within home state/territory |
| `SEIFA.irsd_state_decile` | int | IRSD decile within home state/territory |
| `SEIFA.irsd_state_percentile` | int | IRSD percentile within home state/territory |
| `SEIFA.irsad_score` | float | IRSAD score |
| `SEIFA.irsad_aus_rank` | int | IRSAD Australia rank |
| `SEIFA.irsad_aus_decile` | int | IRSAD Australia decile |
| `SEIFA.irsad_aus_percentile` | int | IRSAD Australia percentile |
| `SEIFA.irsad_state_rank` | int | IRSAD state rank |
| `SEIFA.irsad_state_decile` | int | IRSAD state decile |
| `SEIFA.irsad_state_percentile` | int | IRSAD state percentile |
| `SEIFA.ier_score` | float | IER score |
| `SEIFA.ier_aus_rank` | int | IER Australia rank |
| `SEIFA.ier_aus_decile` | int | IER Australia decile |
| `SEIFA.ier_aus_percentile` | int | IER Australia percentile |
| `SEIFA.ier_state_rank` | int | IER state rank |
| `SEIFA.ier_state_decile` | int | IER state decile |
| `SEIFA.ier_state_percentile` | int | IER state percentile |
| `SEIFA.ieo_score` | float | IEO score |
| `SEIFA.ieo_aus_rank` | int | IEO Australia rank |
| `SEIFA.ieo_aus_decile` | int | IEO Australia decile |
| `SEIFA.ieo_aus_percentile` | int | IEO Australia percentile |
| `SEIFA.ieo_state_rank` | int | IEO state rank |
| `SEIFA.ieo_state_decile` | int | IEO state decile |
| `SEIFA.ieo_state_percentile` | int | IEO state percentile |
| `SEIFA.urp` | int | Usual resident population (URP) of the SA2 (denominator for area-vs-population deciles) |

## Fetch notes

- The XLSX has multiple sheets; the relevant SA2 sheet has SA2 code
  in column A and the four indexes in subsequent columns. The header
  row is preceded by a multi-row preamble (disclaimer, source notes);
  the parser detects the data table by looking for the SA2-code header.
- "Australia" / state-level summary rows appear at the bottom of the
  data sheet in some releases — filter by SA2 code length (= 9).
- ABS may publish revisions; the augmentor pins to release year via
  `census_year` (default 2021).

## Suppression / privacy notes

- An SA2 can have a score for one index but not another (per the
  Technical Paper, phase-2 exclusions are applied per-variable, so
  different indexes have different exclusion sets). Surface as null;
  do **not** substitute zero or a sentinel.
- "Migratory / offshore / shipping" pseudo-SA2s have no scores at all;
  also surface as null.

## Suggested derived features

- `seifa_irsd_quintile` — derived from `SEIFA.irsd_aus_decile` (deferred to follow-up).

## Sources / citations

- Landing: https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/latest-release
- Technical paper: https://www.abs.gov.au/statistics/detailed-methodology-information/concepts-sources-methods/socio-economic-indexes-areas-seifa-technical-paper/2021
- Licence: CC-BY-4.0
