# Australian Census Augmentation Tool — Specification

> **Status:** Draft v0.3
> **Purpose:** Hand-off specification for implementation by Claude Code. Update this document as design decisions evolve.

---

## 1. Purpose

A Python tool that takes a dataset of locations in Australia (as addresses, coordinates, or a mix) and augments each record with selected variables from the Australian Bureau of Statistics (ABS) Census of Population and Housing at the SA2 statistical area level.

The output is a CSV with the original records plus appended columns drawn from the census, suitable for downstream analysis or merging with other datasets.

---

## 2. Scope

### v1 — in scope
- Input: CSV containing addresses and/or `(lat, lon)` coordinates (or a mix per row).
- Geocoding via Nominatim (public OpenStreetMap API).
- SA2-level statistical area assignment via point-in-polygon.
- 2021 Census DataPack — General Community Profile (GCP).
- Output: enriched CSV.
- Configuration-driven variable selection using human-readable names.
- Local caching of geocoded addresses.
- Runtime download of ABS data (boundaries + DataPacks); nothing checked into git.
- A `discover` command to help users find census variables by keyword.

### Future / out of scope for v1
- G-NAF-based geocoding (pluggable interface should support this later).
- Paid geocoding providers (Google, Mapbox).
- SA1 and SA3 levels (architecture should not preclude them).
- Other DataPack profiles (Indigenous, Working Population, Time Series).
- 2026 Census data when released (architecture should not preclude it).
- Computed/derived variables (ratios, percentages combining multiple columns) — these are an explicit downstream concern of the data science feature engineering pipeline that consumes this tool's output, not a responsibility of this tool.
- Output formats other than CSV (Parquet, GeoPackage).
- Explicit input deduplication. Duplicate input rows are processed independently; efficiency on duplicate addresses comes from the geocoding cache.

### Usage assumptions
- **Target scale:** typically a few hundred rows per run. Nominatim's 1 req/sec policy is acceptable at this scale. Larger workloads are deferred to a future pluggable geocoder (see §13).

---

## 3. Architecture Overview

A linear pipeline:

```
Input CSV  →  Geocoding  →  Spatial Join  →  Census Enrichment  →  Output CSV
              (Nominatim)   (SA2 polygons)   (DataPack lookup)
                  ↓                                  ↑
            geocoding cache              metadata-driven config
```

Each stage is independently testable. Cached artifacts (geocoded addresses, downloaded boundaries and DataPacks) make re-runs cheap.

---

## 4. Data Sources

### 4.1 ASGS SA2 Boundaries
- **Source:** ABS Australian Statistical Geography Standard (ASGS) Edition 3 (covers Jul 2021 – Jun 2026).
- **Format preference:** GeoPackage (smaller, single-file). Shapefile acceptable.
- **CRS:** GDA2020 (EPSG:7844). Reproject input points as needed.
- **Approximate size:** ~30 MB.
- **Base URL (configurable):** `https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files`
- **Filename pattern:** `{level}_{year}_AUST_{datum}.zip`, e.g. `SA2_2021_AUST_GDA2020.zip`.
- The tool downloads this on first use into `data/boundaries/` and caches it.

### 4.2 Census DataPacks (2021 GCP)
- **Source:** ABS Census 2021 DataPacks page.
- **Selection (defaults):** General Community Profile (`GCP`), SA2 level, all of Australia (`AUS`), short-header descriptor.
- **Format:** ZIP archive containing one CSV per table (`G01`, `G02`, …) plus an Excel metadata file mapping cryptic column codes (e.g. `Tot_P_M`) to human-readable descriptions ("Total Persons Male").
- **Base URL (configurable):** `https://www.abs.gov.au/census/find-census-data/datapacks/download`
- **Filename pattern:** `{year}_{profile}_{level}_for_{region}_{descriptor}.zip`, e.g. `2021_GCP_SA2_for_AUS_short-header.zip`.
- The tool constructs the full filename deterministically from config values, so users can override the *base* URL without having to specify each individual file.
- The tool downloads, extracts, and indexes this on first use into `data/census/`.

