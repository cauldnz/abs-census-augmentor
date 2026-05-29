---
id: seifa
name: ABS Socio-Economic Indexes for Areas (SEIFA)
status: active
custodian: Australian Bureau of Statistics
licence: CC-BY-4.0
update_cadence: one-shot
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/latest-release
fetch_size_compressed: ~150 KB - 2.4 MB
tags: [seifa, demographics, socio-economic, disadvantage, advantage]
namespace: SEIFA
temporal:
  cadence: per_census
  cover_basis: census_reference_date
  release_id_format: YYYY (Census year)
  available_releases:
    - "2011"
    - "2016"
    - "2021"
  asgs_edition_by_release:
    "2011": 1
    "2016": 2
    "2021": 3
---

# ABS Socio-Economic Indexes for Areas (SEIFA)

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

- **2021**: ABS publishes the SA2 file as `Statistical Area Level 2,
  Indexes, SEIFA 2021.xlsx` (~150 KB compressed; ~1 MB uncompressed).
  Direct-link XLSX download.
- **2016**: ABS publishes `2033055001 - sa2 indexes.xls` (~700 KB).
  Same sheet structure as 2021 but in legacy .xls format (requires
  `python-calamine` for parsing); uses ASGS Edition 2 SA2 codes.
- **2011**: ABS publishes `2033.0.55.001 SA2 Indexes.xls` (~2.4 MB).
  Same sheet structure as 2016, legacy .xls format. Uses ASGS Edition 1
  SA2 codes (different geography from 2016/2021 — ~2,214 SA2s vs
  ~2,310 / ~2,473). Lotus Notes openagent download URL.

## Update cadence

Once per Census (five-yearly). Available releases: 2011 (ASGS Edition 1,
SA2 .xls), 2016 (ASGS Edition 2, SA2 .xls), and 2021 (ASGS Edition 3,
SA2 .xlsx). 2026 will be added to this dataset when ABS publishes it.

Pre-2011 SEIFA releases (2001, 2006) used CCD/SLA pre-ASGS geographies
that don't align with SA2 — they're explicitly out of scope per
`spec-temporal.md` §17.

## Granularity

SA2 native. ABS computes scores at SA1 first then aggregates to higher
levels; the SA2 file is the canonical publication for SA2 modelling.

Per-release SA2 geography differs across ASGS editions:

- **2011** uses ASGS Edition 1 SA2 codes (~2,214 SA2s; e.g. `101011001`).
- **2016** uses ASGS Edition 2 SA2 codes (~2,310 SA2s; e.g. `101021007`).
- **2021** uses ASGS Edition 3 SA2 codes (~2,473 SA2s; e.g. the current
  canonical reference edition).

All three are 9-digit integers but the underlying geography is
different. In temporal mode the pipeline reads each release using its
contemporaneous boundary file, and the canonical `sa2_code` in output
is the configured `reference_edition` (default Edition 3).

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
| `SEIFA.state_abbreviation` | str | State/territory abbreviation (NSW, VIC, ...) — sourced from the workbook's state column |
| `SEIFA.irsd_sa1_min` | float | Minimum SA1-level IRSD score observed within this SA2 (within-SA2 spread) |
| `SEIFA.irsd_sa1_max` | float | Maximum SA1-level IRSD score observed within this SA2 |
| `SEIFA.irsd_pct_urp_no_score` | float | Percentage of this SA2's URP whose SA1s were excluded from IRSD scoring (per-variable exclusion; see Technical Paper §4.5) |
| `SEIFA.irsad_sa1_min` | float | Minimum SA1-level IRSAD score observed within this SA2 |
| `SEIFA.irsad_sa1_max` | float | Maximum SA1-level IRSAD score observed within this SA2 |
| `SEIFA.irsad_pct_urp_no_score` | float | Percentage of this SA2's URP whose SA1s were excluded from IRSAD scoring |
| `SEIFA.ier_sa1_min` | float | Minimum SA1-level IER score observed within this SA2 |
| `SEIFA.ier_sa1_max` | float | Maximum SA1-level IER score observed within this SA2 |
| `SEIFA.ier_pct_urp_no_score` | float | Percentage of this SA2's URP whose SA1s were excluded from IER scoring |
| `SEIFA.ieo_sa1_min` | float | Minimum SA1-level IEO score observed within this SA2 |
| `SEIFA.ieo_sa1_max` | float | Maximum SA1-level IEO score observed within this SA2 |
| `SEIFA.ieo_pct_urp_no_score` | float | Percentage of this SA2's URP whose SA1s were excluded from IEO scoring |
| `SEIFA.irsd_sa2_name` | str | SA2 name as read from the IRSD sheet (duplicate of `sa2_name` in the canonical join key — kept for join-debugging convenience) |
| `SEIFA.irsad_sa2_name` | str | SA2 name as read from the IRSAD sheet (duplicate) |
| `SEIFA.ier_sa2_name` | str | SA2 name as read from the IER sheet (duplicate) |
| `SEIFA.ieo_sa2_name` | str | SA2 name as read from the IEO sheet (duplicate) |

## Fetch notes

- Both the 2016 (.xls) and 2021 (.xlsx) workbooks have the same sheet
  structure (Contents, Table 1 summary, Tables 2-5 per-index detail,
  Table 6 exclusions, Explanatory Notes). Column positions are
  identical across releases; the parser selects the reader (openpyxl
  for .xlsx, python-calamine for .xls) based on the release.
- The SA2-code header row is preceded by a multi-row preamble; the
  parser detects the data table by scanning for the SA2-code header
  text rather than trusting a fixed row offset.
- "Australia" / state-level summary rows appear at the bottom of the
  data sheets — filtered by requiring exactly 9 consecutive digits in
  the SA2 code column.
- ABS may publish revisions; the augmentor pins to release year via
  `release` (default "2021").

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

- Landing (latest): https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/latest-release
- Landing (2016): https://www.abs.gov.au/ausstats/abs@.nsf/mf/2033.0.55.001
- Technical paper (2021): https://www.abs.gov.au/statistics/detailed-methodology-information/concepts-sources-methods/socio-economic-indexes-areas-seifa-technical-paper/2021
- Licence: CC-BY-4.0
