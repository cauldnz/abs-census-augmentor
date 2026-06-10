---
id: salm_labour_force
name: Small Area Labour Markets (SALM) smoothed SA2 estimates
status: proposed
custodian: Jobs and Skills Australia (Department of Employment and Workplace Relations)
licence: CC-BY-4.0
update_cadence: quarterly
geography_level: SA2
geography_edition: 2021_ASGS_Edition_3
geography_native: true
join_key: sa2_code_2021
landing_page: https://www.dewr.gov.au/employment-research/resources/salm-smoothed-sa2-datafiles-asgs-2021
fetch_size_compressed: ~2.4 MB (single CSV)
tags: [economy, labour-market, employment, unemployment]
namespace: SALM
temporal:
  cadence: quarterly
  cover_basis: quarter_ending
  release_id_format: "YYYY-Qn (e.g. 2025-Q4 = December quarter 2025)"
  available_releases:
    - "2025-Q4"
  asgs_edition_by_release:
    "2025-Q4": 3
---

# Small Area Labour Markets (SALM) smoothed SA2 estimates

Quarterly model-smoothed labour-market estimates for every SA2, from
Jobs and Skills Australia's **Small Area Labour Markets** publication.
The canonical sub-regional unemployment series — and the key gap it
fills: ABS Census employment is only 5-yearly, whereas SALM gives a
**current, quarterly** read of local labour-market conditions.

The augmentor surfaces the **latest quarter** present in the downloaded
file (the headline "how's the labour market here right now"). The full
back series (Dec 2010 onward) is in the source CSV — exposing historical
quarters is a wish-list extension.

## Source

Single CSV from the DEWR resources site (smoothed SA2 datafile):

- 2025-Q4 (December quarter 2025): `https://www.dewr.gov.au/download/17068/salm-smoothed-sa2-datafiles-asgs-2021-december-quarter-2025/42403/salm-smoothed-sa2-datafiles-asgs-2021-december-quarter-2025/csv`

The DEWR download URL embeds a rotating asset id per quarter — the URL
is hardcoded per release (no HTML scrape); a new quarter needs a new
entry in `_SALM_URLS_BY_RELEASE` in the fetcher. The parser cross-checks
that the file's latest quarter matches the requested release, so a stale
URL fails loud rather than surfacing the wrong quarter.

## Update cadence

Quarterly. Each quarterly file carries the full back series; the release
id (`YYYY-Qn`) selects which quarterly download, and the parser surfaces
that file's most recent quarter.

## Granularity

SA2 native — no downscale. The CSV is long on `Data Item` (three
measures) and wide on quarter; the parser pivots the latest quarter's
column into one row per SA2.

## Schema (variables exposed by the augmentor)

| Variable | Type | Description |
|---|---|---|
| `SALM.smoothed_unemployment_count` | int | Smoothed number of unemployed persons in the SA2 (latest quarter) |
| `SALM.smoothed_labour_force_count` | int | Smoothed labour force (employed + unemployed) in the SA2 |
| `SALM.smoothed_unemployment_rate` | float | Smoothed unemployment rate (%) — unemployed as a share of the labour force |
| `SALM.reference_period` | str | The quarter surfaced (e.g. "2025-Q4" = December quarter 2025) |

### Wish list — spec'd here, not yet implemented

- **Historical quarters** — the source carries the full Dec-2010-onward
  series in one file; a release/quarter selector could surface any past
  quarter or a multi-quarter trend, not just the latest.

## Fetch notes (live-probed 2026-06-10)

- The CSV's row 1 is an explanatory note (a dash `-` indicates data are
  unavailable), row 2 is blank, and the header is row 3: `Data Item,
  Statistical Area Level 2 (SA2) (2021 ASGS), SA2 Code (2021 ASGS),
  Dec-10, Mar-11, … Dec-25`.
- 2,336 SA2s × three `Data Item` rows each. `-` parses to null.
- The DEWR server is occasionally slow to first byte; the fetcher's
  retry wrapper covers the transient case.

## Suppression / privacy notes

- A `-` marks an SA2 whose labour-force estimate did not meet the
  minimum size, or where a series break prevents a reliable estimate —
  parsed to null rather than zero.
- Estimates are **model-smoothed** (a 4-quarter average underpins the
  method), so quarter-to-quarter movements are damped by design.

## Suggested derived features

- The unemployment *rate* is already provided directly
  (`SALM.smoothed_unemployment_rate`), so no ratio PRESET is needed for
  the headline metric — it's a rare case where the source ships the
  normalised figure.

## Sources / citations

- Jobs and Skills Australia — Small Area Labour Markets:
  https://www.dewr.gov.au/employment-research/resources/salm-smoothed-sa2-datafiles-asgs-2021
- Licence: CC-BY-4.0