> **Implementation note:** Both base URLs are exposed as `data_sources.boundaries_base_url` and `data_sources.datapacks_base_url` in config (see §6). Defaults ship with the spec and are validated to be reachable on first run; users can override either if ABS restructures their site.

---

## 5. Project Structure

```
census-augment/
├── README.md
├── pyproject.toml
├── config.example.yaml
├── .gitignore
├── data/                          # Runtime-fetched; gitignored
│   ├── README.md                  # Describes purpose; checked in
│   ├── .gitignore                 # Ignores all but README.md
│   ├── boundaries/                # ASGS files
│   └── census/                    # Extracted DataPacks
├── cache/                         # Runtime-generated; gitignored
│   ├── README.md
│   ├── .gitignore
│   └── geocoding/                 # JSON per address
├── src/
│   └── census_augment/
│       ├── __init__.py
│       ├── cli.py                 # Entry point (Typer or Click)
│       ├── config.py              # Pydantic schema + validation
│       ├── data_sources/
│       │   ├── boundaries.py      # Download + load SA2 polygons
│       │   └── datapacks.py       # Download + parse tables + metadata
│       ├── geocoding/
│       │   ├── base.py            # Abstract Geocoder interface
│       │   ├── nominatim.py       # Nominatim implementation
│       │   └── cache.py           # Hash-keyed JSON cache
│       ├── spatial.py             # Point-in-polygon → SA2
│       ├── enrich.py              # SA2 + variables → enriched rows
│       ├── catalog.py             # Variable resolution against metadata
│       └── pipeline.py            # Orchestration
└── tests/
    ├── fixtures/
    └── ...
```

### `.gitignore` pattern for data and cache folders

Each runtime folder gets its own `.gitignore` so the folder structure is committed but contents are not:

```
# data/.gitignore (and cache/.gitignore)
*
!.gitignore
!README.md
```

The top-level `.gitignore` should additionally cover `out/`, `*.pyc`, `__pycache__/`, virtualenv folders, etc.

---

## 6. Configuration

**Format:** YAML. **Validation:** Pydantic models. Fail fast and loudly on schema or reference errors.

### 6.1 `config.example.yaml`

```yaml
# Census augmentation configuration

input:
  path: data/locations.csv
  address_column: address          # Optional. If present, used for geocoding.
  latitude_column: lat             # Optional. If both lat & lon present, used directly.
  longitude_column: lon            # Both required together if used.

output:
  path: out/locations_enriched.csv
  prefix: sa2_                     # Prefix for all census-derived columns

census:
  year: 2021                       # Reserved for future use (2026 Census)
  level: SA2                       # v1: SA2 only
  profile: GCP                     # General Community Profile
  region: AUS                      # AUS or state code (NSW, VIC, QLD, ...)
  descriptor: short-header         # short-header | sequential | long-header
  asgs_edition: 3                  # ASGS edition for boundaries (3 covers Jul 2021 – Jun 2026)
  datum: GDA2020                   # GDA2020 or GDA94 for boundary files

# Base URLs for ABS data downloads. Override only if ABS restructures their site.
# Full filenames are constructed deterministically from the census.* values above.
data_sources:
  boundaries_base_url: https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files
  datapacks_base_url: https://www.abs.gov.au/census/find-census-data/datapacks/download

geocoding:
  provider: nominatim
  user_agent: "census-augment/0.1 (you@example.com)"   # Required by Nominatim policy
  rate_limit_per_second: 1
  cache_enabled: true

# Variables to attach to each record.
# Format: <friendly_name>: <table_id>.<column_name>
# Output column will be: <output.prefix><friendly_name>
# Friendly names must be valid snake_case identifiers.
variables:
  median_age: G02.Median_age_persons
  median_household_income_weekly: G02.Median_tot_hhd_inc_weekly
  median_rent_weekly: G02.Median_rent_weekly
  total_population: G01.Tot_P_P
  born_overseas_count: G01.Birthplace_Elsewhere_P
```

### 6.2 Variable resolution

- Each entry under `variables` maps a friendly name to a `<table>.<column>` reference into the DataPack.
- At config-load time, the tool validates every reference against the loaded DataPack metadata. Unknown table or column → fail with a helpful message that suggests near-matches.
- Friendly names must match `^[a-z][a-z0-9_]*$`. The output column is `{prefix}{friendly_name}`.
- A `discover` CLI command lets users search metadata to find the right `table.column` references:
  ```
  census-augment discover --search "income"
  census-augment discover --table G02
  ```

---

## 7. Pipeline Stages

### 7.1 Input parsing
- Load the CSV with pandas.
- Validate that referenced columns exist.
- Determine per-row source: `coordinates` (if both lat/lon present and non-null) takes precedence over `address`.

### 7.2 Geocoding
- For each address-only row, check the geocoding cache first.
- Cache key: SHA-256 hash of the normalized address (lowercase, whitespace-collapsed, trailing punctuation stripped).
- Cache layout: `cache/geocoding/{hash[:2]}/{hash}.json` (sharded so directories stay small).
- Cache value:
  ```json
  {
    "address_input": "...",
    "address_normalized": "...",
    "lat": -33.8688,
    "lon": 151.2093,
    "provider": "nominatim",
    "timestamp": "2026-04-30T...",
    "raw_response": { ... }
  }
  ```
- Respect Nominatim's rate limit (1 req/sec) and User-Agent policy.
- Failed lookups: record retains `null` coordinates and is flagged in the run summary; pipeline continues.
- **Duplicate addresses are not explicitly deduplicated** before geocoding. Within a single run, a duplicate address will hit the cache as soon as the first occurrence is processed and written. This keeps row-level processing predictable and avoids reordering output, at the cost of one extra cache lookup per duplicate (negligible).

### 7.3 Spatial join
- Load SA2 GeoPackage into a GeoDataFrame.
- Build a spatial index (`sindex`).
- Reproject points to match the boundary CRS.
- Point-in-polygon for each record. Records outside any SA2 get `null` `sa2_code` and `sa2_name`.

### 7.4 Census enrichment
- Determine the unique set of `(table)` references across all configured variables.
- Load only those tables from the DataPack.
- Build a single lookup keyed by SA2 code.
- Join enriched columns onto the dataset.

### 7.5 Output
- Write CSV with all original columns preserved, in original order, followed by appended columns (see §8).
- Print a run summary: total rows, geocoded (cache hit / fresh), unmatched SA2, fully enriched, errors.

---

## 8. Output Schema

Original input columns are preserved unchanged. The following columns are appended in this order:

| Column | Description |
|---|---|
| `geo_lat` | Resolved latitude (from input or geocoding). |
| `geo_lon` | Resolved longitude. |
| `geo_source` | One of `input`, `cache`, `fresh`, `failed`. |
| `sa2_code` | 9-digit SA2 code from ASGS. `null` if no match. |
| `sa2_name` | SA2 human-readable name. |
| `sa2_<friendly_name>` | One column per configured variable, with the configured prefix. |

Example header for a config with two variables:

```
address, lat, lon, geo_lat, geo_lon, geo_source, sa2_code, sa2_name,
sa2_median_age, sa2_median_household_income_weekly
```

---

## 9. Caching Strategy

| Cache | Location | Format | Invalidation |
|---|---|---|---|
| Geocoded addresses | `cache/geocoding/` | JSON per address (sharded) | Manual delete; key is hash of normalized address |
| ASGS boundaries | `data/boundaries/` | GeoPackage | Re-download with `census-augment fetch --boundaries --refresh` |
| Census DataPacks | `data/census/` | Extracted CSVs + metadata | Re-download with `census-augment fetch --census --refresh` |

---

## 10. Error Handling

| Condition | Behavior |
|---|---|
| Address fails to geocode | Warn; row keeps null coords; flagged in summary; pipeline continues. |
| Coordinates fall outside Australia / no SA2 match | Warn; row keeps null SA2; flagged in summary; pipeline continues. |
| Some configured variables missing or suppressed for a matched SA2 | Leave those cells as null; flag the row in the summary as "partially enriched"; pipeline continues. |
| Variable reference not found in metadata | **Fail fast at config load.** Suggest near-matches. |
| Required input column missing | **Fail fast at startup.** |
| Network failure during data download | Retry with exponential backoff (3 attempts); then abort with clear message. |
| Required `user_agent` missing for Nominatim | **Fail fast at config load.** |

A summary report is printed at the end of every run.

---

## 11. CLI

Using Typer (preferred) or Click. Proposed commands:

```
census-augment run --config config.yaml
census-augment discover --search "income"     # Find variables by keyword
census-augment discover --table G02           # List all columns in a table
census-augment fetch --boundaries             # Pre-fetch boundaries
census-augment fetch --census                 # Pre-fetch DataPacks
census-augment fetch --boundaries --census --refresh  # Force re-download
census-augment validate --config config.yaml  # Dry-run config validation
```

---

## 12. Dependencies

Suggested (Claude Code may adjust):

- `geopandas` — spatial join
- `shapely` — geometry primitives
- `pyproj` — CRS transforms
- `pandas` — tabular data
- `pydantic` — config validation
- `pyyaml` — config parsing
- `typer` (or `click`) — CLI
- `requests` — HTTP downloads
- `tqdm` — progress bars
- `openpyxl` — read DataPack metadata Excel file

Test deps: `pytest`, `pytest-mock`, `responses` (HTTP mocking).

---

## 13. Extensibility Hooks

The architecture should make these additions cheap, without restructuring:

- **New geocoder** (G-NAF, Google, Mapbox): implement the `Geocoder` interface in `geocoding/base.py`; register by name in config.
- **New SA level** (SA1, SA3): boundary loader and spatial join already key on SA code. Add boundary file resolution and update level enum.
- **New census year** (2026): data source loader takes year as a parameter; variable catalog is year-scoped.
- **New DataPack profile** (Indigenous, Working Population): DataPack downloader takes profile as a parameter; metadata index is profile-scoped.
- **New input/output formats**: parser and writer modules behind a small format interface.

---

## 14. Resolved Decisions

These were open questions in v0.1, resolved in v0.2:

1. **Address deduplication.** *Decision: Do not explicitly deduplicate.* Rely on the geocoding cache for efficiency. Avoids reordering output rows and keeps row-level processing predictable. Documented in §7.2.
2. **DataPack download URL stability.** *Decision: Ship with known-good base URLs and construct filenames deterministically.* Two configurable base URLs in `data_sources` (one for boundaries, one for DataPacks). Filenames built from `census.*` config values. Documented in §4 and §6.1.
3. **Computed variables.** *Decision: Out of scope for this tool entirely.* This tool's job is raw census variable attachment; ratios, percentages, and other derived metrics are the responsibility of the downstream feature engineering pipeline. Documented in §2.
4. **Partial enrichment policy.** *Decision: Leave missing/suppressed cells as null and flag the row as "partially enriched" in the run summary.* No row is dropped due to missing census values. Documented in §10.
5. **Boundary version pinning.** *Decision: Add explicit `census.asgs_edition` and `census.datum` config fields with sensible defaults (3 / GDA2020).* Forces deliberate handling when a new ASGS edition is released. Documented in §6.1.
6. **Target scale and geocoder choice.** *Decision: design for a few hundred rows per run.* At this scale Nominatim's 1 req/sec policy is acceptable (~5 minutes of geocoding for 300 fresh addresses; cache hits on re-runs are instant). Larger scales are deferred to the pluggable geocoder hook in §13 (G-NAF, paid providers). Documented in §2.

## 15. Open Questions

*(None at present. Add new ones here as they arise during implementation.)*

---

## 16. Acceptance Criteria for v1

The implementation is considered done when:

- A user can run `census-augment run --config config.example.yaml` end-to-end on a sample input of mixed addresses + coordinates and produce an enriched CSV.
- All boundary and DataPack files are downloaded automatically on first run.
- Geocoding cache is populated and reused on the second run (verifiable by faster runtime).
- `census-augment discover --search "income"` returns matching census variables.
- Config errors (bad variable references, missing input columns) produce clear, actionable error messages.
- Test suite covers: config validation, cache hit/miss, spatial join correctness on a small fixture, end-to-end pipeline on a tiny dataset.
